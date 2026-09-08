"""Fallen -> home recovery foundation.

Provides full-minimap position sensing (sees the lower platforms the bot's narrow
band crop misses) in the SAME coordinate frame as the existing calibration
(offset 20,171 -> so ROPE_MINIMAP_X=91, PLATFORM_Y=101 stay valid).

CLI:
  python recovery.py runnav                     # full production farming run (F8 start/pause)
  python recovery.py nav [secs]                 # bounded runnav test (default 90s)
  python recovery.py focus                      # bring game to foreground (verify)
  (run with no/unknown cmd to print the full command list)
"""
import sys, time, ctypes
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
import keyboard as kb   # safe_press/release/release_all + F8 pause listener
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


def _window_box():
    """The game window's screen rect as an mss grab box, or None. Prefers pygetwindow;
    falls back to the Win32 finder focus() uses (FindWindowW + GetWindowRect). Does NOT
    require the window to sit fully inside one monitor -- a maximized / monitor-straddling
    window used to make capture() return None (character read (-1,-1) -> recovery bailed)."""
    win = gw.getWindowsWithTitle(TITLE)
    if win:
        w = win[0]
        box = {"left": w.left, "top": w.top, "width": w.width, "height": w.height}
    else:                                                # pygetwindow missed the title -> Win32
        hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
        if not hwnd:
            return None
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        box = {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}
    if box["width"] <= 0 or box["height"] <= 0:
        return None
    return box


_sct_tls = threading.local()


def _sct():
    """One reusable mss handle PER THREAD. mss.mss() allocates (and its context-exit frees)
    a Windows device context every call, so doing it per grab dominated capture cost; a
    persistent handle skips that. Thread-local because mss is not thread-safe and the farming
    loop and panel can grab concurrently -- each thread gets its own handle, lazily created."""
    s = getattr(_sct_tls, "sct", None)
    if s is None:
        s = mss.mss()
        _sct_tls.sct = s
    return s


def capture():
    box = _window_box()
    if box is None:
        return None
    shot = _sct().grab(box)
    return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


def capture_pil_np():
    """Capture returning (PIL RGB, np BGR) -- get_exp() needs the PIL image."""
    box = _window_box()
    if box is None:
        return None, None
    shot = _sct().grab(box)
    pil = Image.frombytes("RGB", shot.size, shot.rgb)
    return pil, cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


def capture_minimap():
    """Grab ONLY the minimap ROI (MM_X/Y/W/H) as BGR -- ~37x fewer pixels than capture() plus
    it skips the full-frame cvtColor, so the hot position reads (stable_char does >=4 per read)
    get much cheaper. Equivalent to _minimap(capture()) but grabs just the sub-box. Clamped to
    the window rect so a smaller live window can't pull in neighbouring screen pixels (mirrors
    _minimap's clamp). Returns None if the window is gone / the ROI falls outside it."""
    box = _window_box()
    if box is None:
        return None
    if MM_X >= box["width"] or MM_Y >= box["height"]:
        return None
    w = min(MM_W, box["width"] - MM_X)
    h = min(MM_H, box["height"] - MM_Y)
    region = {"left": box["left"] + MM_X, "top": box["top"] + MM_Y, "width": w, "height": h}
    shot = _sct().grab(region)
    return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


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


def dump_frame(reason="manual", frame=None, tag=""):
    """Save a FULL capture() frame to datasets/lie_check/captures/ for offline recall tuning.
    F10 dumps the current frame on demand -- use it to grab a lie-check screen the detector
    MISSED (the one case auto-dump can't see, since it only fires on a hit). The fast/full
    ticks also call this when they fire, so real live-res positives accumulate for threshold
    tuning. Reuses the passed `frame` when given (no extra grab). Never raises -- this rides
    the safety path and must not crash the loop."""
    try:
        f = frame if frame is not None else capture()
        if f is None or not hasattr(f, "shape"):
            print("[dump] no frame (window gone?)")
            return None
        os.makedirs(_LIE_CAP_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime()) + f"_{int((time.time() % 1) * 1000):03d}"
        safe_tag = "".join(c for c in tag if c.isalnum() or c in "-_.")
        name = f"{ts}_{reason}" + (f"_{safe_tag}" if safe_tag else "") + ".png"
        path = os.path.join(_LIE_CAP_DIR, name)
        cv2.imwrite(path, f)
        print(f"[dump] saved {path}  ({f.shape[1]}x{f.shape[0]})")
        return path
    except Exception as e:
        print(f"[dump] failed: {e}")
        return None


def _hit_tag(hits):
    """Compact filename tag from detection hits: names+scores, e.g. 'curse_lock0.85'."""
    return "_".join(f"{n.replace('.png', '')}{s:.2f}" for n, s in hits)


def lie_check_fast_tick(interval=0.8):
    """Fast SAFETY scan (~40ms) on ONE capture at `interval` cadence, run in the hot loops:
    BOTH the transparent-shape lie-check (captcha -> alarm) AND the another-player check
    (red dot on the minimap -> alarm + auto-pause). They share this single tick so they run
    at the same time and frequency -- neither can be missed while the other is checked."""
    now = time.time()
    if now - _last_fast_tick[0] < interval:
        return
    _last_fast_tick[0] = now
    f = capture()
    if f is None or not hasattr(f, "shape"):
        return
    if _lie_enabled:                                       # transparent-shape captcha -> alarm
        hits = detect_lie_check(f, templates_folder=_LIE_DIR,
                                template_filter=_FAST_TEMPLATES, work_width=520)
        if _fast_alert.update(bool(hits)) and hits:
            print(f"[lie-check] ⚠️ 透明圖形驗證 {[(n, round(s, 2)) for n, s in hits]} -- ALARM (F8 暫停)")
            notify.send("lie_check", f"⚠️ 透明圖形驗證 (captcha) detected {[n for n, _ in hits]} — needs a human (F8 暫停)")
            dump_frame("fast_auto", f, tag=_hit_tag(hits))
    dots = _enemy_dots(f)                                  # another player -> alarm + pause
    if dbg_on():
        dbg(f"[enemy] scan -> {len(dots)} red dot(s)" + (f" at {dots}" if dots else ""))
    if dots:
        print("[water] another player -> ALARM + PAUSE (F9 silence, F8 resume)")
        notify.send("another_player", "⚠️ Another player entered the map — bot PAUSED (F9 silence, F8 resume)")
        enemy_alarm_on(); kb.safe_release_all(); kb.pause = True

# Near-miss telemetry: the full check only auto-dumps on a HIT, so a MISSED curse/monster screen
# (the exact failure we're chasing) leaves no trace. When a template lands in [floor, threshold)
# -- close but not firing -- dump the frame so we can see WHY it missed (degraded live text vs a
# runtime problem) and feed it to the planned YOLO detector. Throttled so a normal frame that
# grazes the floor can't spam. Floor is above the ~0.72 normal-frame ceiling so dumps are rare.
_NEAR_MISS_FLOOR = 0.72
_NEAR_MISS_INTERVAL = 30.0
_last_near_miss_dump = [0.0]


def lie_check_full_tick(interval=1.5):
    """Full check for the slower screens (curse / monster). Loop top only."""
    if not _lie_enabled:
        return
    now = time.time()
    if now - _last_full_tick[0] < interval:
        return
    _last_full_tick[0] = now
    f = capture()
    if f is None or not hasattr(f, "shape"):
        return
    # higher work_width keeps the curse banner/lock detail (fine 2-line text + icon)
    # so they clear threshold on smaller live windows; per-template thresholds apply.
    scores = {}
    hits = detect_lie_check(f, templates_folder=_LIE_DIR, template_filter=_FULL_TEMPLATES,
                            work_width=1000, scores=scores)
    yolo = detect_lie_check_yolo(f, conf=0.80)           # [] when the model file is absent
    #   conf 0.80: real popups score 0.978-0.991; worst false detection on normal frames was
    #   0.701 (a minimap-crop debug image) -- 0.80 clears positives with headroom for degraded
    #   live frames while staying above that floor. Recall-first, measured not guessed.
    hits = hits + [(f"yolo:{cls}", cf) for cls, cf, _ in yolo]
    if _full_alert.update(bool(hits)) and hits:
        print(f"[lie-check] ⚠️ 需真人處理畫面 {[(n, round(s, 2)) for n, s in hits]} -- ALARM (F8 暫停)")
        notify.send("lie_check", f"⚠️ 需真人處理畫面 (curse/monster) {[n for n, _ in hits]} — needs a human (F8 暫停)")
        dump_frame("full_auto", f, tag=_hit_tag(hits))
    elif not hits:                                        # near-miss capture (below threshold)
        near = [(n, s) for n, s in scores.items() if s >= _NEAR_MISS_FLOOR]
        if near and now - _last_near_miss_dump[0] >= _NEAR_MISS_INTERVAL:
            _last_near_miss_dump[0] = now
            near.sort(key=lambda x: -x[1])
            print(f"[lie-check] near-miss (below threshold) {[(n, round(s, 2)) for n, s in near]} -- dumping frame")
            dump_frame("near_miss", f, tag=_hit_tag(near))

def lie_check_tick():
    """Loop-top check: run both pipelines (fast covers transparent between states too)."""
    lie_check_fast_tick()
    lie_check_full_tick()


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


def _safety_monitor_loop():
    while not _safety_stop.is_set() and not STOP.is_set():
        if not kb.pause:
            try:
                lie_check_full_tick()                  # self-throttled to 1.5s internally
                STATUS["lie"] = is_lie_check_active()
            except Exception as e:
                print(f"[safety] monitor error: {e}")
        _safety_stop.wait(0.3)                          # poll ~3x/s; full_tick paces the real work


def start_safety_monitor():
    """Start the background FULL lie-check monitor (idempotent). Call at farm start."""
    if not _lie_enabled:
        return
    t = _safety_thread[0]
    if t is not None and t.is_alive():
        return
    _safety_stop.clear()
    t = threading.Thread(target=_safety_monitor_loop, name="safety-monitor", daemon=True)
    _safety_thread[0] = t
    t.start()
    print("[safety] background curse/monster monitor started (off the action loop)")


def stop_safety_monitor():
    """Stop the background monitor (idempotent). Call at every farm-loop exit."""
    _safety_stop.set()
    t = _safety_thread[0]
    if t is not None and t.is_alive() and t is not threading.current_thread():
        t.join(timeout=1.0)
    _safety_thread[0] = None


def lie_check_silence():
    """Stop both alarms (e.g. when the bot pauses -- a human is present)."""
    _fast_alert.update(False)
    _full_alert.update(False)


def is_lie_check_active():
    """True while either lie-check alarm is sounding (a human is needed)."""
    return bool(_fast_alert.alarm.active or _full_alert.alarm.active)


# --- another-player alarm: a DISTINCT lower tone, sounds until F9 acknowledges it ---
_enemy_alarm = Alarm(freq=700, beep_ms=250, gap_ms=120)


def enemy_alarm_on():
    """Start the another-player alarm (keeps beeping until F9 / silence)."""
    _enemy_alarm.start()


def enemy_alarm_silence():
    _enemy_alarm.stop()


def silence_all_alarms():
    """F9: acknowledge -- stop every alarm (lie-check + another-player). Quiet when nothing
    is sounding, so F9 stays usable for other things (e.g. record-route node marks)."""
    active = is_lie_check_active() or _enemy_alarm.active
    lie_check_silence()
    enemy_alarm_silence()
    if active:
        print("[alarm] silenced (F9)")


kb.f9_callback = silence_all_alarms   # F9 silences alarms wherever kb.on_press is the listener
kb.f10_callback = lambda: dump_frame("manual")   # F10 dumps the current frame for offline recall tuning


# --- shared status + cooperative stop, for the control UI (panel.py) --------------
# STATUS is a plain dict updated in-place by the loops; the UI polls it. STOP is a
# cooperative stop the UI sets to end a background-thread run (F8 pause still works).
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


def set_enemy_threshold(t):
    global ENEMY_THRESHOLD
    ENEMY_THRESHOLD = float(t)


# --- enemy-dot shadow rollout: the TEMPLATE detector stays authoritative (drives the alarm);
# the new HSV color detector (detect_red_dots_color) runs alongside in SHADOW so we can prove it
# matches the template on REAL frames before switching. On any activity we log presence agreement
# and dump the FULL frame (throttled) so the rare other-player frames -- and any color false-alarm
# or miss -- accumulate for offline double-checking. The shadow never changes the alarm decision
# and never raises into the safety loop. ---
_ENEMY_SAMPLE_DIR = "datasets/enemy_dots/samples"      # full frames dumped here for offline review
_ENEMY_SHADOW_LOG = "logs/enemy_dots_shadow.jsonl"     # one line per template/color activity
_ENEMY_AGREE_DUMP_INTERVAL = 30.0    # both agree (player present or both quiet-with-one-firing): rarer dumps
_ENEMY_DISAGREE_DUMP_INTERVAL = 10.0 # color-only or template-only: the interesting cases, dumped more often
_enemy_shadow_state = {"agree": 0.0, "disagree": 0.0}  # last-dump wall-clock per category (throttle)


def _dump_enemy_sample(frame, tag=""):
    """Save a FULL frame to datasets/enemy_dots/samples/ for offline template-vs-color review.
    Best-effort -- never raises (rides the safety path)."""
    try:
        os.makedirs(_ENEMY_SAMPLE_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime()) + f"_{int((time.time() % 1) * 1000):03d}"
        safe = "".join(ch for ch in tag if ch.isalnum() or ch in "-_.")
        path = os.path.join(_ENEMY_SAMPLE_DIR, f"{ts}_enemy_{safe}.png")
        cv2.imwrite(path, frame)
        return path
    except Exception:
        return None


def _append_enemy_shadow_log(row):
    """Append one shadow-comparison row (ts, template count, color count, agree) as JSON.
    Best-effort -- never raises."""
    try:
        import json
        os.makedirs(os.path.dirname(_ENEMY_SHADOW_LOG) or ".", exist_ok=True)
        with open(_ENEMY_SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _enemy_shadow(frame, mm, template_dots):
    """Double-check the color detector against the authoritative template result on this frame.
    Logs every activity and dumps a sample (throttled) whenever either detector fires. Compares
    at the PRESENCE level (len>0) -- the boolean the safety check actually uses. Never raises."""
    try:
        color_dots = detect_red_dots_color(mm)
    except Exception:
        return
    t, c = len(template_dots), len(color_dots)
    if t == 0 and c == 0:
        return                                     # all-clear frame -> nothing to learn
    agree = (t > 0) == (c > 0)
    now = time.time()
    _append_enemy_shadow_log({"ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                              "template": t, "color": c, "agree": agree})
    key = "agree" if agree else "disagree"
    interval = _ENEMY_AGREE_DUMP_INTERVAL if agree else _ENEMY_DISAGREE_DUMP_INTERVAL
    if now - _enemy_shadow_state[key] >= interval:
        _enemy_shadow_state[key] = now
        _dump_enemy_sample(frame, f"t{t}_c{c}_{'agree' if agree else 'DISAGREE'}")


def _enemy_dots(frame):
    """Red dots (other players) in the minimap band of a captured BGR frame. TEMPLATE match is
    authoritative (drives the another-player alarm); detect_red_dots_color runs in shadow to
    validate the switch (see _enemy_shadow). Presence-only -> non-empty means escape."""
    if frame is None:
        return []
    ey = min(MM_Y + MM_H, frame.shape[0]); ex = min(MM_X + MM_W, frame.shape[1])
    mm = frame[MM_Y:ey, MM_X:ex]                  # FULL configured minimap band (lower platforms too)
    try:
        dots = detect_red_dots(mm, templates_folder="assets/minimap_other_character/",
                               threshold=ENEMY_THRESHOLD)
    except Exception:
        return []
    try:
        _enemy_shadow(frame, mm, dots)            # shadow double-check: never affects the return
    except Exception:
        pass
    return dots


def get_enemy():
    """Red dots (other players) on the minimap band. Non-empty -> escape. Standalone capture
    (used by guard/panel); the hot-loop scan shares lie_check_fast_tick's frame."""
    return _enemy_dots(capture())


def get_exp_number(exp_processor):
    """Absolute EXP number via OCR (or None). Unlike get_exp it does NOT run the delta /
    'reasonable gain' logic, so it never prints the anomaly warning -- the per-10-min
    tracker diffs these numbers itself with its own filtering."""
    import ocr_processor
    pil_img, _ = capture_pil_np()
    if pil_img is None:
        return None
    ow, oh = pil_img.width, pil_img.height
    wo, ho = (0, 0) if ow % 10 == 0 else (22, 56)
    cw, ch = ow - wo, oh - ho
    rw, rh = 1920, 1080
    reg = (1028, 996, 1190, 1025)
    try:
        left = int(cw * reg[0] / rw) + wo
        top = max(0, int(ch * reg[1] / rh) + ho)
        right = min(ow, int(cw * reg[2] / rw) + wo)
        bottom = min(oh, int(ch * reg[3] / rh) + ho)
        adj = int(max(-30, min(30, (cw - rw) * 0.01)))
        left = max(0, left + adj)
        raw = ocr_processor.run_ocr(ocr_processor.preprocess_exp_image(
            pil_img.crop((left, top, right, bottom))), 'exp')
        num_str, _pct = exp_processor.parse_exp_data(raw)
        val = int(num_str) if num_str else None
        if dbg_on():                                      # detection trace for exp-anomaly debugging
            dbg(f"[exp] ocr raw={raw!r} parsed={num_str!r} -> {val}")
        return val
    except Exception:
        return None


def get_exp(exp_processor):
    """Recent EXP gain as [gain, num_str, pct_str] (or None). Faithful port of the
    notebook get_exp: the width/height offsets normalize the 150%-DPI physical grab
    (1942x1136 -> 1920x1080) so the crop region lines up."""
    pil_img, _ = capture_pil_np()
    if pil_img is None:
        return None
    ow, oh = pil_img.width, pil_img.height
    wo, ho = (0, 0) if ow % 10 == 0 else (22, 56)
    cw, ch = ow - wo, oh - ho
    rw, rh = 1920, 1080
    reg = (1028, 996, 1190, 1025)
    try:
        left = int(cw * reg[0] / rw) + wo
        top = max(0, int(ch * reg[1] / rh) + ho)
        right = min(ow, int(cw * reg[2] / rw) + wo)
        bottom = min(oh, int(ch * reg[3] / rh) + ho)
        adj = int(max(-30, min(30, (cw - rw) * 0.01)))
        left = max(0, left + adj)
        gain, _img = exp_processor.process_exp_value(pil_img.crop((left, top, right, bottom)), n=2)
        return gain
    except Exception as e:
        print("[exp] read error:", e)
        return None


def make_exp_tracker(log_exp=True, sample_secs=10, window_secs=600, label="exp", min_run_secs=60,
                     max_exp_per_sec=20000):
    """A per-window EXP logger, shared by the water and nav farming loops. Returns an
    `exp_tick()` to call each loop iteration: it samples the ABSOLUTE EXP number every
    `sample_secs` (the FIRST sample is taken immediately so a gain is available within a
    couple of samples), and every `window_secs` (10 min default) logs the gain over that
    window (sum of positive consecutive deltas, skipping level-ups and digit-count OCR
    errors) plus the running average across windows. Publishes STATUS['exp_10min'] (last
    window gain), STATUS['exp_per_min'] (LIVE run-average EXP/min, published once the run has
    at least `min_run_secs` elapsed so the estimate is meaningful) and STATUS['exp_total']
    (cumulative gain). No-op if log_exp is False. Self-contained (closes over its buffer)."""
    exp_proc = ExpProcessor() if log_exp else None
    STATUS["exp_per_min"], STATUS["exp_10min"], STATUS["exp_total"] = None, None, 0  # fresh run
    run_start = time.time()
    next_sample = [time.time()]                                # sample immediately at run start
    next_log = [time.time() + window_secs]
    samples = []                                                # [(t, abs_exp_number)]
    windows = [0, 0.0]                                          # [count, cumulative gain]
    cumulative = [0]                                            # gain since run start (incremental)
    prev = [None]                                              # last (t, num) for the running delta

    def _accept(pn, num, dt):
        """Is (num - pn) a plausible EXP delta? Rejects negatives / level-ups (digit-count
        change) AND implausibly large jumps -- a same-digit-count OCR misread (one wrong digit)
        makes a positive, right-digit-count delta the old filter counted, silently inflating the
        total. The cap = max_exp_per_sec * dt bounds a single sample's gain. Returns (ok, d, reason)."""
        d = num - pn
        if d <= 0:
            return False, d, "nonpositive"
        if len(str(pn)) != len(str(num)):
            return False, d, "digit-change"                     # level-up or gross OCR error
        cap = max_exp_per_sec * max(dt, 1.0)
        if d > cap:
            return False, d, f"over-cap(>{int(cap)})"
        return True, d, "ok"

    def _window_gain(since):
        win = [(t, n) for t, n in samples if t >= since]
        total = 0
        for (t0, n0), (t1, n1) in zip(win, win[1:]):
            ok, d, _reason = _accept(n0, n1, t1 - t0)
            if ok:
                total += d
        return total

    def exp_tick():
        if exp_proc is None:
            return
        now = time.time()
        if now >= next_sample[0]:
            next_sample[0] = now + sample_secs
            num = get_exp_number(exp_proc)
            if num:
                if prev[0] is not None:                         # accumulate the run total incrementally
                    pt, pn = prev[0]
                    ok, d, reason = _accept(pn, num, now - pt)
                    if ok:
                        cumulative[0] += d
                    if dbg_on():                                # exp-anomaly trace (see [exp] ocr lines)
                        dbg(f"[exp] sample num={num} prev={pn} dt={now - pt:.0f}s delta={d} "
                            f"-> {'count' if ok else 'REJECT ' + reason} (total={cumulative[0]})")
                elif dbg_on():
                    dbg(f"[exp] sample num={num} (first)")
                prev[0] = (now, num)
                samples.append((now, num))
                if len(samples) > 400:
                    del samples[:200]
                STATUS["exp_total"] = cumulative[0]
                elapsed = now - run_start
                if elapsed >= min_run_secs:                     # live run-average EXP per minute
                    STATUS["exp_per_min"] = round(cumulative[0] / (elapsed / 60.0))
        if now >= next_log[0]:
            next_log[0] = now + window_secs
            gain = _window_gain(now - window_secs)
            windows[0] += 1
            windows[1] += gain
            avg = windows[1] / windows[0]
            STATUS["exp_10min"] = round(gain)
            mins = int(round(window_secs / 60))
            print(f"[{label}] last {mins} min: {gain:,.0f} EXP  |  avg/{mins}min: {avg:,.0f} "
                  f"(over {windows[0]} window{'s' if windows[0] != 1 else ''})")

    return exp_tick


# --- EXP session log: persist human/bot farm sessions (start/duration/gain/rate/label) to a
# JSON-Lines file so a human-farm reference can be compared against bot runs. ---
_EXP_SESSIONS_PATH = "logs/exp_sessions.jsonl"


def append_exp_session(row, path=_EXP_SESSIONS_PATH):
    """Append one session row as a JSON line. Best-effort -- never raises (it must not crash
    the recorder's shutdown path)."""
    import json
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[exp] could not append session: {e}")


def read_exp_sessions(path=_EXP_SESSIONS_PATH, limit=50):
    """The last `limit` session rows, NEWEST FIRST, for the panel's history table. Skips
    malformed lines; returns [] if the file is absent."""
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []
    rows = []
    for ln in lines[-limit:]:
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    rows.reverse()                                     # newest first
    return rows


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


def _minimap(np_img):
    ey = min(MM_Y + MM_H, np_img.shape[0]); ex = min(MM_X + MM_W, np_img.shape[1])
    return np_img[MM_Y:ey, MM_X:ex]


def _char_color_from_mm(mm, near=None):
    """Character (x,y) via COLOR (bright yellow blob) from a MINIMAP crop `mm`. Shared by
    get_character_color / get_character_full so each does exactly one grab. (-1,-1) if none."""
    hsv = cv2.cvtColor(mm, cv2.COLOR_BGR2HSV)
    # tight to the real dot's signature (hue~29, sat~245, val~242). A translucent buff/
    # heat-aura glow reads as lower saturation/value -> excluded. Size ~54px; keep 15-120.
    mask = cv2.inRange(hsv, np.array([20, 165, 165]), np.array([36, 255, 255]))
    num, labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    blobs = [(int(cents[i][0]), int(cents[i][1]), int(stats[i][4]))
             for i in range(1, num) if 15 <= stats[i][4] <= 120]
    pick = _pick_char_blob(blobs, near)
    if pick is None:
        return -1, -1
    return pick[0], pick[1]


def get_character_color(np_img=None, near=None):
    """Character (x,y) via COLOR (bright yellow blob) on the minimap. Far more robust than
    template matching (which returns a phantom (122,188) at some spots). If `near`=(x,y) is
    given and several yellow blobs exist, pick the closest (temporal continuity); else the
    largest plausible blob. With no `np_img` grabs ONLY the minimap ROI (capture_minimap);
    pass a full BGR frame to crop it instead (shared-frame callers). (-1,-1) if none."""
    mm = _minimap(np_img) if np_img is not None else capture_minimap()
    if mm is None:
        return -1, -1
    return _char_color_from_mm(mm, near=near)


def _pick_char_blob(blobs, near=None):
    """Choose the character dot among yellow blobs, rejecting buff-glow phantoms.
    Prefer blobs inside the valid map box (MAP_X/Y_MIN/MAX); fall back to all blobs
    only when NONE are in-box (e.g. a genuine deep fall) so recovery can still see
    her. Within the candidates: nearest to `near` if given, else the largest."""
    if not blobs:
        return None
    in_box = [b for b in blobs
              if MAP_X_MIN <= b[0] <= MAP_X_MAX and MAP_Y_MIN <= b[1] <= MAP_Y_MAX]
    cand = in_box or blobs
    if near is not None and near[0] >= 0:
        return min(cand, key=lambda b: (b[0]-near[0])**2 + (b[1]-near[1])**2)
    return max(cand, key=lambda b: b[2])


def get_character_full(np_img=None, near=None):
    """Character (x, y) on the FULL minimap. Color first (robust), template as a fallback --
    both read the SAME crop (one grab): the minimap ROI when no frame is passed, else the
    supplied full frame cropped. (-1,-1) if neither finds it."""
    mm = _minimap(np_img) if np_img is not None else capture_minimap()
    if mm is None:
        return -1, -1
    x, y = _char_color_from_mm(mm, near=near)
    if x >= 0:
        return x, y
    centers = detect_character_on_minimap(mm, templates_folder=CHAR_TPL, threshold=0.7)
    if centers:
        return centers[0][0], centers[0][1]
    return -1, -1


_SWIM_MAX_JUMP = 70   # px: a read leaping more than this from the last known pos is a phantom
                      # (a spurious yellow minimap blob), not real motion -- she can't cross that
                      # much of the ~200px-wide minimap between two ~40ms reads.


def swim_read(near=None):
    """FAST position read for swim TRAVEL (direction only): two quick samples ~20ms apart
    instead of stable_char's four. If they agree (within 12px) return their midpoint; if one
    is invalid return the other; if they disagree return the LATEST (freshest). ~2 captures
    vs stable_char's 4 (+0.2s of pacing sleeps), so the swim loop decides direction ~2-3x
    faster and the on-arrival settle freeze shrinks accordingly.

    `near` = the last known position, threaded in by swim_to for temporal continuity: the reads
    prefer the blob NEAREST it, and any sample leaping more than _SWIM_MAX_JUMP from it is dropped
    as a phantom. Without this a lone spurious blob (e.g. a speck at the bottom of the water
    minimap) could be returned as her position and FLIP the swim direction -- sending her off to a
    map edge in a positive-feedback runaway (real dot lost -> only the phantom left). Landing was
    already phantom-safe (settle needs CONSECUTIVE in-band reads); this makes DIRECTION safe too.
    Without `near` it behaves exactly as before (largest plausible blob). stable_char stays the
    default everywhere else."""
    n = near if (near is not None and near[0] >= 0) else None
    if n is None:
        a = get_character_full()
        time.sleep(0.02)
        b = get_character_full()
    else:
        a = get_character_full(near=n)
        time.sleep(0.02)
        b = get_character_full(near=n)
        def _far(p):
            return p[0] >= 0 and (abs(p[0] - n[0]) > _SWIM_MAX_JUMP or abs(p[1] - n[1]) > _SWIM_MAX_JUMP)
        if _far(a):
            a = (-1, -1)
        if _far(b):
            b = (-1, -1)
    if a[0] < 0:
        return b
    if b[0] < 0:
        return a
    if abs(a[0] - b[0]) <= 12 and abs(a[1] - b[1]) <= 12:
        return ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
    return b


def minimap_scroll(prev_crop, cur_crop, min_resp=0.4):
    """Vertical scroll of the minimap TERRAIN between two full-minimap crops (from
    `_minimap`), via phase correlation with the yellow dot masked out so only terrain
    drives it. Returns (dy, resp): dy > 0 == the character ROSE (calibrated on a known
    ascent); resp is the 0..1 correlation confidence. The minimap scrolls to keep the
    character centered, so in the map's middle the dot is pinned (~95,136) while the
    terrain scrolls -- this recovers climb progress the pinned dot cannot show."""
    def _prep(mm):
        hsv = cv2.cvtColor(mm, cv2.COLOR_BGR2HSV)
        dot = cv2.inRange(hsv, np.array([18, 120, 120]), np.array([45, 255, 255]))
        g = cv2.cvtColor(mm, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g[dot > 0] = 0                                    # drop the dot -> terrain only
        return g * cv2.createHanningWindow((g.shape[1], g.shape[0]), cv2.CV_32F)
    (_sx, sy), resp = cv2.phaseCorrelate(_prep(prev_crop), _prep(cur_crop))
    return (sy if resp >= min_resp else 0.0), resp


def focus():
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, TITLE)
    if not hwnd:
        return False
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    gt = u.GetWindowThreadProcessId(hwnd, None)
    u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)
    u.AttachThreadInput(cur, gt, True)
    u.ShowWindow(hwnd, 9); u.BringWindowToTop(hwnd); u.SetForegroundWindow(hwnd)
    u.AttachThreadInput(cur, gt, False)
    time.sleep(0.3)
    return u.GetForegroundWindow() == hwnd


def stable_char(n=3):
    """Robust position: take reads and return the center of the DENSEST cluster, so
    scattered buff-glow phantoms are rejected even if a few slip past the color filter
    (the real position recurs; phantoms are scattered). (-1,-1) if none detect."""
    reads = []
    for _ in range(max(n, 4)):                     # a few extra so a cluster can form
        x, y = get_character_full()
        if x >= 0:
            reads.append((x, y))
        time.sleep(0.05)
    if not reads:
        return -1, -1
    best, best_n = reads, -1
    for r in reads:                                # densest cluster within 12px
        cl = [q for q in reads if abs(q[0] - r[0]) <= 12 and abs(q[1] - r[1]) <= 12]
        if len(cl) > best_n:
            best_n, best = len(cl), cl
    xs = sorted(c[0] for c in best); ys = sorted(c[1] for c in best)
    return xs[len(xs) // 2], ys[len(ys) // 2]


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
    releases keys and returns False so the caller degrades to panic. F8 (kb.pause) aborts."""
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


def _apply_swim_keys(want):
    """Hold exactly the arrow keys in `want`; release the rest."""
    for name, key in _ARROW.items():
        if name in want:
            kb.safe_press(key)
        else:
            kb.safe_release(key)


def swim_to(target_x, target_y, tol=(3, 3), cap=8.0, locate=None, jump=True,
            jump_burst=4, jump_gap=0.04, axis="xy", settle=2, verbose=False, label="",
            start_near=None):
    """Swim toward (target_x, target_y) until within `tol` on both axes or `cap` seconds.
    Returns True on arrival. F8 (kb.pause) aborts. `locate` (default get_character_full)
    is injectable for tests.

    LANDING: arrival requires `settle` CONSECUTIVE in-band reads (not one), and no jump is
    fired while confirming -- so she must be RESTING on the platform, not just passing
    through the target y mid-jump-arc. If she isn't seated she sinks back out of the band,
    the streak resets, and she jumps again. This is what makes her actually LAND (critical
    for the stacked P4 pin, where a momentary y-touch used to count as 'arrived').

    Water-world movement: you RISE by JUMPING repeatedly (holding Up does nothing), so when
    the target is above we spam JUMP and NEVER press Up. Left/right use arrow keys; descending
    just sinks. The position read (stable_char) is slow (~0.28s), so ONE jump per loop caps
    the rate at ~3/s; instead we fire a BURST of hops per read, scaled by the gap: far below
    -> up to `jump_burst` quick hops; near the target -> a single gentle hop (no overshoot).
    `jump_gap` = seconds between hops in a burst.

    `axis="x"` reaches the target X only (ignores Y, no jump) -- used by the reset to reach
    the rightmost open column without fighting the descent.

    Uses the fast two-sample swim_read by default (travel only needs a direction and the loop
    re-reads every iteration); landing robustness comes from `settle` CONSECUTIVE in-band reads,
    not from any single read, so a scattered phantom at a bad spot (e.g. P6's far-right edge)
    can't false-land -- it just costs one self-correcting iteration."""
    locate = locate or swim_read
    t0 = time.time()
    settle_hits = 0
    # Seed the phantom anchor with her DEPARTURE position: the very first read otherwise has no
    # `near`, so if her real dot is momentarily absent (mid fall/transition) the read falls back to
    # the largest blob -- which can be a fixed minimap phantom -- and then near LOCKS onto it. With
    # the departure seed, that first phantom is rejected as an implausible jump and she waits for a
    # real read instead of chasing it off-map.
    last = start_near if (start_near is not None and start_near[0] >= 0) else (-1, -1)
    _pfx = f"[swim {label}]" if label else "[swim]"

    def _arrived(x, y):
        if verbose:
            dbg(f"{_pfx} ARRIVED at ({x},{y}) tgt ({target_x},{target_y}) "
                f"gap dx={x - target_x} dy={y - target_y}")
    try:
        while time.time() - t0 < cap:
            if kb.pause:
                return False
            lie_check_fast_tick()
            # Anchor the read to her last known position so a phantom blob can't hijack direction.
            # Only the default swim_read takes `near`; injected locates (tests) stay zero-arg.
            x, y = swim_read(near=last) if locate is swim_read else locate()
            if x is None or x < 0:
                time.sleep(0.04); continue
            # Arrival needs her AT the target AND RESTING: if y is still rising she is falling
            # THROUGH the band (mid jump-arc / sinking), not seated -- that must not conclude arrival.
            sinking = last[1] >= 0 and y > last[1] + 0.5
            last = (x, y)
            want = watermap.swim_keys((x, y), (target_x, target_y), tol)
            if verbose:
                dbg(f"{_pfx} at ({x},{y}) tgt ({target_x},{target_y}) dx={x - target_x} "
                    f"dy={y - target_y} want={sorted(want)} sinking={sinking} hits={settle_hits}")
            if axis == "x":                               # horizontal only; arrived when x holds AND resting
                horiz = want & {"left", "right"}
                if not horiz:                             # x within tol -> confirm it HOLDS and she's not
                    settle_hits = settle_hits + 1 if not sinking else 0   # sinking through -> not landed;
                    if settle_hits >= settle:            # a single in-tol read (overshoot bounce) or a
                        _arrived(x, y); return True      # fall through the band must not stop her short
                    _apply_swim_keys(set())              # of the column. Release arrows, re-read to
                    time.sleep(0.05); continue           # double-confirm before declaring arrival.
                settle_hits = 0                           # drifted off target x -> reset the confirm streak
                _apply_swim_keys(horiz)
                time.sleep(0.05); continue
            if not want:                                  # in the target band -> confirm SEATED (resting)
                settle_hits = settle_hits + 1 if not sinking else 0   # in tol but still falling through
                if settle_hits >= settle:                             # -> not landed; needs to come to rest
                    _arrived(x, y); return True           # in tol AND not sinking `settle` reads -> landed
                _apply_swim_keys(set())                   # release arrows; DON'T jump -> let her settle
                time.sleep(0.08); continue
            settle_hits = 0                               # drifted out of band -> not landed yet
            # Water-world vertical: rise = jump (no Up), descend = just sink (no Down).
            # Only horizontal keys are ever held.
            _apply_swim_keys(want - {"up", "down"})
            if jump and "up" in want:
                # BURST of hops per read, scaled by the vertical gap: far below -> jump_burst
                # quick hops; near the target -> 1 gentle hop (don't overshoot up).
                dy = y - target_y
                span = 30.0
                frac = max(0.0, min(1.0, (dy - tol[1]) / span))
                hops = 1 + int(round(frac * (jump_burst - 1)))
                for _ in range(hops):
                    if kb.pause:
                        break
                    kb.safe_press(JUMP); time.sleep(0.02); kb.safe_release(JUMP)
                    time.sleep(jump_gap)
                continue                                   # burst paced this iter -> re-read now
            time.sleep(0.05)
        if verbose:
            dbg(f"{_pfx} CAP {cap:.0f}s at {last} tgt ({target_x},{target_y}) "
                f"gap dx={last[0] - target_x} dy={last[1] - target_y} (never seated in tol)")
        return False
    finally:
        for key in _ARROW.values():
            kb.safe_release(key)
        kb.safe_release(JUMP)


def sink_to_bottom(bottom_y, cap=15.0, locate=None, settle=4, near=35):
    """Straight descent (no direction held): release ALL keys and let her SINK. Returns True once
    she LANDED after sinking -- was sinking, then stopped for `settle` reads AND is within `near`
    px of `bottom_y`. Used for start_sink (she's already over the drop). The RESET uses
    hold_to_bottom instead (it must WALK to the rightmost drop first). F8 aborts."""
    locate = locate or get_character_full
    kb.safe_release_all()
    t0 = time.time()
    prev_y = None
    still = 0
    sank = False
    while time.time() - t0 < cap:
        if kb.pause:
            return False
        lie_check_fast_tick()              # unified safety scan: lie-check + another-player
        x, y = locate()
        if y is not None and y >= 0:
            if prev_y is not None:
                if y > prev_y + 0.5:                      # still descending
                    sank = True
                    still = 0
                else:
                    still += 1
                    if sank and still >= settle and y >= bottom_y - near:
                        return True                       # sank, then landed near the bottom
            prev_y = y
        time.sleep(0.1)
    return True


def hold_to_bottom(bottom_y, key, cap=15.0, near=35, settle=3):
    """Reset descent by WALKING: HOLD `key` continuously (toward the rightmost drop) the whole
    way -- she walks off the edge and falls, and holding never lets her stop short of the drop
    column the way a tol'd align could. No release on minor y wobble (that made her stutter:
    walk-stop-walk-stop). ARRIVED requires BOTH: she's at the target y (within `near` of
    `bottom_y`) AND she is NOT still sinking (y didn't rise since the last read) -- so a fast fall
    passing THROUGH the band can't conclude arrival; she must have come to REST on the bottom
    platform, for `settle` consecutive reads. The `near` band also rejects a far minimap phantom
    (e.g. y~347, well below the bottom platform). Releases `key` on exit. F8 aborts."""
    kb.safe_release_all()
    kb.safe_press(key)
    t0 = time.time()
    hits = 0
    prev_y = None
    try:
        while time.time() - t0 < cap:
            if kb.pause:
                return False
            lie_check_fast_tick()
            x, y = get_character_full()
            valid = y is not None and y >= 0
            in_band = valid and abs(y - bottom_y) <= near
            resting = valid and prev_y is not None and y <= prev_y + 0.5   # not sinking (y not rising)
            hits = hits + 1 if (in_band and resting) else 0
            if valid:
                prev_y = y
            if dbg_on():
                dbg(f"[reset-hold] at ({x},{y}) tgt_bottom={bottom_y} in_band={in_band} "
                    f"resting={resting} hits={hits}")
            if hits >= settle:
                return True                               # at the bottom platform's y AND resting
            time.sleep(0.1)
        if dbg_on():
            dbg(f"[reset-hold] CAP {cap:.0f}s -- never settled at bottom (last y read above)")
        return True
    finally:
        kb.safe_release(key)


def water_shoot(center, seconds, count_fn=None, threshold=1, tol=(3, 3),
                check_every=0.5, debounce=2, attack_key='c'):
    """Park at a platform center and hold the attack key for a beat; if knocked off, swim
    back. With `count_fn` (fast single-frame YOLO count), returns DEPLETED as soon as
    `debounce` consecutive reads fall below `threshold`. Returns True otherwise / False
    if paused out."""
    cx, cy = center
    swim_to(cx, cy, tol=tol, cap=6.0)
    _face_right(0.04)
    t0, nextc, low = time.time(), time.time() + check_every, 0
    kb.safe_press(attack_key)                             # HOLD attack (continuous)
    try:
        while time.time() - t0 < seconds:
            if kb.pause:
                return False
            lie_check_fast_tick()
            # Phantom-safe: anchor the read to the parked center and reject a blob that leaps far
            # from it (buff-glow speck). A bare read let one phantom fire a false "drifted off" ->
            # release attack + swim back, interrupting the beat. None -> keep firing (no false move).
            r = _plausible_read((cx, cy), max_jump=_SWIM_MAX_JUMP)
            if r is not None and (abs(r[0] - cx) > tol[0] + 8 or abs(r[1] - cy) > tol[1] + 8):
                kb.safe_release(attack_key)               # drifted off -> swim back, resume
                swim_to(cx, cy, tol=tol, cap=4.0)
                _face_right(0.04); kb.safe_press(attack_key)
            if count_fn is not None and time.time() >= nextc:
                nextc = time.time() + check_every
                low, rotate = _reactive_deplete(low, count_fn(), threshold, debounce)
                if rotate:
                    return DEPLETED
            time.sleep(0.1)
        return True
    finally:
        kb.safe_release(attack_key)


def walk_shoot(node_mm_x, seconds, detect_fn, attack_key='c', half=60, tol=(3, 3),
               walk_step=0.25, deplete_reads=2, verbose=True, label=""):
    """Sweep a platform end-to-end firing -- NO screen player-anchor (minimap only, so it's
    robust to the damage-number/monster-bar anchor mess). Walks between [center-half,
    center+half] on the MINIMAP x, stopping to fire an attack burst each step (the attack
    roots the char anyway). Depletion = detect_fn finds no mob in the central screen band
    for `deplete_reads` consecutive sweep-ends. Returns DEPLETED / True (beat done) / False."""
    left, right = max(0, node_mm_x - half), node_mm_x + half
    target = right                                        # sweep toward the right first
    t0, empty_ends, last_log = time.time(), 0, [0.0]

    def log(msg):
        if verbose and time.time() - last_log[0] > 0.6:
            last_log[0] = time.time(); print(f"[walk {label}] " + msg)

    def burst(n=2):
        for _ in range(n):
            if kb.pause:
                return
            kb.safe_press(attack_key); time.sleep(0.3); kb.safe_release(attack_key)

    def central_clear():
        f = capture()
        if f is None:
            return False
        H, W = f.shape[:2]
        band = (int(W * 0.12), int(H * 0.34), int(W * 0.88), int(H * 0.80))
        return len(detect_fn(f, band)) == 0

    try:
        while time.time() - t0 < seconds:
            if kb.pause or STOP.is_set():
                return False
            lie_check_fast_tick()
            x, _y = stable_char(2)                         # minimap x (robust)
            if x < 0:                                      # no read -> fire in place, retry
                burst(1); continue
            if abs(x - target) <= tol[0] + 3:              # reached an end -> fire + reverse
                burst(2)
                target = left if target == right else right
                if central_clear():                        # depletion check at the sweep end
                    empty_ends += 1
                    log(f"sweep end clear ({empty_ends}/{deplete_reads})")
                    if empty_ends >= deplete_reads:
                        return DEPLETED
                else:
                    empty_ends = 0
                continue
            key = Key.right if target > x else Key.left    # step toward the end, then fire
            log(f"mmx={x} -> {'right' if target > x else 'left'} to {target}")
            kb.safe_press(key); time.sleep(walk_step); kb.safe_release(key)
            burst(1)
        return True
    finally:
        kb.safe_release_all()


def approach_shoot(seconds, detect_fn, anchor,
                   attack_range=110, band=70, step=0.14, attack_key='c',
                   deplete_reads=4, stall_limit=8, verbose=True, label="",
                   mm_bounds=None, mm_y=None, fall_margin=22, fall_check_every=0.9,
                   attack_keys=None, priority_class=None, priority_range=None,
                   priority_hold_hits=0, priority_lock_grace=0, fall_confirm=2, ease_margin=45,
                   continuous_attack=False, attack_dwell=0.25, face_deadzone=15,
                   face_settle=0.2, fire_stall_limit=5, lock_switch_px=70, combined=None):
    """Close-range farming for a beat: repeatedly locate the player (HP-bar anchor) and
    the nearest SAME-PLATFORM mob (its box-bottom near the player's feet), walk toward it
    (facing it) and attack; fire in place once within `attack_range` px. Returns DEPLETED
    when no mob is on the platform, True after the beat, False if paused.

    `detect_fn(frame, roi) -> [(score, x, y, w, h)]` is the detector (YOLO or template) --
    approach is detector-agnostic. Screen-space: mob x and player x come from the same
    frame, so 'which way to walk' is their x difference. `band` = how close a mob's feet
    must be to the player's feet (px) to count as the same platform."""
    import player as _player
    t0 = time.time()
    empty_reads = 0
    nonempty_streak = 0     # consecutive frames WITH a same-platform mob (debounces deplete reset)
    best_absdx = None       # closest we've gotten to the current target (net-progress stall)
    no_improve = 0
    last_player = None      # sticky anchor: lock onto the bar nearest last frame's player
    pri_lock_pos = None     # last screen pos of the engaged priority target (fishhouse)
    pri_miss = 0            # consecutive reads the priority target has been missing (occlusion debounce)
    pri_engaged = None      # x of the fishhouse last hit -> post-kill hold fires when it DISAPPEARS
    _prev_tx = None         # last chosen target x -> flag target SWITCHES in the debug trace
    rooted = False          # True only while firing in place -> the skill roots her, so a lost
                            # HP bar means she hasn't moved (assume last pos). While WALKING she
                            # IS moving, so a stale pos would overshoot + false-stall -> never fake it.
    held = [None]           # currently-held walk key -> HELD across frames for smooth motion
    _ha = [None]            # currently-held ATTACK key (continuous_attack) -> released on a move
    _faced = [None]         # direction she's currently facing -> a side change is a new decision
    _fire_stall = 0         # consecutive in-range beats on the SAME target that isn't dying
    _last_fire_tx = None    # target x of the last in-range beat (to detect a persistent target)
    last_log = [0.0]
    last_fall = [t0]        # throttle the minimap fall check (stable_char is not free)
    fall_streak = 0         # consecutive out-of-band fall reads (debounce: bottom-edge phantoms)

    def log(msg):
        if verbose and time.time() - last_log[0] > 0.6:
            last_log[0] = time.time()
            print(f"[approach {label}] " + msg if label else "[approach] " + msg)

    def stop_walk():
        if held[0] is not None:
            kb.safe_release(held[0]); held[0] = None

    def hold_attack(k):
        """Continuous attack: HOLD the key down (the game repeats the skill while held), only
        (re)pressing on a key change -- so attack uptime stays high across detection reads instead
        of the press/release burst's gaps. Released by release_attack() on a non-attack decision."""
        if _ha[0] != k:
            if _ha[0] is not None:
                kb.safe_release(_ha[0])
            kb.safe_press(k); _ha[0] = k

    def release_attack():
        if _ha[0] is not None:
            kb.safe_release(_ha[0]); _ha[0] = None

    def walk(key):
        """Hold `key` continuously (only (re)press on a direction change) so motion doesn't
        stutter between detection frames."""
        if held[0] != key:
            if held[0] is not None:
                kb.safe_release(held[0])
            kb.safe_press(key); held[0] = key
        _faced[0] = key                       # walking that way faces her that way

    try:
        while time.time() - t0 < seconds:
            if kb.pause:
                return False
            lie_check_fast_tick()          # unified safety scan: lie-check + another-player
            # FALL check: she can be knocked (or walk) OFF the platform mid-beat; the screen-space
            # anchor keeps "farming" the platform below. Read her minimap y (robust, densest-cluster
            # so a phantom can't trip it) and if she has dropped well past the platform band, bail so
            # the caller RE-SEATS her instead of farming the wrong platform.
            if mm_y is not None and time.time() - last_fall[0] >= fall_check_every:
                last_fall[0] = time.time()
                _mx, _my = stable_char(2)
                if _my >= 0 and _my > mm_y + fall_margin:
                    # DEBOUNCE: a lone out-of-band read is usually a bottom-edge minimap phantom
                    # (or a jump/knock arc), not a real fall -- re-seating on it wrecks stacked
                    # platforms like P3. Only bail after `fall_confirm` consecutive confirmations.
                    fall_streak += 1
                    if fall_streak >= fall_confirm:
                        log(f"FELL off platform (y={_my} > {mm_y}+{fall_margin}, x{fall_streak}) -> re-seat")
                        kb.safe_release_all()
                        return FELL
                    log(f"fall read {fall_streak}/{fall_confirm} (y={_my}) -- confirming before re-seat")
                else:
                    fall_streak = 0                            # back in band -> reset the streak
            f = capture()
            if f is None:
                time.sleep(0.1); continue
            _shared_dets = None
            if combined is not None:                      # shared single pass: mobs + player box
                _shared_dets, _pbox = combined(f, anchor.last)   # ONE inference this beat...
                anchor.push(_pbox)                        # ...its player box feeds the anchor (falls
                #                                            back to nametag when None, as before)
            p = anchor.locate(f)                          # HP-bar/name-tag anchor
            if p is None:
                if last_player is None:                   # never locked yet -> brief blind attack
                    log("player NOT found (no prior lock) -> blind attack")
                    stop_walk()
                    kb.safe_press(attack_key); time.sleep(0.3); kb.safe_release(attack_key); continue
                if not rooted:                            # she was WALKING (moving): her last pos is
                    # already stale -- faking it overshoots the mob and trips the stall guard. Stop
                    # and wait a beat for the bar to re-appear (no skill VFX while walking, so brief).
                    log("player lost mid-walk -> stop, wait for re-lock")
                    stop_walk(); time.sleep(0.03); continue
                # Rooted: the attack SKILL's VFX hides the bar but also holds her still -- she hasn't
                # moved -- so assume her last position and keep firing.
                p = last_player
                log(f"player NOT found (rooted) -> assume last position {p}")
            last_player = p
            if getattr(anchor, "which", None):            # composite fell past the primary anchor
                log(f"anchor fallback #{anchor.which} (nametag) located player at {p}")
            px, pfeet = p
            # Scan the FULL platform-width strip at the player's y-band (YOLO is cheap on the
            # whole frame). Catches far mobs (P6's rightmost fishhouse) without any patrol.
            H, W = f.shape[:2]
            # scan a strip that spans the full same-platform band both ways (+margins), so a
            # wider band actually reaches mobs above AND below her feet.
            strip = (0, max(0, pfeet - band - 40), W, min(H, pfeet + band // 2 + 40))

            def same_platform(dets):
                # A detection is (score,x,y,w,h) or (score,x,y,w,h,cls_name) -> carry the class
                # (or None) through so the attack key can be chosen per mob. `d[:5]` handles both.
                out = []
                for d in dets:
                    _s, mx, my, mw, mh = d[:5]
                    if abs((my + mh) - pfeet) <= band:
                        out.append((mx + mw // 2, my + mh, d[5] if len(d) > 5 else None))
                return out

            dets = _shared_dets if _shared_dets is not None else detect_fn(f, strip)
            same = same_platform(dets)
            if dbg_on():                                   # full per-frame trace (file only)
                _md = [(round(d[0], 2), d[1] + d[3] // 2, d[2] + d[4]) for d in dets]
                dbg(f"[app {label}] player=({px},{pfeet}) band={band} "
                    f"dets(score,cx,feet)={_md} same_platform={[(m[0], m[2]) for m in same]}")
            # DEATH-TRIGGERED post-kill hold: if the fishhouse we were just hitting is GONE now
            # (killed -- reliable at ~0.99 recall), blanket its spot for priority_hold_hits to catch
            # the goby that spawn there. Fires ONCE at the kill, not on every attack beat.
            if pri_engaged is not None and priority_hold_hits and priority_class and not continuous_attack:
                if not any(m[2] == priority_class and abs(m[0] - pri_engaged) <= band for m in same):
                    log(f"fishhouse killed (x~{pri_engaged}) -> post-kill hold +{priority_hold_hits}")
                    stop_walk()
                    for _ in range(priority_hold_hits):
                        if kb.pause:
                            break
                        kb.safe_press(attack_key); time.sleep(0.35); kb.safe_release(attack_key)
                        time.sleep(0.05)
                pri_engaged = None
            # Locked fishhouse occluded mid-kill (our own AoE VFX / a passing bone fish hides the WHOLE
            # platform): DON'T count it as depletion -- that abandons the fishhouse she's killing (the
            # #1 "gives up while attacking" case). Bridge it via the priority hold below (fires the lock
            # spot). Only genuine, unlocked emptiness advances the deplete counter.
            _lock_bridge = (priority_class and pri_lock_pos is not None and pri_miss < priority_lock_grace)
            if not same and not _lock_bridge:
                stop_walk(); release_attack(); rooted = False   # platform empty -> stop attacking too
                nonempty_streak = 0
                empty_reads += 1                          # DEBOUNCED: several empty frames -> clear
                feet = [d[2] + d[4] for d in dets]
                log(f"no same-platform mob ({empty_reads}/{deplete_reads}); "
                    f"pfeet={pfeet} band={band} detected feet={feet}")
                if empty_reads >= deplete_reads:          # debounced empty -> platform done
                    return DEPLETED
                time.sleep(0.12); continue
            # Only a SUSTAINED detection (>=2 consecutive frames) clears deplete progress -- a lone
            # flaky frame no longer resets the tail, so a near-empty platform depletes sooner.
            nonempty_streak += 1
            if nonempty_streak >= 2:
                empty_reads = 0
            # Target priority: prefer the priority_class (the fishhouse that SPAWNS the goby
            # burst) so she stands ON it when it dies -- the self-centered AoE then catches the
            # 6 goby the instant they pop, stacked, instead of chasing them one-by-one as they
            # scatter. No priority mob present -> nearest same-platform mob.
            pri = [m for m in same if m[2] == priority_class] if priority_class else []
            holding = False
            if pri:                                        # priority target visible -> commit to ONE
                # Pick the fishhouse nearest the EXISTING lock, not nearest px -- the player anchor
                # jumps hundreds of px between reads, and keying off px made "nearest" flip between
                # fishhouses so she abandoned the one she was killing. Fishhouses are stationary, so
                # matching to the lock keeps her on the same physical target until it dies.
                ref = pri_lock_pos[0] if pri_lock_pos is not None else px
                cand = min(pri, key=lambda m: abs(m[0] - ref))
                if (pri_lock_pos is not None and abs(cand[0] - ref) > lock_switch_px
                        and pri_miss < priority_lock_grace):
                    # The locked fishhouse isn't in this frame and the nearest one is a DIFFERENT,
                    # far fishhouse (P3 has two ~930px apart; the model sees one at a time). Don't
                    # oscillate to it -- HOLD the locked one (walk to it, it's stationary) until it
                    # re-appears/dies, else she thrashes back and forth across the platform.
                    tx, _tfy = pri_lock_pos; tcls = priority_class; holding = True
                else:
                    tx, _tfy, tcls = cand
                    pri_lock_pos = (tx, _tfy); pri_miss = 0
            elif (priority_class and pri_lock_pos is not None and pri_miss < priority_lock_grace):
                # Priority target BLINKED OUT (a bone fish swam through / our own AoE VFX) -- not
                # necessarily dead. Stay committed to its last position instead of switching to a
                # goby and leaving the fishhouse half-killed. The grace only ticks down while she's
                # IN RANGE firing the spot (below) -- an occlusion while still WALKING in hasn't
                # confirmed anything, so it must not burn the grace before she lands a hit. Once she
                # HAS been firing the spot and it still doesn't reappear, it's dead -> lock clears
                # and the goby it spawned become the target.
                tx, _tfy = pri_lock_pos; tcls = priority_class; holding = True
            else:
                pri_lock_pos = None                        # no lock (or gave up) -> nearest same-platform
                tx, _tfy, tcls = min(same, key=lambda m: abs(m[0] - px))
            # TARGET trace (file only): why she picked this target, and flag SWITCHES so target
            # changes are diagnosable. mode=lock (fishhouse, nearest the lock), hold (occluded
            # fishhouse), nearest (goby / no fishhouse). fh=candidate fishhouse x's.
            if dbg_on():
                _mode = "lock" if pri else ("hold" if holding else "nearest")
                _fh_xs = sorted(int(m[0]) for m in pri)
                _sw = "" if (_prev_tx is None or abs(tx - _prev_tx) <= 20) else f" SWITCH<-{_prev_tx}"
                dbg(f"[tgt {label}] mode={_mode} tx={int(tx)} cls={tcls} px={px} "
                    f"lock={None if pri_lock_pos is None else int(pri_lock_pos[0])} miss={pri_miss} "
                    f"fh={_fh_xs} goby={sum(1 for m in same if m[2] != priority_class)}{_sw}")
            _prev_tx = tx
            dx = tx - px
            key = Key.right if dx >= 0 else Key.left
            akey = (attack_keys or {}).get(tcls, attack_key)   # per-mob skill; falls back to default
            # Tighter close-in on the priority target: get point-blank on the fishhouse so the
            # goby spawn inside the AoE, not at its (wider) normal firing standoff.
            _rng = priority_range if (priority_range and tcls == priority_class) else attack_range
            if abs(dx) <= _rng:                           # in range: face + fire a burst
                if holding:                                # occluded AND firing the spot -> real evidence
                    pri_miss += 1                          # it's gone; count only in-range misses
                    log(f"priority occluded ({pri_miss}/{priority_lock_grace}) -> hold+fire last pos {tx}")
                best_absdx = None; no_improve = 0
                rooted = True                             # firing in place -> assume-last-pos is valid
                log(f"IN RANGE dx={dx} px={px} -> attack '{akey}' ({tcls}, same={len(same)})")
                stop_walk()
                if continuous_attack:
                    # A SIDE change is a different decision: if the mob is now on the opposite side
                    # of where she faces, drop the held attack (so the turn registers -- a facing tap
                    # is swallowed while the attack key is down), face the new side, then resume
                    # holding. Otherwise keep the attack held. A deadzone ignores dx jitter near 0.
                    if _faced[0] != key and abs(dx) > face_deadzone:
                        # A facing tap is SWALLOWED while the attack animation is still playing (she
                        # keeps attacking the old side). Drop attack, let the cast clear (face_settle),
                        # THEN turn with a firm tap, then resume. Without this she never actually turns.
                        release_attack()
                        time.sleep(face_settle)
                        kb.safe_press(key); time.sleep(0.12); kb.safe_release(key); _faced[0] = key
                    elif _faced[0] is None:
                        kb.safe_press(key); time.sleep(0.12); kb.safe_release(key); _faced[0] = key
                    hold_attack(akey)                     # HOLD -> keeps attacking through re-detect
                    time.sleep(attack_dwell)              # short dwell, then re-decide (still held)
                    # FIRE-STALL: the same in-range target persisting (not dying) means the turn was
                    # swallowed again OR it's out of reach -> force a fresh re-face next beat.
                    if _last_fire_tx is not None and abs(tx - _last_fire_tx) <= 30:
                        _fire_stall += 1
                    else:
                        _fire_stall = 0
                    _last_fire_tx = tx
                    if _fire_stall >= fire_stall_limit:
                        log(f"fire-stall at dx={dx} (target not dying) -> force re-face")
                        _faced[0] = None; _fire_stall = 0
                else:
                    kb.safe_press(key); time.sleep(0.03); kb.safe_release(key)   # face the target
                    for _ in range(3):                    # burst: several hits before re-evaluating
                        kb.safe_press(akey); time.sleep(0.35); kb.safe_release(akey)
                        time.sleep(0.05)
                if priority_class and tcls == priority_class:
                    pri_engaged = tx        # remember the fishhouse we hit; hold fires when it DIES
            else:                                         # nearest visible mob is OUT of range
                release_attack()                          # moving now -> the skill roots her, drop attack
                # NET-progress stall: give up (advance) only if we stop getting CLOSER (best
                # |dx| not improving) for stall_limit frames -- tolerates jitter + slow approach,
                # and escapes a genuinely unreachable mob so the platform can't hang.
                if best_absdx is None or abs(dx) < best_absdx - 4:
                    best_absdx = abs(dx); no_improve = 0
                else:
                    no_improve += 1
                if no_improve >= stall_limit:
                    log(f"stalled at dx={dx} (best={best_absdx}, unreachable) -> advance")
                    return DEPLETED
                # WALK ONLY -- do NOT attack while approaching: the attack skill roots her in
                # place, so firing mid-walk cancels her movement and she never closes in.
                # MINIMAP BOUND: navigation stays on the minimap. Never step OFF the platform
                # even if the (noisy) anchor says so -- fire in place at the edge instead. This
                # stops a bad anchor from walking her off-platform and breaking the next swim_to.
                if mm_bounds is not None:
                    mmx, _mmy = stable_char(2)
                    if mmx >= 0 and ((key == Key.right and mmx >= mm_bounds[1]) or
                                     (key == Key.left and mmx <= mm_bounds[0])):
                        log(f"platform edge (mmx={mmx} bounds={mm_bounds}) -> fire in place")
                        stop_walk(); rooted = True         # firing in place at the edge
                        kb.safe_press(akey); time.sleep(0.3); kb.safe_release(akey)
                        continue
                # CONTINUOUS walk: hold the key across frames (only re-press on a turn), so
                # motion is smooth instead of stutter-stepping between detections.
                log(f"dx={dx} px={px} -> walk {'right' if dx > 0 else 'left'} "
                    f"(same={len(same)}, best={best_absdx}, noimp={no_improve})")
                rooted = False                            # moving now -> a lost bar must NOT be faked
                # EASE-IN: once she's about to arrive, TAP-and-release instead of holding. The
                # detection read is ~0.7s, so a held key keeps her swimming through that whole read
                # and she COASTS PAST a tight target (then has to turn back -- the overshoot). A
                # short nudge can't overshoot far, and she re-reads from near a standstill.
                if abs(dx) <= _rng + ease_margin:
                    kb.safe_press(key); time.sleep(0.05); kb.safe_release(key); held[0] = None; _faced[0] = key
                else:
                    walk(key)                             # far -> hold continuously for smooth travel
                    if step > 0:                          # 0 = no pacing, run at compute speed
                        time.sleep(step)
        return True
    finally:
        release_attack()                          # drop any held (continuous) attack key
        kb.safe_release(attack_key)
        kb.safe_release(Key.left); kb.safe_release(Key.right)


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


def _reactive_deplete(low_streak, count, threshold, debounce):
    """Debounce for the in-shoot count. A single-frame `count` below `threshold`
    extends `low_streak`; `debounce` consecutive lows -> rotate. Any healthy count,
    or a failed read (count is None), resets the streak. Returns (streak, rotate?)."""
    if count is not None and count < threshold:
        low_streak += 1
        return low_streak, low_streak >= debounce
    return 0, False


def _stand_shoot(node, seconds, count_fn=None, threshold=2, check_every=0.5, debounce=2):
    """STAND_SHOOT state: parked at home, fire in place for a beat. Returns False if
    she fell off the platform (walks back to home if knocked toward the right edge).

    If `count_fn` is given (a fast single-frame YOLO count) it is polled every
    `check_every`s while the attack is HELD; `debounce` consecutive reads below
    `threshold` return DEPLETED so the caller rotates immediately. The attack key
    stays held across the count capture, so counting never interrupts firing."""
    c = FARM_CTX[node]
    walk_to_x(c["home"], tol=2)                        # reposition to the LEFT home...
    _face_right(0.04)                                 # ...facing right (short tap = minimal drift off home)
    t0, last = time.time(), (c["home"], c["home_y"])
    next_check, low = t0 + check_every, 0
    while time.time() - t0 < seconds:
        if kb.pause:
            break
        kb.safe_press('c'); time.sleep(0.1)           # HOLD attack (continuous, not tapped)
        r = _plausible_read(last)                     # rejects buff-glow phantom reads
        if r is not None:
            last = r
            if r[1] >= c["fall_y"]:
                kb.safe_release_all(); print(f"[stand] fell to {r}"); return False
            if r[0] >= c["edge"]:                      # knocked past the RIGHT edge -> walk left home
                kb.safe_release('c')
                if not _walk_shoot(Key.left, c["home"], going_right=False,
                                   seed=(c["edge"], c["home_y"]), cap=c["cap"], fall_y=c["fall_y"]):
                    return False
                _face_right(); last = (c["home"], c["home_y"])
            elif r[0] <= c["left"]:                     # knocked past the LEFT boundary -> walk right home
                print(f"[stand] {node}: left-guard fired @ x={r[0]} (<= {c['left']}) -> walk right to {c['home']}")
                kb.safe_release('c')
                if not _walk_shoot(Key.right, c["home"], going_right=True,
                                   seed=(c["left"], c["home_y"]), cap=c["cap"],
                                   fall_y=c["fall_y"], edge_safety=c["edge"]):
                    return False
                _face_right(); last = (c["home"], c["home_y"])
        if count_fn is not None and time.time() >= next_check:
            next_check = time.time() + check_every
            low, rotate = _reactive_deplete(low, count_fn(), threshold, debounce)
            if rotate:
                kb.safe_release_all(); print(f"[stand] {node} depleted -> rotate"); return DEPLETED
        lie_check_fast_tick()      # cheap transparent-shape check while shooting (3s window)
    kb.safe_release_all()
    return True

def _walk_shoot_sweep(node):
    """WALK_SHOOT state: sweep right to x_far shooting, then back left to home, then
    face right. Returns False if she fell."""
    c = FARM_CTX[node]
    if not _walk_shoot(Key.right, c["far"], going_right=True, seed=(c["home"], c["home_y"]),
                       cap=c["cap"], fall_y=c["fall_y"], edge_safety=c["edge"]):
        return False
    if not _walk_shoot(Key.left, c["home"], going_right=False, seed=(c["far"], c["home_y"]),
                       cap=c["cap"], fall_y=c["fall_y"]):
        return False
    _face_right()
    return True


def farming_loop_nav(exp_check=None, enemy_check=None, panic=None,
                     stand_secs=(6, 8), break_every=(8 * 60, 15 * 60),
                     rest_range=(30, 120), skill_interval=(240, 300),
                     deplete_threshold=1, max_seconds=None, breaks=True, cfg=None):
    """Node-graph farming loop with a STAND/WALK state machine. Each iteration runs
    ONE state move on the current platform (STAND_SHOOT or WALK_SHOOT), then counts
    dragons at the resulting standstill; a low count (< deplete_threshold) rotates to
    the next platform via travel(), otherwise the states alternate. Recovery and
    rotation both go through navmap.travel(). Preserves EXP/red-dot safety, F8 pause,
    jittered breaks, and jittered skill/heal cadence. max_seconds bounds it."""
    import random as _r
    import monsters, navmap
    if cfg:                                    # per-map overrides (dragon_nest.json): tune rest cadence
        break_every = tuple(cfg.get("break_every", break_every))
        rest_range = tuple(cfg.get("rest_range", rest_range))
        breaks = cfg.get("breaks", breaks)
    if not focus():
        print("[nav] could not focus"); return
    print(f"[nav] state machine (stand/walk) + motion-based dragon counting"
          + (f" | breaks {int(rest_range[0])}-{int(rest_range[1])}s every "
             f"{int(break_every[0] / 60)}-{int(break_every[1] / 60)}min" if breaks else " | breaks OFF"))
    t_start = time.time()
    next_break = time.time() + _r.uniform(*break_every)
    next_skill = [time.time()]                    # cast skills from the first stint (as in split)
    current = "TOP_FARM"
    farm_state = navmap.STAND_SHOOT
    STOP.clear()
    STATUS["state"] = "farming"

    # Reactive deplete-check: with a YOLO model loaded we poll a fast single-frame
    # count WHILE shooting and rotate the instant the platform runs dry. Without a
    # model we fall back to the old motion count at the end of each move.
    model = monsters.load_dragon_model()
    reactive = model is not None
    if reactive:                                  # warm CUDA once so the first in-loop check is fast
        _f0 = capture()
        if _f0 is not None:
            monsters.detect_dragons_yolo(_f0, model, monsters.DEFAULT_MOTION_ROI)
    print(f"[nav] reactive deplete-check: {'YOLO (single-frame)' if reactive else 'off -> motion fallback'}")

    exp_tick = make_exp_tracker(label="nav")     # per-10-min EXP gain + running average
    STATUS["buff_at"] = None                      # fresh run (panel timers)
    active_tick, active_freeze = make_active_timer()  # ACTIVE farm time (excludes pauses)

    def one_count(nd):
        roi = monsters.MOTION_ROI_BY_NODE.get(nd, monsters.DEFAULT_MOTION_ROI)
        f = capture()
        c = None if f is None else len(monsters.detect_dragons_yolo(f, model, roi))
        STATUS["count"] = c
        return c

    def heal_skill():
        # Throttled to skill_interval (240-300s): the timed BUFFS A and J only. 'H' is
        # NOT here -- it is cast before every STAND_SHOOT (see cast_h), per user.
        if time.time() < next_skill[0]:
            return
        next_skill[0] = time.time() + _r.uniform(*skill_interval)
        kb.safe_press('a'); time.sleep(0.4); kb.safe_release('a')
        kb.safe_press('j'); time.sleep(0.4); kb.safe_release('j')
        STATUS["buff_at"] = time.time()               # panel buff timer

    def cast_h():
        # 'H' (heal/potion) before every STAND_SHOOT -- not throttled, per user request.
        kb.safe_press('h'); time.sleep(0.08); kb.safe_release('h')

    def go(dst):
        return navmap.travel(dst, locate_fn=_nav_locate, execute_fn=execute_edge)

    start_safety_monitor()                            # curse/monster runs off the action loop
    while True:
        now = time.time()
        if STOP.is_set():
            kb.safe_release_all(); active_freeze(now); STATUS["state"] = "idle"
            stop_safety_monitor(); print("[nav] STOP -> stop"); return
        if max_seconds is not None and now - t_start > max_seconds:
            kb.safe_release_all(); active_freeze(now); STATUS["state"] = "idle"
            stop_safety_monitor(); print("[nav] max_seconds -> stop"); return
        if kb.pause:
            kb.safe_release_all(); lie_check_silence(); active_freeze(now)
            STATUS["state"] = "paused"; STATUS["lie"] = False; time.sleep(0.1); continue
        STATUS["state"] = "farming"
        active_tick(now)                              # advance ACTIVE farm time
        lie_check_fast_tick()                         # FULL curse/monster now runs in the monitor thread
        STATUS["lie"] = is_lie_check_active()
        exp_tick()                                    # per-10-min EXP logging (no-op between samples)

        # locate; recover onto a farm node if off-map
        node = _nav_locate()
        STATUS["node"] = node
        if node is None:
            # Not in any mapped node band. Two cases: (a) a transient bad read (buff
            # glow, mid-fall) -> no clean position -> wait; or (b) a REACHABLE platform
            # we haven't added a node for -> a stable read. Never spin here: hand any
            # stable position to recover_to_farming (it has its own on-top / rope-top /
            # in-envelope / bail logic) and escape to safety only if it truly can't
            # climb out. This kills the whole "unmapped spot -> loop stops" bug class.
            x, y = stable_char(3)
            if x < 0:
                time.sleep(0.2); continue                 # no clean read -> tolerate, wait
            print(f"[nav] unmapped position ({x},{y}) -> recover to top")
            if recover_to_farming():
                current = "TOP_FARM"
            elif panic:
                print("[nav] unmapped & unrecoverable -> panic")
                panic(); time.sleep(1)
            else:
                time.sleep(1)                             # no panic hook -> avoid tight retry
            continue
        if node not in navmap.FARM_NODES:
            print(f"[nav] on {node} -> travel to {current}")
            if not go(current) and panic:
                panic(); time.sleep(1)
            continue

        # safety
        if enemy_check and enemy_check():
            print("[nav] another player -> ALARM + panic (F9 to silence)")
            enemy_alarm_on()
            if panic: panic()
            time.sleep(1); continue
        if exp_check and exp_check():
            print("[nav] EXP too low -> panic")
            if panic: panic()
            time.sleep(1); continue

        # scheduled break: drop to REST, rest, climb back
        if breaks and time.time() >= next_break:
            print("[nav] break -> rest on fallen platform")
            if drop_to_fallen():
                _idle_on_fallen(_r.uniform(*rest_range))
            recover_to_farming()
            next_break = time.time() + _r.uniform(*break_every)
            current = "TOP_FARM"
            continue

        # --- one state move of the farming state machine ---
        current = node
        if farm_state == navmap.WALK_SHOOT:
            ok = _walk_shoot_sweep(node)
        else:
            cast_h()                                     # 'H' before every STAND_SHOOT
            ok = _stand_shoot(node, _r.uniform(*stand_secs),
                              count_fn=(lambda: one_count(node)) if reactive else None,
                              threshold=deplete_threshold)
        if ok is False:
            farm_state = navmap.STAND_SHOOT          # fell -> re-locate/recover, reset state
            continue
        heal_skill()

        # decide: rotate when the platform is depleted, else alternate stand<->walk.
        if ok is DEPLETED:                            # STAND's in-shoot poll already saw it dry
            depleted = True
        elif reactive:
            # STAND stayed populated for the whole beat; give WALK one cheap end-of-sweep check.
            if farm_state == navmap.WALK_SHOOT:
                n = one_count(node)
                depleted = n is not None and n < deplete_threshold
            else:
                depleted = False
        else:
            # no model: motion count needs static frames -> release keys, take the median count
            kb.safe_release_all()
            n = monsters.count_dragons_best(capture, node)
            STATUS["count"] = n
            print(f"[nav] {node} {farm_state} dragons~{n}")
            depleted = n < deplete_threshold

        if depleted:
            target = navmap.next_farm_target(node, 0, threshold=deplete_threshold)
            if target:
                print(f"[nav] {node} depleted -> travel to {target}")
                if go(target):
                    current = target
            farm_state = navmap.STAND_SHOOT          # start fresh on the new platform
        else:
            farm_state = navmap.WALK_SHOOT if farm_state == navmap.STAND_SHOOT else navmap.STAND_SHOOT


CHAR_FIELDS = ("attack_key", "attack_keys", "buff_keys",
               "buff_interval_secs", "buff_settle_secs")


def load_char(path):
    """Load a chars/<name>.json character config. `path` may be a path string (read as
    UTF-8 JSON) or an already-loaded dict (returned unchanged)."""
    if isinstance(path, dict):
        return path
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_character(map_cfg, char_cfg):
    """Overlay a character's key settings onto a map config. Returns a SHALLOW COPY of
    `map_cfg` with each present CHAR_FIELDS value from `char_cfg` overriding the map's
    (precedence character -> map -> code default). A field absent or None in `char_cfg`
    leaves the map's value untouched. `char_cfg` falsy -> `map_cfg` returned unchanged."""
    if not char_cfg:
        return dict(map_cfg)  # always a copy, per the docstring's guarantee
    merged = dict(map_cfg)
    for k in CHAR_FIELDS:
        if char_cfg.get(k) is not None:
            merged[k] = char_cfg[k]
    return merged


def farming_loop_water(map_cfg, char=None, enemy_check=None, panic=None,
                       stand_secs=(6, 8), break_every=(8 * 60, 15 * 60),
                       rest_range=(30, 120), skill_interval=(240, 300),
                       deplete_threshold=1, max_seconds=None):
    """Water-map farming: swim between platform centers, fire in place, rotate when a
    platform runs dry. Reuses lie-check, YOLO dragon counting, breaks, skills, panic,
    STATUS/STOP. `map_cfg` is a maps/<name>.json path or a loaded config dict. `char` is an
    optional chars/<name>.json path, dict, or None; when set, its key overrides are merged
    onto map_cfg (character -> map -> code default precedence) before anything is read."""
    import random as _r
    import monsters
    if isinstance(map_cfg, str):
        map_cfg = watermap.load_map(map_cfg)
    if char is not None:                              # overlay this character's keys
        map_cfg = apply_character(map_cfg, load_char(char))
    _LAST_WATER_ATTACK_KEY[0] = map_cfg.get("attack_key", "c")
    set_minimap(*watermap.minimap_crop(map_cfg))
    set_enemy_threshold(map_cfg.get("enemy_threshold", 0.75))   # red-dot sensitivity (per map)
    if map_cfg.get("map_box"):                          # per-map valid dot box (reject phantoms)
        set_map_box(*map_cfg["map_box"])
    centers = watermap.node_centers(map_cfg)
    farm_nodes = map_cfg.get("farm_nodes") or list(centers)
    if not farm_nodes:
        print("[water] no farm nodes in map config"); return
    tol = watermap.swim_tol(map_cfg)
    # Jump burst for RISING between platforms: hops fired per position read (scaled by the
    # gap). Higher = faster rise (the slow read no longer caps the rate). P5->P4 wants snappy.
    _jb = int(map_cfg.get("swim_jump_burst", 4))
    _jg = float(map_cfg.get("swim_jump_gap", 0.04))    # seconds between hops in a burst (lower=faster)
    # debug_log: write a fine-grained per-frame trace to logs/debug_<ts>.log (file only, NOT
    # the console/panel), collecting what's needed to debug a run. Implies swim/approach traces.
    _debug = bool(map_cfg.get("debug_log", False))
    if _debug:
        _p = open_debug_log()
        print(f"[water] debug trace -> {_p}" if _p else "[water] debug trace: could not open file")
    _log_swim = _debug or bool(map_cfg.get("log_swim", False))   # swim pos vs target (-> debug file)
    if not focus():
        print("[water] could not focus"); return
    STOP.clear(); STATUS["state"] = "farming"
    print(f"[water] {map_cfg.get('name', '?')}: {len(centers)} nodes, order {farm_nodes}")

    # Detection: build ONE detect_fn(frame, roi) -> [(score,x,y,w,h)] used by BOTH approach
    # and depletion. 'mob_yolo' = trained YOLO (recommended), 'fish' = template match,
    # 'time' = no detection (rotate every beat), 'yolo' = legacy dragon model.
    detector = map_cfg.get("detector", "yolo")
    poll_in_shoot = False
    one_count = None
    detect_fn = None
    _count_roi = tuple(map_cfg["count_roi"]) if map_cfg.get("count_roi") else None

    if detector == "mob_yolo":
        import mob_detect
        _mm = mob_detect.load_yolo(map_cfg.get("mob_model", "models/mob_yolo.pt"))
        _mconf = float(map_cfg.get("mob_conf", 0.6))
        _mimg = int(map_cfg.get("mob_imgsz", 640))
        _cconf = {}                                        # per-class conf floor (fishhouse=0, goby=1)
        if map_cfg.get("fishhouse_conf") is not None:
            _cconf[0] = float(map_cfg["fishhouse_conf"])   # lower -> keep it through attack VFX
        if map_cfg.get("goby_conf") is not None:
            _cconf[1] = float(map_cfg["goby_conf"])
        _cconf = _cconf or None
        _wc = bool(map_cfg.get("attack_keys"))             # return the class name for per-mob skill

        def detect_fn(frame, roi):
            return mob_detect.yolo_boxes(_mm, frame, roi, _mconf, _mimg,
                                         class_conf=_cconf, with_class=_wc)
        print(f"[water] detector: mob_yolo conf={_mconf} imgsz={_mimg}"
              + (f" class_conf={_cconf}" if _cconf else "")
              + (f" attack_keys={map_cfg.get('attack_keys')}" if _wc else ""))
    elif detector == "fish":
        import fish as _fish
        _templates, _mode = _fish.templates_for(map_cfg)
        _thr = map_cfg.get("fish_threshold") or _fish.default_threshold(_mode)
        _ds = map_cfg.get("match_downscale", 0.5)

        def detect_fn(frame, roi):
            return _fish.scan(frame, _templates, roi=roi, threshold=_thr, downscale=_ds)["dets"]
        print(f"[water] detector: {_mode} templates x{len(_templates)} thr={_thr} ds={_ds}")
    elif detector == "time":
        print("[water] detector: time-based rotation (rotate every beat)")
    else:                            # legacy dragon yolo
        model = monsters.load_dragon_model()
        if model is not None:
            _f0 = capture()
            if _f0 is not None:
                monsters.detect_dragons_yolo(_f0, model, monsters.DEFAULT_MOTION_ROI)
            poll_in_shoot = True

            def one_count(node):
                roi = monsters.MOTION_ROI_BY_NODE.get(node, monsters.DEFAULT_MOTION_ROI)
                f = capture()
                c = None if f is None else len(monsters.detect_dragons_yolo(f, model, roi))
                STATUS["count"] = c
                return c
        print(f"[water] detector: {'dragon-YOLO' if one_count else 'none -> time'}")

    if detect_fn is not None and one_count is None:   # depletion count from detect_fn
        def one_count(node):
            f = capture()
            c = None if f is None else len(detect_fn(f, _count_roi))
            STATUS["count"] = c
            return c

    # Farming mode: 'walk_shoot' = minimap-only end-to-end sweep firing (no screen anchor,
    # robust); 'approach' = walk to the detected nearest mob (needs a stable player anchor);
    # else fixed-fire at the node center.
    attack_key = map_cfg.get("attack_key", "c")
    farm_mode = map_cfg.get("farm_mode", "approach" if map_cfg.get("approach") else "park")
    _phalf = int(map_cfg.get("platform_half", 60))    # minimap half-width to sweep for walk_shoot
    if farm_mode == "walk_shoot":
        if detect_fn is None:
            raise RuntimeError("walk_shoot needs detector 'mob_yolo' or 'fish'")
        print(f"[water] walk_shoot ON: detector={detector} platform_half={_phalf}")
    approach = farm_mode == "approach"
    _make_anchor = None
    if approach:
        if detect_fn is None:
            raise RuntimeError("approach needs detector 'mob_yolo' or 'fish'")
        import player as _p
        _arange = int(map_cfg.get("attack_range", 110))
        _aband = int(map_cfg.get("same_platform_band", 70))
        _astep = float(map_cfg.get("approach_step", 0.14))
        _adeplete = int(map_cfg.get("deplete_reads", 4))
        _astall = int(map_cfg.get("stall_limit", 8))
        if map_cfg.get("anchor") == "yolo_player":        # class-2 box of the unified mob YOLO
            if detector != "mob_yolo":
                raise RuntimeError("anchor 'yolo_player' needs detector 'mob_yolo'")
            import mob_detect as _md
            _pfoot = int(map_cfg.get("yolo_foot_offset", 0))   # HP-bar box bottom -> feet (~123px down)
            _pconf = float(map_cfg.get("yolo_player_conf", _mconf))   # player recall > mob (HP bar is small)
            _pgrace = int(map_cfg.get("yolo_stale_grace", 0))  # 0 -> approach_shoot governs misses (rooted vs walk)

            def _make_anchor():
                return _md.YoloPlayerAnchor(_mm, conf=_pconf, imgsz=_mimg, foot_offset=_pfoot,
                                            stale_grace=_pgrace,
                                            max_jump=map_cfg.get("player_anchor_max_jump"))
            print(f"[water] anchor: yolo_player conf={_pconf} (class 2 of {map_cfg.get('mob_model', 'models/mob_yolo.pt')})")
        elif map_cfg.get("anchor") == "nametag":          # KenYu-style name-tag anchor
            _tagw = _p.load_nametag(map_cfg["nametag_template"])
            _titlew = _p.load_nametag(map_cfg["title_template"]) if map_cfg.get("title_template") else None

            def _make_anchor():
                name = _p.NametagAnchor(_tagw, feet_offset=int(map_cfg.get("nametag_feet_offset", 6)),
                                        accept_thres=float(map_cfg.get("nametag_accept", 0.55)))
                if _titlew is None:
                    return name
                title = _p.NametagAnchor(_titlew, feet_offset=int(map_cfg.get("title_feet_offset", 40)),
                                         accept_thres=float(map_cfg.get("title_accept", 0.55)))
                return _p.CompositeAnchor([name, title])   # name tag first, 稱號 fallback
            print(f"[water] anchor: nametag ({map_cfg['nametag_template']})"
                  + (f" + title fallback ({map_cfg['title_template']})" if _titlew is not None else ""))
        else:
            def _make_anchor():
                return _p.HPBarAnchor(cfg=map_cfg.get("player"))
            print("[water] anchor: hpbar (sticky)")
        # Nametag FALLBACK: wrap the primary anchor so a lost primary lock (e.g. the attack
        # VFX covering the HP bar that yolo_player keys on) falls back to the name tag/title,
        # which sit BELOW the character and stay visible through the skill burst.
        _fb = map_cfg.get("nametag_fallback")
        if _fb and _fb.get("nametag_template"):
            _primary_make = _make_anchor
            _fb_tagw = _p.load_nametag(_fb["nametag_template"])
            _fb_titlew = _p.load_nametag(_fb["title_template"]) if _fb.get("title_template") else None

            def _make_anchor():
                anchors = [_primary_make(),
                           _p.NametagAnchor(_fb_tagw,
                                            feet_offset=int(_fb.get("nametag_feet_offset", 6)),
                                            accept_thres=float(_fb.get("nametag_accept", 0.55)))]
                if _fb_titlew is not None:
                    anchors.append(_p.NametagAnchor(_fb_titlew,
                                                    feet_offset=int(_fb.get("title_feet_offset", 40)),
                                                    accept_thres=float(_fb.get("title_accept", 0.55))))
                return _p.CompositeAnchor(anchors)          # primary first, nametag(+title) fallback
            print(f"[water] anchor fallback: nametag ({_fb['nametag_template']})"
                  + (f" + title ({_fb['title_template']})" if _fb_titlew is not None else ""))
        print(f"[water] approach ON: detector={detector} range={_arange} band={_aband}")

    # Buff groups: each key set fires on its OWN interval, so e.g. 'd' every 5min and a slow buff
    # 'G' every 15min run independently. Config: "buff_groups": [{"keys":["d"],"interval_secs":300},
    # {"keys":["G"],"interval_secs":900}]. Back-compat: no buff_groups -> one group from buff_keys /
    # buff_interval_secs (else the skill_interval range's low end).
    _groups = map_cfg.get("buff_groups")
    if _groups:
        buff_groups = [(list(g.get("keys", [])), float(g.get("interval_secs", 300))) for g in _groups]
    else:
        _bk = map_cfg.get("buff_keys", [])
        _bi = map_cfg.get("buff_interval_secs")
        buff_groups = [(list(_bk), float(_bi) if _bi else float(skill_interval[0]))] if _bk else []
    buff_groups = [(k, iv) for k, iv in buff_groups if k]      # drop empty key sets
    # per-group next-cast time; each first fires ~5s in (staggered 1s so they don't collide)
    _next_buff = [time.time() + 5.0 + i for i in range(len(buff_groups))]

    _buff_settle = float(map_cfg.get("buff_settle_secs", 0.6))   # wait out the attack root first
    _buff_gap = float(map_cfg.get("buff_gap_secs", 0.7))         # gap AFTER each cast so the next
    #                                                             buff isn't swallowed by its animation

    def heal_skill():
        now = time.time()
        due = [i for i in range(len(buff_groups)) if now >= _next_buff[i]]
        if not due:
            return
        # The attack skill ROOTS her (its animation eats a key pressed too soon), so drop all
        # keys and let the root clear BEFORE casting -- otherwise the buff press is swallowed.
        kb.safe_release_all()
        time.sleep(_buff_settle)
        for i in due:
            keys, iv = buff_groups[i]
            _next_buff[i] = now + iv
            print(f"[water] buff -> press {keys} (next in {int(iv)}s)")
            for k in keys:
                kb.safe_press(k); time.sleep(0.3); kb.safe_release(k)
                time.sleep(_buff_gap)          # let this cast's animation finish before the next
        STATUS["buff_at"] = time.time()

    # rotation: 'sweep' = farm the list in order (bottom->top) then reset via reset_node;
    # 'cyclic' (default) = farm current until depleted, advance to the next (wrapping).
    _ylift = int(map_cfg.get("node_y_lift", 5))          # aim this many px above node center
    _lift_override = map_cfg.get("node_lift_override", {})  # per-node lift (pin nodes -> 0)
    _arrive_jumps = map_cfg.get("node_arrive_jumps", {})   # extra hops after arriving (seat on pin)
    rotation = map_cfg.get("rotation", "cyclic")
    reset_node = map_cfg.get("reset_node")
    # Farm each platform until it's actually DEPLETED (no beat cap). node_max_secs is a
    # SAFETY only -- if a platform never clears (e.g. an unreachable edge mob), rotate after
    # this many seconds so the loop can't hang. 0 = no cap (farm until clear, may hang).
    _node_max = float(map_cfg.get("node_max_secs", 90))
    t_start = time.time()
    STATUS["buff_at"] = None                             # fresh run (panel timers)
    # ACTIVE farming time (excludes pauses) so total EXP / time / avg stay consistent.
    active_tick, _freeze_active = make_active_timer()
    next_break = [time.time() + _r.uniform(*break_every)]

    # EXP tracker: per-10-min gain + running average (see make_exp_tracker).
    exp_tick = make_exp_tracker(log_exp=map_cfg.get("log_exp", True),
                                sample_secs=float(map_cfg.get("exp_sample_secs", 10)),
                                window_secs=float(map_cfg.get("exp_window_secs", 600)),
                                max_exp_per_sec=float(map_cfg.get("exp_max_per_sec", 20000)))

    def guard():
        """Per-tick housekeeping. Returns 'stop' (return now), 'pause'/'skip'
        (continue the outer loop), or 'ok'."""
        now = time.time()
        if STOP.is_set():
            kb.safe_release_all(); _freeze_active(now); STATUS["state"] = "idle"; print("[water] STOP"); return "stop"
        if max_seconds is not None and now - t_start > max_seconds:
            kb.safe_release_all(); _freeze_active(now); STATUS["state"] = "idle"; print("[water] max_seconds"); return "stop"
        if kb.pause:
            kb.safe_release_all(); lie_check_silence(); _freeze_active(now)
            STATUS["state"] = "paused"; STATUS["lie"] = False; time.sleep(0.1); return "pause"
        STATUS["state"] = "farming"
        active_tick(now)                                   # advance ACTIVE farm time
        lie_check_fast_tick(); STATUS["lie"] = is_lie_check_active()   # FULL runs in the monitor thread
        exp_tick()
        if enemy_check and enemy_check():
            print("[water] another player -> ALARM + panic (F9 to silence)")
            enemy_alarm_on()
            if panic: panic()
            time.sleep(1); return "skip"
        return "ok"

    _breaks = map_cfg.get("breaks", True)              # False -> farm continuously (no rest)

    def take_break_if_due():
        if not _breaks or time.time() < next_break[0]:
            return
        print("[water] break -> swim to base and idle")
        swim_to(*centers[farm_nodes[0]], tol=tol, cap=12.0, jump_burst=_jb)
        end = time.time() + _r.uniform(*rest_range)
        while time.time() < end and not kb.pause and not STOP.is_set():
            lie_check_fast_tick(); time.sleep(0.5)
        next_break[0] = time.time() + _r.uniform(*break_every)

    # Stacked pair: P3 sits directly ABOVE P4 and reads the same minimap y, so swim_to can't
    # move between them. When we reach the UPPER node right after its lower partner, jump-up
    # blindly instead of swimming (the "arrive P4 -> target P3" flag).
    stacked_up = {up: lo for lo, up in map_cfg.get("stacked_up", [])}
    stacked_jump_secs = float(map_cfg.get("stacked_jump_secs", 0.9))
    # Nodes reached AFTER a sink (start_sink / reset): she's already at the right y, so align
    # x-only (no jump) instead of a full swim_to that fights vertically off the floor.
    _x_align = set(map_cfg.get("x_align_nodes", []))
    _no_fall = set(map_cfg.get("no_fall_nodes", []))   # skip the minimap fall check on these nodes
    #                                                    (e.g. P3, where a bottom-edge phantom keeps
    #                                                    false-triggering re-seats)
    _arrive_retries = int(map_cfg.get("arrive_retries", 1))   # extra swim attempts if she didn't seat

    def _seat(node, make_swim):
        """Run a swim and RETRY if it didn't seat (swim_to returns False only when she never
        confirmed arrival -- in tol AND resting). First try seeds the departure anchor; retries
        re-anchor from her actual position. True once seated, False if still not after the retries
        (caller re-locates instead of farming the WRONG platform she happened to land on)."""
        for i in range(_arrive_retries + 1):
            if make_swim(i == 0):
                return True
            if kb.pause or STOP.is_set():
                return False
            print(f"[water] {node}: did not seat (try {i + 1}/{_arrive_retries + 1})")
        return False

    # Shared-pass detection (opt-in via map "shared_detect"): ONE yolo_detect per beat gives BOTH
    # the mobs AND the player box, halving hot-loop inference vs the separate detect_fn + anchor
    # passes. Only when detector+anchor are the SAME mob YOLO; other detectors/anchors keep the
    # two-pass path (combined stays None). approach_shoot pushes the player box to the anchor.
    _combined = None
    if map_cfg.get("shared_detect") and detector == "mob_yolo" and map_cfg.get("anchor") == "yolo_player":
        _pmax = map_cfg.get("player_anchor_max_jump")

        def _combined(frame, near):
            return mob_detect.yolo_detect(_mm, frame, roi=None, conf=_mconf, imgsz=_mimg,
                                          class_conf=_cconf, with_class=_wc, player_conf=_pconf,
                                          near=near, max_jump=_pmax)
        print("[water] shared-pass detection ON (one YOLO inference/beat: mobs + player)")

    def farm_node(node, prev=None):
        cx, cy = centers[node]
        STATUS["node"] = node
        dep = centers.get(prev) if prev in centers else None   # departure anchor -> rejects the
        #                                          first-read phantom when her dot is briefly absent
        def _seat_on_platform(dep):
            """(Re)seat her on `node`: True if seated (blindly for a stacked jump), False if a swim
            couldn't confirm arrival. `dep` seeds the phantom anchor -- the departure on the first
            seat; None on a re-seat, where she reads fine off the platform she fell to."""
            if node in stacked_up and prev == stacked_up[node]:
                # The upper node isn't always DIRECTLY above -- P3 sits 18px right of P4 -- so a
                # straight-up jump falls back onto the lower platform. Drift toward the upper node's
                # x (for `stacked_drift_secs`) WHILE jumping, then jump straight up to seat on it.
                lo_x = centers[prev][0]
                hkey = Key.right if cx > lo_x + 2 else (Key.left if cx < lo_x - 2 else None)
                drift = float(map_cfg.get("stacked_drift_secs", 0.6))
                _hd = "R" if hkey == Key.right else ("L" if hkey == Key.left else "-")
                print(f"[water] --> {node}: jump-up from {prev} (stacked); drift {_hd} {drift}s (x {lo_x}->{cx})")
                t0j = time.time(); t_end = t0j + stacked_jump_secs
                while time.time() < t_end and not kb.pause:
                    if hkey and time.time() - t0j < drift:
                        kb.safe_press(hkey)            # drift toward the upper node's x...
                    elif hkey:
                        kb.safe_release(hkey)          # ...then straight up to land on it
                    kb.safe_press(JUMP); time.sleep(0.1); kb.safe_release(JUMP)
                kb.safe_release_all(); time.sleep(0.4) # let her fall onto the upper platform (seat)
                return True
            if node in _x_align:                       # already at the bottom y (just sank) -> x-only
                print(f"[water] --> farm {node}: align x to {cx} (no jump)")
                return _seat(node, lambda first: swim_to(cx, cy, tol=tol, cap=6.0, jump=False, axis="x",
                                                         verbose=_log_swim, label=node,
                                                         start_near=dep if first else None))
            lift = _lift_override.get(node, _ylift)        # pin nodes (P4) use 0 -- target below
            ntol = watermap.node_swim_tol(map_cfg, node, tol)   # tighten tol_y_up on stacked pins
            print(f"[water] --> farm {node} (center {cx},{cy}) lift={lift} tol={ntol}")  # the pin is unreachable
            if not _seat(node, lambda first: swim_to(cx, cy - lift, tol=ntol, cap=12.0, jump_burst=_jb,
                                                     jump_gap=_jg, verbose=_log_swim, label=node,
                                                     start_near=dep if first else None)):  # aim ABOVE
                return False
            for _ in range(_arrive_jumps.get(node, 0)):    # seat on a pin platform (P4): a few more hops
                if kb.pause:
                    break
                kb.safe_press(JUMP); time.sleep(0.12); kb.safe_release(JUMP); time.sleep(0.05)
            return True

        if not _seat_on_platform(dep):
            print(f"[water] {node}: could not seat -> re-locate")
            return False                               # don't farm the wrong platform she landed on
        heal_skill()                                   # cast buffs here: just arrived, NOT mid-attack
        node_anchor = _make_anchor() if _make_anchor else None   # fresh lock per platform, tracks across beats
        _node_t0 = time.time()
        while True:                                    # farm THIS platform until DEPLETED (no beat cap)
            if kb.pause or STOP.is_set():
                return False
            if _node_max and time.time() - _node_t0 > _node_max:
                print(f"[water] {node}: {_node_max:.0f}s safety cap -> rotate (couldn't clear)")
                break
            if farm_mode == "walk_shoot":
                ok = walk_shoot(centers[node][0], _r.uniform(*stand_secs), detect_fn,
                                attack_key=attack_key, half=_phalf, tol=tol,
                                deplete_reads=int(map_cfg.get("deplete_reads", 2)), label=node)
                heal_skill()
                if ok is False:
                    return False
                if ok is DEPLETED:
                    break
                continue
            if approach:
                _ncx = centers[node][0]                    # keep her on THIS platform (minimap)
                ok = approach_shoot(_r.uniform(*stand_secs), detect_fn, node_anchor,
                                    attack_range=_arange, band=_aband, step=_astep,
                                    attack_key=attack_key, deplete_reads=_adeplete,
                                    stall_limit=_astall, label=node,
                                    mm_bounds=(_ncx - _phalf, _ncx + _phalf),
                                    mm_y=(None if node in _no_fall else cy),
                                    fall_margin=int(map_cfg.get("fall_margin", 22)),
                                    ease_margin=int(map_cfg.get("approach_ease_margin", 45)),
                                    attack_keys=map_cfg.get("attack_keys"),
                                    priority_class=map_cfg.get("priority_class"),
                                    priority_range=map_cfg.get("priority_range"),
                                    priority_hold_hits=int(map_cfg.get("priority_hold_hits", 0)),
                                    priority_lock_grace=int(map_cfg.get("priority_lock_grace", 0)),
                                    fall_confirm=int(map_cfg.get("fall_confirm", 2)),
                                    continuous_attack=bool(map_cfg.get("continuous_attack", False)),
                                    attack_dwell=float(map_cfg.get("attack_dwell_secs", 0.25)),
                                    face_deadzone=int(map_cfg.get("face_deadzone", 15)),
                                    face_settle=float(map_cfg.get("face_settle_secs", 0.2)),
                                    fire_stall_limit=int(map_cfg.get("fire_stall_limit", 5)),
                                    lock_switch_px=int(map_cfg.get("lock_switch_px", 70)),
                                    combined=_combined)
                heal_skill()
                if ok is False:
                    return False
                if ok is FELL:                           # knocked/walked off mid-beat -> re-seat & farm on
                    print(f"[water] {node}: fell off platform -> re-seat")
                    # PHANTOM GUARD: seed the re-seat swim with her ACTUAL fallen position
                    # (stable_char = densest-cluster read, phantom-safe). Without a seed the first
                    # swim read has no `near` and can lock onto a fixed minimap phantom -> she swims
                    # to the top-right map edge (the old phantom-runaway) instead of climbing back.
                    _fell_at = stable_char()
                    if not _seat_on_platform(_fell_at if _fell_at[0] >= 0 else None):
                        return False
                    node_anchor = _make_anchor() if _make_anchor else None
                    _node_t0 = time.time()
                    continue
                if ok is DEPLETED:
                    break                                # platform clear -> advance
                continue
            ok = water_shoot((cx, cy), _r.uniform(*stand_secs),
                             count_fn=(lambda: one_count(node)) if poll_in_shoot else None,
                             threshold=deplete_threshold, tol=tol, attack_key=attack_key)
            heal_skill()
            if ok is False:
                return False
            if ok is DEPLETED:
                break
            if one_count is not None:
                n = one_count(node)
                if n is not None and n < deplete_threshold:
                    break
            elif detector == "time":
                break                                    # time mode: one beat then advance
        return True

    start_safety_monitor()                            # curse/monster runs off the action loop
    if rotation == "sweep":
        print(f"[water] sweep {farm_nodes} then reset via {reset_node or '(bottom)'}")
        if map_cfg.get("start_sink"):                     # farm_nodes[0] is the bottom -> drop
            _b0 = centers[farm_nodes[0]][1]               # straight down instead of swim-wandering
            print(f"[water] start -> sink to bottom (y={_b0}) before first sweep")
            sink_to_bottom(_b0, cap=15.0, near=int(map_cfg.get("sink_near", 35)))
        while True:
            g = guard()
            if g == "stop":
                stop_safety_monitor(); return
            if g != "ok":
                continue
            broke = False
            prev = None
            for node in farm_nodes:                      # bottom -> top, in listed order
                if guard() != "ok":
                    broke = True; break
                take_break_if_due()
                if not farm_node(node, prev=prev):       # prev enables the P4->P3 jump-up
                    broke = True; break
                prev = node
            if broke:
                continue
            # reached the top -> reset to the bottom. NO tol-based alignment: she HOLDS toward the
            # drop (right) until she reaches the rightmost edge and falls, then sinks straight to the
            # bottom (re-holding off any ledge). Going all the way right can't stop her short of the
            # open drop column the way a tol'd swim_to could. The hold IS the alignment + the drop.
            if reset_node:
                STATUS["node"] = reset_node
            bottom_y = centers[farm_nodes[0]][1]                   # P6 y (land here before looping)
            _near = int(map_cfg.get("sink_near", 35))              # buffer: landing a bit high = arrived
            _dir = map_cfg.get("reset_nudge", "right")
            _key = {"right": Key.right, "left": Key.left}.get(_dir)
            print(f"[water] reset -> hold {_dir} to bottom (y within {_near} of {bottom_y})")
            if _key is not None:
                hold_to_bottom(bottom_y, _key, cap=15.0, near=_near)   # WALK to the drop, arrive by y
            else:                                                       # reset_nudge:none -> straight sink
                sink_to_bottom(bottom_y, cap=15.0, near=_near)
            # next sweep's farm_node(farm_nodes[0]) swims to it -> no separate re-center needed
    else:
        current = farm_nodes[0]
        while True:
            g = guard()
            if g == "stop":
                stop_safety_monitor(); return
            if g != "ok":
                continue
            take_break_if_due()
            if farm_node(current):
                current = watermap.next_farm(current, farm_nodes)


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
    Stops on F8 (kb.pause) or `cap` seconds. Zero-risk: never moves the character."""
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
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            print("RESULT:", recover_to_farming())
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "where":                                    # print current node classification
        x, y = stable_char(4)
        print(f"({x},{y}) -> {navmap.classify_node(x, y)}")
    elif cmd == "trackpos":                                 # live minimap (x,y) readout; F8/Ctrl-C to stop
        hz = float(sys.argv[sys.argv.index("--hz") + 1]) if "--hz" in sys.argv else 6.0
        lis = Listener(on_press=kb.on_press); lis.start()
        print(f"[trackpos] live minimap (x,y) at {hz:.0f}Hz -- move up/down to watch the pin. F8 pauses, Ctrl-C stops.")
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
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            print("RESULT:", climb_rope_hop(gx, ly, dismount=dm))
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "portal":                                   # run the full nav chain -> TOP_FARM
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
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
        lis = Listener(on_press=kb.on_press); lis.start()      # F8 stops
        print("[record-climb] manually climb bottom->top; press F8 to stop")
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
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            print("RESULT:", drop_to_fallen())
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "break":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            print("RESULT:", break_cycle(secs))
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "demo":
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            demo_sequence()
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "gobottom":
        if not focus():
            print("could not focus"); sys.exit(1)
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            print("RESULT:", go_to_bottom())
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "farmbottom":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        if not focus():
            print("could not focus"); sys.exit(1)
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            print("RESULT:", farm_bottom(secs))
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "runnav":
        # EXP-low auto-pause removed per user (unreliable; monitored manually).
        def _enemy_check(): return len(get_enemy()) > 0
        def _panic():
            kb.safe_release_all()
            print("[safety] trigger -> releasing keys and PAUSING (no Free Market). F8 to resume.")
            kb.pause = True
        lis = Listener(on_press=kb.on_press); lis.start(); kb.pause = True
        print("FULL RUN (runnav) ready. Switch to the game and press F8 to start / pause.")
        import watermap
        _navcfg = watermap.load_map("maps/dragon_nest.json")   # rest cadence overrides live here
        try:
            farming_loop_nav(exp_check=None, enemy_check=_enemy_check, panic=_panic, cfg=_navcfg)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "nav":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        lis = Listener(on_press=kb.on_press); lis.start()
        try:
            farming_loop_nav(break_every=(9999, 9999), stand_secs=(4, 8), max_seconds=secs)
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
            print("usage: python recovery.py waternav [secs] --map maps/<name>.json"); sys.exit(1)
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else None
        def _enemy_check(): return len(get_enemy()) > 0
        def _panic():
            kb.safe_release_all()
            print("[safety] trigger -> releasing keys and PAUSING. F8 to resume.")
            kb.pause = True
        lis = Listener(on_press=kb.on_press); lis.start()
        if secs is None:
            kb.pause = True
            print("WATER RUN (waternav) ready. Switch to the game and press F8 to start / pause.")
        try:
            farming_loop_water(_map_path, enemy_check=_enemy_check, panic=_panic,
                               max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
    else:
        print("usage: python recovery.py <cmd>")
        print("  run:   runnav          full production farming run (F8 to start/pause)")
        print("  test:  nav [secs]      bounded runnav (default 90s)")
        print("  nav debug: focus | where | trackpos [--hz N] | hop GX LY [dismount] | portal | drop | gobottom | recover")
        print("  route: record-route <map>   record a path -> routes/<map>.capture.jsonl")
        print("  water: waternav [secs] --map maps/<name>.json   water-map farming (F8 start/pause)")
        print("  water: fishcount --map maps/<name>.json [--sweep|--scale S|--thr T|--per N|--roi x0,y0,x1,y1]")
        print("  other: farmbottom [s] | demo | break [s] | record-climb")
