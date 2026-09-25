"""Fallen -> home recovery foundation.

Provides full-minimap position sensing (sees the lower platforms the bot's narrow
band crop misses) in the SAME coordinate frame as the existing calibration
(offset 20,171 -> so ROPE_MINIMAP_X=91, PLATFORM_Y=101 stay valid).

CLI:
  python core.py runnav                     # full production farming run ('n' start/pause)
  python core.py nav [secs]                 # bounded runnav test (default 90s)
  python core.py focus                      # bring game to foreground (verify)
  (run with no/unknown cmd to print the full command list)
"""
import sys, time, ctypes
# Run as a script (`python core.py <cmd>`), this module is `__main__`. The extracted modules
# (exp_tracking, safety, capture, ...) `import core`, which would otherwise load core.py a SECOND
# time and hit a circular import at the re-exports below. Alias ourselves as `core` first so they
# get THIS module, exactly as when core is imported normally.
if __name__ == "__main__":
    sys.modules.setdefault("core", sys.modules["__main__"])
from ctypes import wintypes
import cv2
import numpy as np
from PIL import Image
import mss, pygetwindow as gw
from screeninfo import get_monitors
from detection import (detect_character_on_minimap, detect_red_dots,
                       detect_red_dots_color, detect_lie_check)
from lie_check_yolo import detect_lie_check_yolo
from alarm import Alarm, AlertController
import notify
import os
from glob import glob as _glob
from exp_processor import ExpProcessor
import pyautogui
from pynput.keyboard import Key, Listener
import random
import threading
import keyboard as kb   # safe_press/release/release_all + 'n' pause listener
import navmap
import watermap

# --- recovery map constants (calibrated 2026-08-09, full-minimap frame) ---
ROPE_X = 91              # rope minimap x (climbs to farming platform)
BOTTOM_ROPE_X = 95
FARMING_Y_MAX = 95       # y <= this (and x in patrol range) == on farming platform
ON_PLATFORM_Y = 88       # y <= this == STANDING on the top platform (settles ~85); the rope
                         # top reads ~90-95, so this distinguishes "mounted" from "still hanging"
FALLEN_Y_MIN = 96        # y >= this == below the farming platform (fallen reads ~99-110,
                         # farming reads ~76-90; boundary ~95 with no dead-zone)
PATROL_X_L, PATROL_X_R = 66, 161
# The rope only reaches the fallen platforms directly beside it. Outside this box she
# is somewhere the rope-climb can't fix (deeper/other platform) -> DON'T flail; stop/panic.
RECOVER_X_MIN, RECOVER_X_MAX = 55, 172   # right-side drop ledge reads to ~x155 (live median
                                         # (155,131)); was 152 -> rejected it as unrecoverable
RECOVER_Y_MAX = 156      # covers rest (~110), bottom farming (~143), AND the close LOWER
                         # bottom ledge (~150) she sometimes drops to; deep falls (200+) still
                         # rejected. Rope connects the levels, so climbing up reaches the top
ROPE_EXIT_TOP_Y = 92     # climb until y<=this before the up-jump (live-proven)
ROPE_CLIMB_MAX = 12.0    # bottom->top spans the pinned scroll-zone; give one call room to finish
# --- rope climb (bottom -> top). The minimap SCROLLS to keep the character centered, so in
# the map's middle the dot is PINNED at ~(ROPE_X,136) while the terrain scrolls. Climb
# PROGRESS is therefore read from the terrain scroll (minimap_scroll), not the pinned dot;
# success is the dot resolving onto the top platform (y<=TOP_EXIT_Y) in the top clamp zone. ---
TOP_EXIT_Y = ROPE_EXIT_TOP_Y   # 92; climb until y<=this, then up-jump onto the top platform
SCROLL_RISE_PX = 3.0     # net upward terrain-scroll (px) that counts as real climb progress
CLIMB_STALL_S = 0.7      # s with no net rise in one grab -> release, RE-ALIGN, and re-grab
                         # (each rope->rope gap: realign to the column before the next jump)
MAX_GRABS = 7            # align+jump+climb attempts per climb (bottom->mid->top needs ~2-3)
JUMP = Key.alt_l
FALL_ABORT_DY = 18       # if y jumps this much more than expected mid-walk -> abort
DROP_X = 67              # narrow drop-through gap: down-jump here drops straight down to
                         # the left fallen platform (x=64 and x=76 are solid; verified by trail)
RIGHT_EDGE_SAFETY = 128  # if detected at/beyond this x, force a left-return NOW
                         # (right edge/gap is ~x140; keeps a safe margin)
TOP_HOME_X = 74          # top farming home (park-left spot); recovery ends here facing right
BOTTOM_Y_MIN = 140       # y >= this == start the climb's BOTTOM grab (vs the mid ledge ~136);
                         # kept high so the mid ledge grabs the upper column, not the base
ON_BOTTOM_Y = 133        # y >= this == DETECTED on the bottom platform (settles ~143 but bobs
                         # to ~133 when dragons jostle her); tolerant so detection doesn't flap
BOTTOM_HOME_X, BOTTOM_FAR_X = 63, 90   # bottom park-left home & conservative sweep-right
BOTTOM_FALL_Y = 160      # y >= this == fell well BELOW the bottom (the ~150 lower ledge is
                         # still tolerated/recoverable, not treated as a fatal fall)
LOWER_LEDGE_Y = 147      # y >= this == on the LOWER ledge; the rope doesn't reach it, so
                         # recovery must hop UP to the bottom platform (~143) first

TITLE = "MapleStory Worlds-Artale (?????)"
SP = r"C:\Users\jerry\AppData\Local\Temp\claude\C--jerry-toy-work-maple\76b73c35-1984-400a-a0fc-417f80626173\scratchpad"

# minimap map-area, same offset the bot uses; taller so fallen positions are visible
MM_X, MM_Y, MM_W, MM_H = 20, 171, 229, 259   # band was H=145; 259 reaches lower platforms
CHAR_TPL = "assets/minimap_character/"

# Valid map area for the character dot (minimap-crop coords). The character is
# always on a platform: patrol x66..161, recover x55..152, y ~76 (top) .. ~156
# (lower ledge). Heavy buff-glow spawns phantom yellow blobs at the minimap
# CORNERS/EDGES -- documented at (22,232),(2,242),(36,153),(28,235),(122,188) --
# all outside this box. Used to prefer the real dot over glow phantoms.
MAP_X_MIN, MAP_X_MAX = 60, 170
MAP_Y_MIN, MAP_Y_MAX = 65, 175


_sct_tls = threading.local()


# --- lie-check alarm: beep when a "needs a human" screen appears (captcha / curse) ---
# Two independent pipelines (each its own alarm, so cadences never fight):
#   FAST: transparent-shape only -- ~40ms, so it can run INSIDE the 6-8s STAND_SHOOT
#         and fire immediately. That screen gives ~3s, so latency must be ~1s.
#   FULL: curse + monster -- heavier (wide banner), runs only at the loop top; those
#         screens persist far longer so a slower, debounced check is fine.
_LIE_DIR = "assets/lie_check/"
_FAST_TEMPLATES = ["transparent_title.png"]     # 3s window -> checked in-state, immediate
# monster_instr.png = old transparent-overlay popup; monster_instr_box.png = new opaque-box
# popup art (the two renderings don't cross-match, so keep both to cover either style).
# monster_warn_box.png = the red warning line on the new popup -- a 2nd independent OR signal,
# so a single degraded template can't cause a miss (detect_lie_check fires if ANY hits).
# The popup text is semi-transparent, so it doesn't match across map backgrounds -- each map
# needs its own instr+warn pair. *_box = underwater (bright); *_temple = temple (dark).
_FULL_TEMPLATES = ["curse_banner.png", "curse_lock.png", "monster_instr.png",
                   "monster_instr_box.png", "monster_warn_box.png",
                   "monster_instr_temple.png", "monster_warn_temple.png"]
_lie_enabled = bool(_glob(os.path.join(_LIE_DIR, "*.png")))

_fast_alarm = Alarm(freq=1000, beep_ms=350, gap_ms=150)
_fast_alert = AlertController(_fast_alarm, trigger_consecutive=1, clear_consecutive=2)  # immediate
_full_alarm = Alarm(freq=1000, beep_ms=350, gap_ms=150)
_full_alert = AlertController(_full_alarm, trigger_consecutive=2, clear_consecutive=1)
_last_fast_tick = [0.0]
_last_full_tick = [0.0]
if not _lie_enabled:
    print(f"[lie-check] '{_LIE_DIR}' 沒有模板，警報停用。")
if notify.notifier().enabled:
    print("[notify] Discord webhook 已啟用 (lie-check / another-player -> Discord)")

# Full frames dumped here for OFFLINE recall tuning (kept out of assets/, which holds templates).
_LIE_CAP_DIR = "datasets/lie_check/captures"


# Near-miss telemetry: the full check only auto-dumps on a HIT, so a MISSED curse/monster screen
# (the exact failure we're chasing) leaves no trace. When a template lands in [floor, threshold)
# -- close but not firing -- dump the frame so we can see WHY it missed (degraded live text vs a
# runtime problem) and feed it to the planned YOLO detector. Throttled so a normal frame that
# grazes the floor can't spam. Floor is above the ~0.72 normal-frame ceiling so dumps are rare.
_NEAR_MISS_FLOOR = 0.72
_NEAR_MISS_INTERVAL = 30.0
_last_near_miss_dump = [0.0]


# --- background safety monitor: the FULL check (curse/monster) is ~0.5s of template matching,
# so running it inline froze the action loop for that long every 1.5s. Run it in its OWN thread
# instead -- curse/monster screens persist for many seconds, so a steady side-thread cadence
# loses no recall while the action loop stays responsive. The cheap FAST tick (transparent
# captcha + another-player, ~55ms, 3s window) stays INLINE in the action loops, where it's
# guaranteed to run every iteration. Enabled by the thread-local mss handle (each thread grabs
# with its own). Skips while paused (a human is present -> lie_check_silence handles it) so it
# can't re-arm an alarm the action loop just silenced. ---
_safety_stop = threading.Event()
_safety_thread = [None]


# --- another-player alarm: a DISTINCT lower tone, sounds until 'm' acknowledges it ---
_enemy_alarm = Alarm(freq=700, beep_ms=250, gap_ms=120)


# --- auto-escape: if another player stays and the alarm is left un-acknowledged, click the
# 自由市場 (Free Market) button to leave the map. Opt-in per map (free_market_escape). The button
# coords are its center in the 1942x1136 capture FRAME; the live screen point is _window_box()
# + this offset (recomputed each time), so it survives the window being moved. ---
_FM_ESCAPE = [False]           # enabled for the current run?
_FM_AFTER = [20.5]            # seconds the enemy alarm may sound UN-acked before we click
_FM_XY = [(1452, 1092)]      # 自由市場 button center in the capture frame (deep_sea_2, 1942x1136)
_enemy_alarm_since = [None]   # when the enemy alarm began sounding continuously (None = quiet)
_fm_clicked = [False]         # clicked this episode already -> don't re-click until it's silenced


# --- shared status + cooperative stop, for the control UI (panel.py) --------------
# STATUS is a plain dict updated in-place by the loops; the UI polls it. STOP is a
# cooperative stop the UI sets to end a background-thread run ('n' pause still works).
STATUS = {"state": "idle", "node": None, "count": None, "lie": False,
          "exp_per_min": None, "exp_10min": None, "exp_total": 0,
          "run_secs": 0, "run_active_base": None, "run_active_since": None, "buff_at": None,
          "exp_started_at": None}   # wall-clock start of an exp_record run (None = not recording)
STOP = threading.Event()
_LAST_WATER_ATTACK_KEY = [None]   # test/inspection hook: attack_key after character merge


# --- DEBUG log: fine-grained per-frame traces written to a FILE only (never console/panel),
# so the operational Log stays clean while a full trace is captured for post-run debugging.
_DBG = [None]


def open_debug_log(logdir="logs"):
    """Open logs/debug_<ts>.log for the fine-grained trace. Returns the path (or None)."""
    import os
    close_debug_log()                                      # close a prior run's handle first
    try:
        os.makedirs(logdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        path = os.path.join(logdir, f"debug_{stamp}.log")
        _DBG[0] = open(path, "a", buffering=1, encoding="utf-8")   # line-buffered
        return path
    except Exception:
        _DBG[0] = None
        return None


def close_debug_log():
    if _DBG[0] is not None:
        try:
            _DBG[0].close()
        except Exception:
            pass
        _DBG[0] = None


def dbg_on():
    return _DBG[0] is not None


def dbg(msg):
    """Append a timestamped DEBUG line to the debug file (no-op if not open). File-only."""
    f = _DBG[0]
    if f is None:
        return
    try:
        t = time.time()
        ts = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"
        f.write(f"{ts} {msg}\n")
        f.flush()                                          # survive a mid-run shutdown
    except Exception:
        pass


def watch_loop(sleep=time.sleep):
    """Passive 'quick easy loop': run the lie-check ticks (no movement) until STOP is
    set, keeping STATUS['lie'] current. The zero-risk always-on human-check monitor."""
    STOP.clear()
    STATUS["state"] = "watching"
    try:
        while not STOP.is_set():
            lie_check_tick()
            STATUS["lie"] = is_lie_check_active()
            sleep(0.5)
    finally:
        lie_check_silence()
        STATUS["lie"] = False
        STATUS["state"] = "idle"


def exp_record_loop(label="human", sleep=time.sleep):
    """Passive EXP recorder -- presses NO movement keys. Polls the EXP bar via make_exp_tracker
    (updating STATUS['exp_per_min'/'exp_10min'/'exp_total']) plus the lie-check safety scan,
    until STOP. On stop it appends ONE session row (start/duration/gain/rate/label) to
    logs/exp_sessions.jsonl, so a HUMAN farm (started here, farmed by hand) yields a reference
    efficiency to compare against bot runs. exp_gained comes from the tracker's anomaly-filtered
    cumulative total; exp_per_min is computed from gained/duration so even a short session
    summarizes; both are null when the tracker never produced a value (OCR failed)."""
    STOP.clear()
    STATUS["state"] = "exp_record"
    exp_tick = make_exp_tracker(label="record", sample_secs=15, min_run_secs=30)
    t0 = time.time()
    STATUS["exp_started_at"] = t0                       # UI reads this to show a live timer
    print(f"[exp] recording started (label={label}) -- farm by hand; press Stop when done.")
    try:
        while not STOP.is_set():
            lie_check_tick()
            exp_tick()
            STATUS["lie"] = is_lie_check_active()
            sleep(0.5)
    finally:
        dur = int(time.time() - t0)
        gained = STATUS.get("exp_total") or None       # 0/None (OCR failed) -> null, not a fake 0
        print(f"[exp] recording stopped -- {dur}s, gained {gained if gained is not None else 'n/a (no OCR)'}")
        row = {
            "ts_start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
            "ts_end": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "duration_s": dur,
            "exp_gained": gained,
            "exp_per_min": round(gained / (dur / 60.0)) if (gained and dur >= 1) else None,
            "exp_10min": STATUS.get("exp_10min"),
            "label": label,
        }
        append_exp_session(row)
        lie_check_silence()
        STATUS["lie"] = False
        STATUS["state"] = "idle"
        STATUS["exp_started_at"] = None


# --- game-logic safety, ported faithfully from the notebook (screen reads, no keys) ---
ENEMY_THRESHOLD = 0.75      # red-dot match threshold; per-map via set_enemy_threshold(cfg)


# --- enemy-dot shadow rollout: the TEMPLATE detector stays authoritative (drives the alarm);
# the new HSV color detector (detect_red_dots_color) runs alongside in SHADOW so we can prove it
# matches the template on REAL frames before switching. On any activity we log presence agreement
# and dump the FULL frame (throttled) so the rare other-player frames -- and any color false-alarm
# or miss -- accumulate for offline double-checking. The shadow never changes the alarm decision
# and never raises into the safety loop. ---
_ENEMY_SAMPLE_DIR = "datasets/enemy_dots/samples"      # full frames dumped here for offline review
_ENEMY_SAMPLE_DUMPS = False    # opt-in: set True to collect template-vs-color review frames again
_ENEMY_SHADOW_LOG = "logs/enemy_dots_shadow.jsonl"     # one line per template/color activity
_ENEMY_AGREE_DUMP_INTERVAL = 30.0    # both agree (player present or both quiet-with-one-firing): rarer dumps
_ENEMY_DISAGREE_DUMP_INTERVAL = 10.0 # color-only or template-only: the interesting cases, dumped more often
_enemy_shadow_state = {"agree": 0.0, "disagree": 0.0}  # last-dump wall-clock per category (throttle)


def make_active_timer():
    """ACTIVE farming-time tracker (excludes pauses), shared by the water and nav loops.
    Returns (tick, freeze): call tick(now) each iteration WHILE farming to advance the
    timer, and freeze(now) on pause/stop to end the current stretch. Publishes
    STATUS['run_active_base'] / ['run_active_since'] (so the panel can extend the timer
    live) and STATUS['run_secs']. base = sum of finished stretches, since = current start."""
    _active = {"base": 0.0, "since": None}
    STATUS["run_secs"] = 0
    STATUS["run_active_base"], STATUS["run_active_since"] = 0.0, None

    def freeze(now):                                     # end the current active stretch
        if _active["since"] is not None:
            _active["base"] += now - _active["since"]; _active["since"] = None
        STATUS["run_active_base"], STATUS["run_active_since"] = _active["base"], None
        STATUS["run_secs"] = int(_active["base"])

    def tick(now):                                       # (re)enter active farming and advance
        if _active["since"] is None:
            _active["since"] = now
        STATUS["run_active_base"], STATUS["run_active_since"] = _active["base"], _active["since"]
        STATUS["run_secs"] = int(_active["base"] + (now - _active["since"]))

    return tick, freeze


_SWIM_MAX_JUMP = 70   # px: a read leaping more than this from the last known pos is a phantom
                      # (a spurious yellow minimap blob), not real motion -- she can't cross that
                      # much of the ~200px-wide minimap between two ~40ms reads.


def is_farming(x, y):
    return 0 <= y <= FARMING_Y_MAX and PATROL_X_L - 6 <= x <= PATROL_X_R + 6


def walk_to_x(target_x, tol=2, timeout=7.0, coarse=5):
    """Walk to target_x with CONTINUOUS motion (holds the key while polling position),
    then eases in with a couple of tiny taps near the target -- much more human-like
    than tap-sense-tap. Aborts (releases) if the character actually starts falling."""
    x, y = get_character_color()
    if x < 0:
        x, y = stable_char(3)
    if x < 0:
        return False
    if abs(x - target_x) <= tol:
        return True
    base_y = y
    t0 = time.time()

    # phase 1: hold the direction key and walk continuously until near the target
    if abs(x - target_x) > coarse:
        going_right = x < target_x
        key = Key.right if going_right else Key.left
        kb.safe_press(key)
        try:
            while time.time() - t0 < timeout:
                if kb.pause:
                    return False
                lie_check_fast_tick()      # transparent-shape check while walking (3s window)
                x, y = get_character_color(near=(x, y))
                if x < 0:
                    time.sleep(0.03); continue
                if y - base_y > FALL_ABORT_DY:        # maybe falling -- confirm stably
                    kb.safe_release(key)
                    xs, ys = stable_char(3)
                    if ys >= 0 and ys - base_y > FALL_ABORT_DY:
                        print(f"[recover] y {base_y}->{ys} (stable) while walking -> ABORT (fall)")
                        return False
                    base_y = ys if ys >= 0 else base_y  # false alarm -> resume walking
                    kb.safe_press(key); continue
                if (going_right and x >= target_x - coarse) or \
                   (not going_right and x <= target_x + coarse):
                    break
                time.sleep(0.03)
        finally:
            kb.safe_release(key)

    # phase 2: ease in with small taps (humans slow down as they approach)
    for _ in range(10):
        if kb.pause:
            return False
        x, y = get_character_color(near=(x, y))
        if x < 0:
            x, y = stable_char(3)
            if x < 0:
                break
        if abs(x - target_x) <= tol:
            return True
        key = Key.right if x < target_x else Key.left
        kb.safe_press(key); time.sleep(0.045); kb.safe_release(key)
        time.sleep(0.05)
    x, y = get_character_color()
    return x >= 0 and abs(x - target_x) <= tol + 2


def _climb_to_top(grab_x, hop, cap=ROPE_CLIMB_MAX, bridge_x=None):
    """Climb to the TOP farming platform as a sequence of ALIGN -> jump-grab -> climb attempts.
    The minimap pins the dot (~ROPE_X,136) through the map's middle, so climb PROGRESS is read
    from the terrain SCROLL (minimap_scroll) -- net upward scroll == she is rising -- plus the
    dot dropping once it resolves in the top clamp. Within a grab she holds Up while she keeps
    rising; when she stalls at a rope->rope gap (CLIMB_STALL_S with no net rise) she RELEASES,
    walks back to the rope column, and re-grabs -- so every jump happens ALIGNED, not flailing
    mid-air. Success == the dot resolves onto the top platform (y<=TOP_EXIT_Y). Returns False
    after MAX_GRABS attempts or the cap (so the caller retries)."""
    if bridge_x is None:
        bridge_x = ROPE_X                                # the upper ropes sit on the climb column
    t0 = time.time()
    best_y = 999                                          # closest-to-top y reached (diag)
    for attempt in range(MAX_GRABS):
        if kb.pause or time.time() - t0 >= cap:
            break
        align_x = grab_x if attempt == 0 else bridge_x   # first grab at the base; bridges realign
        if not walk_to_x(align_x, tol=1):
            cx, _cy = get_character_full()
            # walk_to_x can't move her while she's HANGING on the rope -- that's fine if she's
            # already on the column (just hold Up); only bail if she's genuinely off to the side.
            if not (cx >= 0 and abs(cx - align_x) <= 5):
                kb.safe_release_all(); return False
        kb.safe_press(Key.up)
        if attempt > 0 or hop:                           # jump-grab the rope (first grab honors hop)
            kb.safe_press(JUMP); time.sleep(0.08); kb.safe_release(JUMP)
        prev_crop, scroll_acc, last_y, last_progress, stalled = None, 0.0, None, time.time(), False
        while time.time() - t0 < cap:
            if kb.pause:
                break
            lie_check_fast_tick()      # transparent-shape check while climbing (3s window)
            img = capture()
            if img is None:
                time.sleep(0.05); continue
            x, y = get_character_full(img)
            if 0 <= y < best_y:
                best_y = y
            crop = _minimap(img)
            if 0 <= y <= FARMING_Y_MAX:                   # on the top platform (matches is_farming;
                kb.safe_release(Key.up); return True       # TOP_EXIT_Y=92 was stricter -> wasted a round)
            rose_dot = (last_y is not None and 0 <= y < last_y - 1)   # dot dropped (clamp zone)
            if prev_crop is not None:
                dy, _resp = minimap_scroll(prev_crop, crop)
                scroll_acc += dy                         # net signed rise since last progress
            prev_crop = crop
            if y >= 0:
                last_y = y
            if rose_dot or scroll_acc >= SCROLL_RISE_PX:  # real upward progress (dot or scroll)
                last_progress = time.time(); scroll_acc = 0.0
            if time.time() - last_progress > CLIMB_STALL_S:
                stalled = True; break                     # stuck at a gap -> release, realign, regrab
            time.sleep(0.08)
        kb.safe_release(Key.up)
        if not stalled:                                   # ended by cap/pause, not a gap stall
            break
        time.sleep(0.2)                                   # settle on the ledge before realigning
    reason = "cap" if time.time() - t0 >= cap else ("pause" if kb.pause else "max_grabs")
    print(f"[climb] gave up: reason={reason} grabs={attempt + 1} t={time.time() - t0:.1f}s best_y={best_y}")
    return False


def climb_and_jump():
    """Climb to the top farming platform, then up-jump onto it. Progress is tracked by the
    minimap terrain scroll (the dot is pinned mid-map -- see _climb_to_top). Returns True only
    if she reached the top; on a miss releases keys and returns False so the caller
    (recover_to_farming) re-localizes and retries."""
    _x0, y0 = get_character_full()
    # the bottom rope BASE sits at ~BOTTOM_ROPE_X (x95); the minimap only reads the climb
    # column (~ROPE_X x90) once she is ON the rope. Align the bottom grab to the BASE.
    grab_x = BOTTOM_ROPE_X if y0 >= BOTTOM_Y_MIN else ROPE_X
    # ALWAYS hop-grab: the rope base is above the floor from the bottom, and from the mid
    # ledge (y~136) she must JUMP onto the rope too -- a plain Up never catches it there.
    if not _climb_to_top(grab_x, hop=True):
        kb.safe_release_all(); return False
    # MOUNT the platform: from the chain top (y~90-95) an up-jump carries her onto the top
    # platform (y~85). VERIFY she's actually standing on it (y<=ON_PLATFORM_Y) -- a plain
    # y<=FARMING_Y_MAX check would pass while she's still hanging at the rope top. Retry the
    # up-jump a few times; only report success once she's truly mounted.
    for _ in range(3):
        if kb.pause:
            break
        kb.safe_press(Key.up); kb.safe_press(JUMP); time.sleep(0.15)
        kb.safe_release(JUMP); kb.safe_release(Key.up); time.sleep(0.3)
        _mx, my = stable_char(2)
        if 0 <= my <= ON_PLATFORM_Y:
            kb.safe_release_all(); time.sleep(0.1); return True
    kb.safe_release_all()
    return False                                          # never mounted -> caller retries


def drop_to_fallen(drop_x=DROP_X):
    """Break move: from the LEFT of the farming platform, down-jump onto the (left)
    fallen platform -- a safe idle spot away from the dragon spawn. Verifies the
    drop via the minimap; bails (keys released) if not currently on the farming
    platform or if the drop didn't register."""
    x, y = stable_char()
    print(f"[drop] at ({x},{y})")
    if not is_farming(x, y):
        print("[drop] not on farming platform -> bail")
        kb.safe_release_all(); return False
    if not walk_to_x(drop_x):
        print("[drop] couldn't reach drop x -> bail")
        kb.safe_release_all(); return False
    # down-jump ONE level: release Down right after the jump so landing on the one-way
    # rest platform isn't read as a second down-jump (which cascades her to the bottom).
    kb.safe_press(Key.down)
    time.sleep(0.05)
    kb.safe_press(JUMP)
    time.sleep(0.05)
    kb.safe_release(JUMP)
    kb.safe_release(Key.down)
    time.sleep(0.5)
    x2, y2 = stable_char()
    dropped = y2 >= FALLEN_Y_MIN
    print(f"[drop] after down-jump ({x2},{y2}) dropped={dropped}")
    kb.safe_release_all()
    return dropped


def _plausible_read(last, max_jump=45):
    """get_character_color near `last`, but reject implausible teleports (the golden
    skill-buff glow spawns a phantom blob in the minimap corner -> huge jump). Returns
    (x,y) if plausible, else None (caller keeps the last known position)."""
    x, y = get_character_color(near=last)
    if x < 0:
        return None
    if last is not None and (abs(x - last[0]) > max_jump or abs(y - last[1]) > max_jump):
        return None
    return (x, y)


def _shoot(n=1):
    for _ in range(n):
        if kb.pause:
            return
        kb.safe_press('c'); time.sleep(0.06); kb.safe_release('c'); time.sleep(0.06)


def _face_right(dur=0.1):
    """Brief right tap so she faces right (dragons are to the right) after a left return
    -- mirrors the origin loop's click_press(Key.right, 0.1). `dur` controls the tap
    length: a shorter tap still flips her facing but drifts her fewer px (used when
    parking at a left boundary, where the drift would push her off the home spot)."""
    if kb.pause:
        return
    kb.safe_press(Key.right); time.sleep(dur); kb.safe_release(Key.right)


def _walk_shoot(key, target_x, going_right, seed=None, cap=3.5, fall_y=FALLEN_Y_MIN, edge_safety=RIGHT_EDGE_SAFETY):
    """Hold a walk key and fire 'c' until reaching target_x (or a time cap). While going
    right, also stops hard at edge_safety. `fall_y` is the y that counts as falling off
    THIS platform. `seed` = last known position, so phantom buff-blobs are rejected from
    the first read. Returns False if she fell."""
    kb.safe_press(key)
    t0, last = time.time(), seed
    while time.time() - t0 < cap:
        if kb.pause:
            break
        if not focus_ok_or_bail("dragon"):     # focus lost mid-walk -> release + refocus, end sweep
            break
        lie_check_fast_tick()      # transparent-shape check while walk-shooting (3s window)
        r = _plausible_read(last)
        if r is not None:
            last = r
            x, y = r
            if y >= fall_y:
                kb.safe_release_all(); print(f"[farm] fell to ({x},{y}) -> stop"); return False
            if going_right and x >= edge_safety:
                break                                 # hard edge guard
            if (going_right and x >= target_x) or (not going_right and x <= target_x):
                break
        kb.safe_press('c'); time.sleep(0.06); kb.safe_release('c'); time.sleep(0.06)
    kb.safe_release(key)
    return True


def farm_bottom(seconds, x_home=BOTTOM_HOME_X, x_far=BOTTOM_FAR_X, stand=12.0):
    """Bottom-platform farm: park left (x~63) and shoot, periodic right->back-left
    sweep. Uses bottom y-context (fell only if y >= BOTTOM_FALL_Y, since here she
    normally sits at y~143). Dragons are to the right, same as the top."""
    _face_right()   
    print(f"[farm_bottom] {seconds}s")
    t0 = time.time()
    home = (x_home, 143)
    while time.time() - t0 < seconds:
        if kb.pause:
            break
        s = time.time()
        last = home
        while time.time() - s < stand and time.time() - t0 < seconds:
            if kb.pause:
                break
            # _shoot()
            kb.safe_press('c'); time.sleep(0.1)
            r = _plausible_read(last)                 # rejects buff-glow phantom reads
            if r is not None:
                last = r
                if r[1] >= BOTTOM_FALL_Y:
                    kb.safe_release_all(); print(f"[farm_bottom] fell to {r}"); return False
        kb.safe_release('c'); time.sleep(0.1)
        if kb.pause or time.time() - t0 >= seconds:
            break
        # sweep right (edge guard = x_far+6), then back left to home; bottom fall-threshold.
        # short cap: the bottom's left edge has no margin, so cap blind walking tightly.
        if not _walk_shoot(Key.right, x_far, going_right=True, seed=(x_home, 143), cap=2.2,
                           fall_y=BOTTOM_FALL_Y, edge_safety=x_far + 6):
            return False
        if not _walk_shoot(Key.left, x_home, going_right=False, seed=(x_far, 143), cap=2.2,
                           fall_y=BOTTOM_FALL_Y):
            return False
        _face_right()
    kb.safe_release_all()
    return True


def farm(seconds, x_home=74, x_far=112, stand=12.0):
    """Origin-style farm: stay parked at the LEFT (x_home) attacking in place most of
    the time; every ~`stand` seconds do one right->back-left sweep (attacking the whole
    time), returning to the left home. Position-bounded so she never walks off an edge."""
    print(f"[farm] farming {seconds}s (origin: hold left, periodic right->left)")
    t0 = time.time()
    while time.time() - t0 < seconds:
        if kb.pause:
            break
        # 1. stand at home and attack in place; bail early if knocked toward the edge
        s = time.time()
        drifted = False
        last = (x_home, 85)
        while time.time() - s < stand and time.time() - t0 < seconds:
            if kb.pause:
                break
            _shoot()
            r = _plausible_read(last)                 # rejects buff-glow phantom reads
            if r is not None:
                last = r
                if r[1] >= FALLEN_Y_MIN:
                    kb.safe_release_all(); print(f"[farm] fell to {r} -> stop"); return False
                if r[0] >= RIGHT_EDGE_SAFETY:          # too close to the right edge
                    print(f"[farm] near right edge (x={r[0]}) -> return left"); drifted = True; break
        if kb.pause or time.time() - t0 >= seconds:
            break
        if drifted:                                    # emergency left-return, face right
            if not _walk_shoot(Key.left, x_home, going_right=False, seed=(RIGHT_EDGE_SAFETY, 85)):
                return False
            _face_right()
            continue
        # 2. periodic sweep: walk right, back left to home, then face right
        if not _walk_shoot(Key.right, x_far, going_right=True, seed=(x_home, 85)):
            return False
        if not _walk_shoot(Key.left, x_home, going_right=False, seed=(x_far, 85)):
            return False
        _face_right()
    kb.safe_release_all()
    return True


def demo_sequence():
    """farm ~20s -> drop to fallen (stop cycle) -> recover -> farm ~10s -> drop to fallen."""
    if not focus():
        print("[demo] could not focus"); return
    x, y = stable_char(3)
    if not is_farming(x, y):
        print("[demo] not on farming platform -> recover first")
        recover_to_farming()
    print("=== FARM ~20s ===");            farm(20)
    print("=== STOP CYCLE: drop to fallen ==="); drop_to_fallen(); time.sleep(3)
    print("=== BACK TO FARMING ===");       recover_to_farming()
    print("=== FARM ~10s ===");            farm(10)
    print("=== BACK TO FALLEN (end) ===");  drop_to_fallen()
    print("final:", stable_char(3))


def _down_jump():
    """Down + tap Jump to fall through ONE level. Release Down immediately after the
    jump so she doesn't down-jump AGAIN when she lands on the (one-way) platform below
    -- that second down-jump is what made her cascade two levels (top -> bottom)."""
    kb.safe_press(Key.down); time.sleep(0.05)
    kb.safe_press(JUMP); time.sleep(0.05); kb.safe_release(JUMP)
    kb.safe_release(Key.down)                 # release Down BEFORE she lands = one level
    time.sleep(0.5)


def go_to_bottom():
    """Top farming -> bottom farming: down-jump to the rest platform, then down-jump
    again to the bottom. Verifies via minimap. Returns True if she's on the bottom."""
    x, y = stable_char(3)
    if not is_farming(x, y):
        print(f"[bottom] not on top farming ({x},{y}) -> abort"); kb.safe_release_all(); return False
    if not walk_to_x(DROP_X):                      # x=67 drop-through
        print("[bottom] couldn't reach drop x -> abort"); kb.safe_release_all(); return False
    _down_jump()                                   # top -> rest
    x, y = stable_char(3)
    print(f"[bottom] after 1st down-jump ({x},{y})")
    if y < FALLEN_Y_MIN:                           # didn't drop to the rest level
        print("[bottom] 1st down-jump didn't land on rest -> abort"); kb.safe_release_all(); return False
    _down_jump()                                   # rest -> bottom
    time.sleep(0.4)                                # let her settle before verifying
    x, y = stable_char(5)
    on_bottom = y >= ON_BOTTOM_Y
    print(f"[bottom] after 2nd down-jump ({x},{y}) on_bottom={on_bottom}")
    kb.safe_release_all()
    return on_bottom


def rest_to_bottom(drop_x=DROP_X):
    """Left fallen (rest) platform -> bottom farming platform: one down-jump. Verifies."""
    x, y = stable_char(3)
    if not (FALLEN_Y_MIN <= y < BOTTOM_Y_MIN):
        print(f"[rest->bottom] not on rest platform ({x},{y}) -> abort"); kb.safe_release_all(); return False
    if not walk_to_x(drop_x):
        print("[rest->bottom] couldn't reach drop x -> abort"); kb.safe_release_all(); return False
    _down_jump()
    time.sleep(0.4)
    x, y = stable_char(5)
    on_bottom = y >= ON_BOTTOM_Y
    print(f"[rest->bottom] after down-jump ({x},{y}) on_bottom={on_bottom}")
    kb.safe_release_all()
    return on_bottom


def _idle_on_fallen(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if kb.pause:
            break
        lie_check_fast_tick()      # transparent-shape check while resting on break (3s window)
        time.sleep(0.3)


def _nav_locate():
    x, y = stable_char(3)
    return navmap.classify_node(x, y)

# --- portal-bottom recovery chain (right side) -----------------------------------
# Two short rope hops lift the character from PORTAL_BOT up to the mid stretch, then
# recover_to_farming finishes (walks left to the central rope, scroll-tracked climb to
# top). These columns / y-targets are LIVE-TUNED in Task 3; the design rationale is in
# docs/superpowers/specs/2026-08-16-portal-rope-recovery-design.md.
PORTAL_ROPE_X = 132      # PORTAL_BOT -> LOWER_R climb column
RIGHT_ROPE_X  = 149      # LOWER_R -> mid-stretch climb column (live-tuned +2)
LOWER_R_Y_MAX = 151      # climbed onto LOWER_R once y <= this (its band y_hi)
MID_STRETCH_Y = 142      # risen off LOWER_R onto the mid stretch once y <= this


def climb_rope_hop(grab_x, land_y_max, dismount=None, land_node=None, cap=12.0):
    """Single, SHORT rope lift with RE-GRAB. Walk to grab_x, hop-grab, hold Up while rising
    to y <= land_y_max, then dismount. Like _climb_to_top's align->grab->climb loop, but a
    bounded hop that reads progress from the true dot-y (it stays BELOW the scroll-pin). If
    a monster knocks her off (no rise for CLIMB_STALL_S -> she's back on the platform),
    release, re-walk to the rope, and re-grab -- up to MAX_GRABS attempts within cap. If
    land_node is given, verify she settled on it. Returns True on success; on any miss
    releases keys and returns False so the caller degrades to panic. 'n' (kb.pause) aborts."""
    if not focus():
        return False
    t0, best, reached = time.time(), 999, False
    for attempt in range(MAX_GRABS):
        if kb.pause or time.time() - t0 >= cap:
            break
        if not walk_to_x(grab_x, tol=2):
            cx, _cy = get_character_full()
            if not (cx >= 0 and abs(cx - grab_x) <= 6):   # not on / beside the rope -> bail
                kb.safe_release_all(); return False
        kb.safe_press(Key.up); kb.safe_press(JUMP); time.sleep(0.08); kb.safe_release(JUMP)
        last_progress, prev_y, stalled = time.time(), None, False
        while time.time() - t0 < cap:
            if kb.pause:
                break
            lie_check_fast_tick()      # transparent-shape check while rope-hopping (3s window)
            x, y = get_character_full()
            if 0 <= y < best:
                best = y
            if 0 <= y <= land_y_max:
                reached = True; break
            if prev_y is not None and 0 <= y < prev_y - 0.5:   # still rising -> progress
                last_progress = time.time()
            if 0 <= y:
                prev_y = y
            if time.time() - last_progress > CLIMB_STALL_S:    # knocked off / stuck -> re-grab
                stalled = True; break
            time.sleep(0.06)
        kb.safe_release(Key.up)
        if reached or not stalled:
            break
        print(f"[hop] rope x{grab_x}: knocked off / stalled -> re-grab ({attempt + 1})")
        time.sleep(0.3)                                   # settle on the platform, then re-grab
    if not reached:
        print(f"[hop] rope x{grab_x}: rose to best_y={best}, target<= {land_y_max} -> miss")
        kb.safe_release_all(); return False
    # DISMOUNT: reaching the target y just means she's level with the ledge -- she is still
    # HANGING on the rope (the dot's x is still the rope column). Hop OFF toward the ledge
    # (direction + jump) and CONFIRM she left the rope column; retry a few times.
    off = (dismount not in ("left", "right"))             # None = handled by the caller (recover)
    if not off:
        key = Key.left if dismount == "left" else Key.right
        for _ in range(4):
            if kb.pause:
                break
            kb.safe_press(key); kb.safe_press(JUMP); time.sleep(0.10)
            kb.safe_release(JUMP); time.sleep(0.15); kb.safe_release(key)
            time.sleep(0.25)
            cx, _cy = get_character_full()
            if cx >= 0 and abs(cx - grab_x) >= 5:         # stepped off the rope column onto the ledge
                off = True; break
    time.sleep(0.2)
    x, y = stable_char(3)
    ok = off and (land_node is None or navmap.classify_node(x, y) == land_node)
    if ok:
        why = ("on " + land_node) if land_node else "off rope"
    elif not off:
        why = "still on rope x" + str(grab_x)
    else:
        why = "reached but NOT " + str(land_node)
    print(f"[hop] rope x{grab_x} -> ({x},{y}) {why}")
    kb.safe_release_all()
    return ok


def _right_rope_to_mid():
    """Climb the right rope up onto the mid stretch and dismount LEFT toward the central
    rope. Leaves her central-reachable for recover_to_farming."""
    return climb_rope_hop(RIGHT_ROPE_X, MID_STRETCH_Y, dismount="left", land_node=None)


def _lower_r_to_top():
    """LOWER_R -> TOP_FARM: right rope onto the mid stretch, then recover_to_farming."""
    if not _right_rope_to_mid():
        return False
    return recover_to_farming()


def recover_from_portal():
    """PORTAL_BOT -> TOP_FARM. Climb the portal rope up off PORTAL_BOT; if she's still on
    the isolated LOWER_R ledge, climb the right rope up onto the mid stretch; then
    recover_to_farming (walks left to the central rope, scroll-aware climb to top).
    Adaptive: a hop that overshoots straight onto the mid stretch just skips ahead."""
    if not climb_rope_hop(PORTAL_ROPE_X, LOWER_R_Y_MAX, dismount="right", land_node=None):
        return False
    x, y = stable_char(3)
    if navmap.classify_node(x, y) == "LOWER_R":           # still on the isolated ledge
        if not _right_rope_to_mid():
            return False
    return recover_to_farming()


# (src,dst) -> a callable that performs the move using the tuned primitives.
# Rope/up moves reuse recover_to_farming (it climbs any-below -> top). Downjumps
# reuse the proven composites.
EDGE_ACTIONS = {
    ("TOP_FARM", "REST"):        lambda: drop_to_fallen(),
    ("TOP_FARM", "BOTTOM_FARM"): lambda: go_to_bottom(),
    ("REST", "BOTTOM_FARM"):     lambda: rest_to_bottom(),
    ("REST", "TOP_FARM"):        lambda: recover_to_farming(),
    ("MID", "TOP_FARM"):         lambda: recover_to_farming(),
    ("BOTTOM_FARM", "TOP_FARM"): lambda: recover_to_farming(),
    ("LOWER_LEDGE", "TOP_FARM"): lambda: recover_to_farming(),
    # PORTAL-BOTTOM recovery (right side): adaptive composite from PORTAL_BOT; a partial
    # fall onto LOWER_R climbs the right rope then recovers.
    ("PORTAL_BOT", "TOP_FARM"):  lambda: recover_from_portal(),
    ("LOWER_R", "TOP_FARM"):     lambda: _lower_r_to_top(),
}

def walk_edge(edge):
    """Recorded WALK edge: walk to the destination's home x."""
    return bool(walk_to_x(edge["target_x"]))


def downjump_edge(edge):
    """Recorded DOWNJUMP edge: (optionally walk to target_x, then) jump in the
    recorded direction and verify she landed on the destination node."""
    tx = edge.get("target_x")
    if tx is not None:
        walk_to_x(tx)
    # key = Key.left if edge.get("dismount") == "left" else Key.right
    key = Key.down
    kb.safe_press(key); kb.safe_press(JUMP); time.sleep(0.12)
    kb.safe_release(JUMP); time.sleep(0.2); kb.safe_release(key)
    time.sleep(0.3)
    x, y = stable_char(3)
    return navmap.classify_node(x, y) == edge["dst"]


def replay_macro(edge):
    """Fallback for an edge we can't type-dispatch and have no composite for."""
    print(f"[nav] no typed executor for {edge['src']}->{edge['dst']} (kind={edge.get('kind')})")
    return False


def execute_edge(edge):
    """Dispatch an edge to its executor. Recorded edges carry a `kind` (+ flattened
    params from navmap.load_route); the hand-tuned graph's edges have no `kind` and
    fall through to the built-in EDGE_ACTIONS composites (recovery chains)."""
    kind = edge.get("kind")
    if kind == "rope" and "grab_x" in edge:
        return bool(climb_rope_hop(edge["grab_x"], edge["land_y"],
                                   dismount=edge.get("dismount"), land_node=edge["dst"]))
    if kind == "walk" and "target_x" in edge:
        return walk_edge(edge)
    if kind == "downjump":
        return downjump_edge(edge)
    fn = EDGE_ACTIONS.get((edge["src"], edge["dst"]))   # hand-built composites
    if fn is not None:
        return bool(fn())
    return replay_macro(edge)


# --- water-world navigation (free 2D swim; no scroll-pin) -------------------------
# For water maps the character dot reads true (x,y), so navigation is just "hold the
# arrow keys toward the target minimap coordinate until you arrive". These reuse the
# pure decisions in watermap.py; only the key presses + screen reads live here.
_ARROW = {"up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right}


def set_minimap(x, y, w, h):
    """Override the full-minimap crop (per-map). Water maps have a taller minimap than
    the dragon nest's default (MM_X,MM_Y,MM_W,MM_H = 20,171,229,259)."""
    global MM_X, MM_Y, MM_W, MM_H
    MM_X, MM_Y, MM_W, MM_H = x, y, w, h


def set_map_box(x_min, x_max, y_min, y_max):
    """Override the VALID character-dot box (per-map). The dragon-nest default
    (60-170, 65-175) wrongly excludes the water map's dots (x~40-185, y~76-372), so the
    reader falls back to the largest blob and a minimap-edge PHANTOM wins. Set this to the
    map's real dot range to reject phantoms outside it."""
    global MAP_X_MIN, MAP_X_MAX, MAP_Y_MIN, MAP_Y_MAX
    MAP_X_MIN, MAP_X_MAX, MAP_Y_MIN, MAP_Y_MAX = x_min, x_max, y_min, y_max


# Per-node farming context for the state machine (home/far x, the y that counts as
# a fall off THIS platform, the resting y, and the right-edge safety x).
FARM_CTX = {
    # `home` = where she parks and fires; `left`/`edge` = the LEFT/RIGHT boundaries
    # that, if crossed (knocked by a dragon), send her walking back to home instead of
    # off the platform. LIVE-TUNE `home`/`left` to the leftmost x that is still safe.
    "TOP_FARM":    dict(home=63, far=112, fall_y=FALLEN_Y_MIN, home_y=85,
                        left=61, edge=RIGHT_EDGE_SAFETY, cap=3.5),
    "BOTTOM_FARM": dict(home=64, far=BOTTOM_FAR_X, fall_y=BOTTOM_FALL_Y,
                        home_y=143, left=62, edge=BOTTOM_FAR_X + 6, cap=2.2),
}

DEPLETED = "DEPLETED"   # _stand_shoot sentinel: platform ran dry mid-shoot -> rotate now
FELL = "FELL"           # approach_shoot sentinel: her minimap y dropped off the platform -> re-seat
LOST = "LOST"           # approach_shoot sentinel: the player anchor found nothing for many beats
#                         (YOLO HP-bar + nametag both failing) -> re-seat instead of blind-attacking

_PLAYER_MISS_DIR = "datasets/player_misses"
_last_player_miss = [0.0]


_YOLO_FAIL_DIR = "datasets/player_yolo_fail"
_last_yolo_fail = [0.0]
_last_fail_small = [None]        # 64x36 gray of the last SAVED fail frame -> near-duplicate reject


CHAR_FIELDS = ("attack_key", "attack_keys", "buff_keys", "buff_groups",
               "buff_interval_secs", "buff_settle_secs",
               "nametag_template", "title_template")   # per-character name/title for the anchor


def break_cycle(idle_seconds=30):
    """Full human-like break: down-jump off the left of the farming platform to the
    safe fallen platform (away from dragons), idle there, then recover back up.
    If the drop fails, stays on the farming platform (no break) rather than risk it."""
    if not focus():
        print("[break] could not focus"); return False
    if not drop_to_fallen():
        print("[break] drop failed -> staying on farming (no break taken)")
        return False
    print(f"[break] idling on fallen platform {idle_seconds}s (safe from dragons)")
    end = time.time() + idle_seconds
    while time.time() < end:
        if kb.pause:
            break
        time.sleep(0.5)
    ok = recover_to_farming()
    print(f"[break] done, recovered={ok}")
    return ok


def record_climb_attempt(cap=20.0):
    """Data collection ONLY (sense-only, presses NO keys): while the USER manually
    climbs bottom->top, poll get_character_full() at ~30Hz into reads=[[t,x,y],...].
    Stops on 'n' (kb.pause) or `cap` seconds. Zero-risk: never moves the character."""
    reads, t0 = [], time.time()
    while time.time() - t0 < cap:
        if kb.pause:
            break
        x, y = get_character_full()
        if x >= 0:
            reads.append([round(time.time() - t0, 3), int(x), int(y)])
        time.sleep(0.03)
    return {"reads": reads}


def recover_to_farming(max_rounds=8):   # extra rounds: a dragon can hit her mid-climb
    """Fallen -> farming: walk to the rope x, climb, up-jump. Retries a few times;
    bails safely (releases keys) rather than risking a blind fall."""
    if not focus():
        print("[recover] could not focus game"); return False
    for rnd in range(max_rounds):
        x, y = stable_char(3)                       # median smooths the idle bob (fast)
        print(f"[recover] round {rnd+1}: at ({x},{y})")
        if x < 0:
            print("[recover] position unknown -> bail"); kb.safe_release_all(); return False
        if is_farming(x, y) and y <= ON_PLATFORM_Y:      # STANDING on the platform (not rope-top)
            print("[recover] on top -> move to farm home, face right")
            walk_to_x(FARM_CTX["TOP_FARM"]["home"], tol=2)   # go straight to the farm home (one pass)...
            _face_right(0.04)                    # ...facing right (short tap = minimal drift off home)
            x2, y2 = stable_char(3)
            if is_farming(x2, y2) and y2 <= ON_PLATFORM_Y:
                kb.safe_release_all(); return True
            print(f"[recover] slipped off top during home-walk ({x2},{y2}) -> retry")
            kb.safe_release_all(); time.sleep(0.2); continue
        if y < FALLEN_Y_MIN:                              # 89-95: HANGING at the rope top just below
            print(f"[recover] at rope top ({x},{y}) -> up-jump onto platform")
            if not climb_and_jump():                      # climb_and_jump up-jumps + verifies mount
                print("[recover] mount from rope top failed -> retry")
                kb.safe_release_all(); time.sleep(0.2); continue
            time.sleep(0.1); continue
        # ONLY the fallen platforms beside the rope are rope-recoverable. If she's
        # deeper or off to the side, walking toward the rope just cascades her further
        # down -- STOP instead (caller escapes to Free Market / stops).
        if not (RECOVER_X_MIN <= x <= RECOVER_X_MAX and y <= RECOVER_Y_MAX):
            print(f"[recover] ({x},{y}) outside recoverable region -> cannot rope-recover, bail")
            kb.safe_release_all(); return False
        # LOWER ledge / rope / fallen platform beside the rope: climb_and_jump does its own
        # align + jump-grab + climb (and tolerates already HANGING on the rope, where a
        # horizontal walk can't move her). Don't pre-walk here -- that walk aborts on the rope
        # and used to spin recover forever ("walk to rope aborted -> retry").
        if not climb_and_jump():
            print("[recover] climb didn't reach top -> re-align & retry")
            kb.safe_release_all(); time.sleep(0.2); continue
        time.sleep(0.1)
    x, y = get_character_full()
    ok = is_farming(x, y) and 0 <= y <= ON_PLATFORM_Y     # truly mounted, not hanging at rope top
    print(f"[recover] final ({x},{y}) farming={ok}")
    kb.safe_release_all()
    return ok


# EXP reading / tracking / session persistence live in exp_tracking.py (extracted from here).
# Re-exported so callers and tests keep using core.<name> and monkeypatch.setattr(core, ...)
# still binds them. Import is at module end so all engine names exp_tracking calls (via core.<x>)
# are defined first; exp_tracking references core only at call time, so the cycle is safe.
from exp_tracking import (  # noqa: E402
    get_exp_number, get_exp, make_exp_tracker,
    append_exp_session, read_exp_sessions, _EXP_SESSIONS_PATH,
)


# The safety subsystem (lie-check scans, alarms, background monitor, free-market escape,
# enemy-dot detection) lives in safety.py, extracted from here. All its STATE stays above
# (alarms/alerts/timers/FM state/thread handles/ENEMY_THRESHOLD/minimap bounds); safety.py
# reads it as core.<name>, so monkeypatch.setattr(core, ...) still binds. Re-exported so
# callers/tests keep using core.<name>; safety references core only at call time (safe cycle).
from safety import (  # noqa: E402
    dump_frame, _hit_tag, lie_check_fast_tick,
    lie_check_full_tick, lie_check_tick, _safety_monitor_loop,
    start_safety_monitor, stop_safety_monitor, lie_check_silence,
    is_lie_check_active, enemy_alarm_on, enemy_alarm_silence,
    silence_all_alarms, _click_screen, click_free_market,
    _maybe_escalate_to_free_market, set_enemy_threshold, _dump_enemy_sample,
    _append_enemy_shadow_log, _enemy_shadow, _enemy_dots,
    get_enemy,
)
kb.f9_callback = silence_all_alarms   # 'm' silences alarms wherever kb.on_press is the listener
kb.f10_callback = lambda: dump_frame("manual")   # F10 dumps the current frame for offline recall tuning


from char_config import (  # noqa: E402  (extracted; re-exported so core.<name> still works)
    load_char, apply_character, parse_buff_groups,
)


from capture import (  # noqa: E402  (extracted; re-exported so core.<name> still works)
    _window_box, _sct, capture,
    capture_pil_np, capture_minimap,
)


from focus_util import (  # noqa: E402  (extracted; re-exported so core.<name> still works)
    focus, is_focused, ensure_game_focused,
    focus_ok_or_bail, stable_char,
)


from charloc import (  # noqa: E402  (extracted; re-exported so core.<name> still works)
    _minimap, _char_color_from_mm, get_character_color,
    _pick_char_blob, get_character_full, swim_read,
    minimap_scroll,
)


from swim import (  # noqa: E402  (extracted; re-exported so core.<name> still works)
    _apply_swim_keys, swim_to, sink_to_bottom,
    hold_to_bottom, water_shoot,
)


from approach import (  # noqa: E402  (extracted; re-exported so core.<name> still works)
    walk_shoot, approach_shoot, _dump_player_miss,
    _dump_yolo_fail, _yolo_primary_anchor, _reactive_deplete,
    _stand_shoot, _walk_shoot_sweep,
)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    # Optional recorded route: `--route routes/<map>.route.json` loads the recorded
    # node-graph (falls back to the hand-tuned graph when omitted).
    if "--route" in sys.argv:
        _ri = sys.argv.index("--route")
        _rp = sys.argv[_ri + 1] if _ri + 1 < len(sys.argv) else None
        if _rp:
            navmap.load_route(_rp)
            print(f"[nav] loaded recorded route: {_rp} "
                  f"({len(navmap.NODES)} nodes, {len(navmap.EDGES)} edges)")
        del sys.argv[_ri:_ri + 2]                 # strip so positional args (e.g. secs) still parse
    _map_path = None                              # `--map maps/<name>.json` for water maps
    if "--map" in sys.argv:
        _mi = sys.argv.index("--map")
        _map_path = sys.argv[_mi + 1] if _mi + 1 < len(sys.argv) else None
        del sys.argv[_mi:_mi + 2]
    _minimap_override = None                       # `--minimap x,y,w,h` (e.g. recording a water map)
    if "--minimap" in sys.argv:
        _ni = sys.argv.index("--minimap")
        _minimap_override = tuple(int(v) for v in sys.argv[_ni + 1].split(","))
        del sys.argv[_ni:_ni + 2]
        set_minimap(*_minimap_override)
        print(f"[nav] minimap crop override -> {_minimap_override}")
    if cmd == "focus":
        print("focused:", focus())
    elif cmd == "recover":
        lis = Listener(on_press=kb.on_press); lis.start()   # 'n' aborts
        try:
            print("RESULT:", recover_to_farming())
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "where":                                    # print current node classification
        x, y = stable_char(4)
        print(f"({x},{y}) -> {navmap.classify_node(x, y)}")
    elif cmd == "trackpos":                                 # live minimap (x,y) readout; 'n'/Ctrl-C to stop
        hz = float(sys.argv[sys.argv.index("--hz") + 1]) if "--hz" in sys.argv else 6.0
        lis = Listener(on_press=kb.on_press); lis.start()
        print(f"[trackpos] live minimap (x,y) at {hz:.0f}Hz -- move up/down to watch the pin. 'n' pauses, Ctrl-C stops.")
        prev = None
        try:
            while True:
                if kb.pause:
                    time.sleep(0.1); continue
                x, y = get_character_full()
                dyq = "" if (prev is None or y < 0 or prev < 0) else f"  dy={y - prev:+d}"
                if y >= 0:
                    prev = y
                print(f"  ({x:>4},{y:>4}){dyq}")
                time.sleep(1.0 / hz)
        except KeyboardInterrupt:
            pass
        finally:
            lis.stop()
    elif cmd == "hop":                                      # test one climb_rope_hop: hop GX LY [dismount]
        gx, ly = int(sys.argv[2]), int(sys.argv[3])
        dm = sys.argv[4] if len(sys.argv) > 4 else None
        lis = Listener(on_press=kb.on_press); lis.start()   # 'n' aborts
        try:
            print("RESULT:", climb_rope_hop(gx, ly, dismount=dm))
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "portal":                                   # run the full nav chain -> TOP_FARM
        lis = Listener(on_press=kb.on_press); lis.start()   # 'n' aborts
        try:
            print("RESULT:", navmap.travel("TOP_FARM", locate_fn=_nav_locate,
                                            execute_fn=execute_edge))
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "record-route":
        mp = sys.argv[2] if len(sys.argv) > 2 else "map"
        if not focus():
            print("no focus"); sys.exit(1)
        import record_route
        record_route.record_live(mp)
    elif cmd == "record-climb":
        import json as _json
        if not focus():
            print("no focus"); sys.exit(1)
        lis = Listener(on_press=kb.on_press); lis.start()      # 'n' stops
        print("[record-climb] manually climb bottom->top; press 'n' to stop")
        try:
            tr = record_climb_attempt()
        finally:
            kb.safe_release_all(); lis.stop()
        with open("climb_traj.jsonl", "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(tr) + "\n")
        print(f"[record-climb] wrote {len(tr['reads'])} reads to climb_traj.jsonl")
    elif cmd == "drop":
        if not focus():
            print("could not focus"); sys.exit(1)
        lis = Listener(on_press=kb.on_press); lis.start()   # 'n' aborts
        try:
            print("RESULT:", drop_to_fallen())
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "break":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        lis = Listener(on_press=kb.on_press); lis.start()   # 'n' aborts
        try:
            print("RESULT:", break_cycle(secs))
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "demo":
        lis = Listener(on_press=kb.on_press); lis.start()   # 'n' aborts
        try:
            demo_sequence()
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "gobottom":
        if not focus():
            print("could not focus"); sys.exit(1)
        lis = Listener(on_press=kb.on_press); lis.start()   # 'n' aborts
        try:
            print("RESULT:", go_to_bottom())
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "farmbottom":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        if not focus():
            print("could not focus"); sys.exit(1)
        lis = Listener(on_press=kb.on_press); lis.start()   # 'n' aborts
        try:
            print("RESULT:", farm_bottom(secs))
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "runnav":
        # EXP-low auto-pause removed per user (unreliable; monitored manually).
        def _enemy_check(): return len(get_enemy()) > 0
        def _panic():
            kb.safe_release_all()
            print("[safety] trigger -> releasing keys and PAUSING (no Free Market). 'n' to resume.")
            kb.pause = True
        lis = Listener(on_press=kb.on_press); lis.start(); kb.pause = True
        print("FULL RUN (runnav) ready. Switch to the game and press 'n' to start / pause.")
        import watermap
        _navcfg = watermap.load_map("maps/dragon_nest.json")   # rest cadence overrides live here
        try:
            import farm_dragon
            farm_dragon.farming_loop_nav(exp_check=None, enemy_check=_enemy_check, panic=_panic, cfg=_navcfg)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "nav":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        lis = Listener(on_press=kb.on_press); lis.start()
        try:
            import farm_dragon
            farm_dragon.farming_loop_nav(break_every=(9999, 9999), stand_secs=(4, 8), max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "fishcount":
        # Live tuning: capture one frame, count fish, print best scores, save a debug image.
        # Config supplies defaults; CLI flags override for fast iteration without editing JSON:
        #   --scale S  --thr T  --per N  --roi x0,y0,x1,y1  --sweep
        import fish as _fish

        def _flag(name, cast=str, default=None):
            if name in sys.argv:
                i = sys.argv.index(name)
                v = sys.argv[i + 1]
                del sys.argv[i:i + 2]
                return cast(v)
            return default

        sweep = "--sweep" in sys.argv
        if sweep:
            sys.argv.remove("--sweep")
        cfg = watermap.load_map(_map_path) if _map_path else {}
        if _map_path and cfg.get("minimap"):
            set_minimap(*watermap.minimap_crop(cfg))
        scale = _flag("--scale", float, None)
        thr = _flag("--thr", float, None)
        per = _flag("--per", int, None)
        species_s = _flag("--species", str, None)
        roi_s = _flag("--roi", str, None)
        if species_s:
            cfg = {**cfg, "fish_species": species_s.split(",")}
        if per:
            cfg = {**cfg, "fish_per_species": per}
        _tmpls0, mode = _fish.templates_for(cfg, scale=scale)
        if thr is None:
            thr = cfg.get("fish_threshold") or _fish.default_threshold(mode)
        roi = tuple(int(v) for v in roi_s.split(",")) if roi_s else (
            tuple(cfg["count_roi"]) if cfg.get("count_roi") else None)
        if not focus():
            print("no focus"); sys.exit(1)
        time.sleep(0.4)
        f = capture()
        if f is None:
            print("no frame"); sys.exit(1)

        ds = cfg.get("match_downscale", 0.5)
        if sweep:
            print(f"[fishcount] SWEEP mode={mode} roi={roi} thr={thr} ds={ds}")
            for s in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4):
                tmpls, _ = _fish.templates_for(cfg, scale=s)
                r = _fish.scan(f, tmpls, roi=roi, threshold=thr, downscale=ds)
                print(f"  scale {s}: best={r['best']}  count@{thr}={len(r['dets'])}")
            cv2.imwrite("fishcount_debug.png", f if roi is None else
                        cv2.rectangle(f.copy(), (roi[0], roi[1]), (roi[2], roi[3]), (0, 255, 255), 2))
            print("[fishcount] wrote fishcount_debug.png (yellow=roi). Pick the scale with high"
                  " best-scores on fish and count matching what you see.")
        else:
            tmpls, _ = _fish.templates_for(cfg, scale=scale)
            r = _fish.scan(f, tmpls, roi=roi, threshold=thr, downscale=ds)
            b, dets = r["best"], r["dets"]
            print(f"[fishcount] mode={mode} scale={scale or cfg.get('fish_scale', 1.0)} "
                  f"thr={thr} ds={ds} roi={roi} templates={len(tmpls)}")
            print(f"[fishcount] best score by species: {b}")
            print(f"[fishcount] COUNT = {len(dets)}")
            dbg = f.copy()
            if roi:
                cv2.rectangle(dbg, (roi[0], roi[1]), (roi[2], roi[3]), (0, 255, 255), 2)
            for s, x, y, w, h in dets:
                cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 0, 255), 2)
                cv2.putText(dbg, f"{s:.2f}", (x, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            cv2.imwrite("fishcount_debug.png", dbg)
            print("[fishcount] wrote fishcount_debug.png (yellow=roi, red=matches)")
    elif cmd == "waternav":
        # Water-map farming. Requires --map maps/<name>.json. Optional secs (bounded test).
        if not _map_path:
            print("usage: python core.py waternav [secs] --map maps/<name>.json"); sys.exit(1)
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else None
        def _enemy_check(): return len(get_enemy()) > 0
        def _panic():
            kb.safe_release_all()
            print("[safety] trigger -> releasing keys and PAUSING. 'n' to resume.")
            kb.pause = True
        lis = Listener(on_press=kb.on_press); lis.start()
        if secs is None:
            kb.pause = True
            print("WATER RUN (waternav) ready. Switch to the game and press 'n' to start / pause.")
        try:
            import farm_water
            farm_water.farming_loop_water(_map_path, enemy_check=_enemy_check, panic=_panic,
                                          max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "sweep":
        # General-map directional sweep. Requires --map maps/<name>.json. Optional secs.
        if not _map_path:
            print("usage: python core.py sweep [secs] --map maps/<name>.json"); sys.exit(1)
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else None
        def _enemy_check(): return len(get_enemy()) > 0
        def _panic():
            kb.safe_release_all()
            print("[safety] trigger -> releasing keys and PAUSING. 'n' to resume.")
            kb.pause = True
        lis = Listener(on_press=kb.on_press); lis.start()
        if secs is None:
            kb.pause = True
            print("SWEEP RUN ready. Switch to the game and press 'n' to start / pause.")
        try:
            import farm_general
            farm_general.farming_loop_general(_map_path, enemy_check=_enemy_check, panic=_panic,
                                              max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
    else:
        print("usage: python core.py <cmd>")
        print("  run:   runnav          full production farming run ('n' to start/pause)")
        print("  test:  nav [secs]      bounded runnav (default 90s)")
        print("  nav debug: focus | where | trackpos [--hz N] | hop GX LY [dismount] | portal | drop | gobottom | recover")
        print("  route: record-route <map>   record a path -> routes/<map>.capture.jsonl")
        print("  water: waternav [secs] --map maps/<name>.json   water-map farming ('n' start/pause)")
        print("  water: fishcount --map maps/<name>.json [--sweep|--scale S|--thr T|--per N|--roi x0,y0,x1,y1]")
        print("  other: farmbottom [s] | demo | break [s] | record-climb")
