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
from detection import detect_character_on_minimap, detect_red_dots, detect_lie_check
from alarm import Alarm, AlertController
import os
from glob import glob as _glob
from exp_processor import ExpProcessor
import pyautogui
from pynput.keyboard import Key, Listener
import random
import keyboard as kb   # safe_press/release/release_all + F8 pause listener
import navmap

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


def capture():
    box = _window_box()
    if box is None:
        return None
    with mss.mss() as sct:
        shot = sct.grab(box)
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


def capture_pil_np():
    """Capture returning (PIL RGB, np BGR) -- get_exp() needs the PIL image."""
    box = _window_box()
    if box is None:
        return None, None
    with mss.mss() as sct:
        shot = sct.grab(box)
        pil = Image.frombytes("RGB", shot.size, shot.rgb)
        return pil, cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


# --- lie-check alarm: beep when a "needs a human" screen appears (captcha / curse) ---
# Two independent pipelines (each its own alarm, so cadences never fight):
#   FAST: transparent-shape only -- ~40ms, so it can run INSIDE the 6-8s STAND_SHOOT
#         and fire immediately. That screen gives ~3s, so latency must be ~1s.
#   FULL: curse + monster -- heavier (wide banner), runs only at the loop top; those
#         screens persist far longer so a slower, debounced check is fine.
_LIE_DIR = "assets/lie_check/"
_FAST_TEMPLATES = ["transparent_title.png"]     # 3s window -> checked in-state, immediate
_FULL_TEMPLATES = ["curse_banner.png", "curse_lock.png", "monster_instr.png"]
_lie_enabled = bool(_glob(os.path.join(_LIE_DIR, "*.png")))

_fast_alarm = Alarm(freq=1000, beep_ms=350, gap_ms=150)
_fast_alert = AlertController(_fast_alarm, trigger_consecutive=1, clear_consecutive=2)  # immediate
_full_alarm = Alarm(freq=1000, beep_ms=350, gap_ms=150)
_full_alert = AlertController(_full_alarm, trigger_consecutive=2, clear_consecutive=1)
_last_fast_tick = [0.0]
_last_full_tick = [0.0]
if not _lie_enabled:
    print(f"[lie-check] '{_LIE_DIR}' 沒有模板，警報停用。")

def lie_check_fast_tick(interval=0.8):
    """Cheap transparent-shape check (~40ms). Call it often -- inside STAND_SHOOT and
    at the loop top -- so the 3s transparent screen alarms within ~1s. Immediate."""
    if not _lie_enabled:
        return
    now = time.time()
    if now - _last_fast_tick[0] < interval:
        return
    _last_fast_tick[0] = now
    f = capture()
    if f is None or not hasattr(f, "shape"):
        return
    hits = detect_lie_check(f, templates_folder=_LIE_DIR,
                            template_filter=_FAST_TEMPLATES, work_width=520)
    if _fast_alert.update(bool(hits)) and hits:
        print(f"[lie-check] ⚠️ 透明圖形驗證 {[(n, round(s, 2)) for n, s in hits]} -- ALARM (F8 暫停)")

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
    hits = detect_lie_check(f, templates_folder=_LIE_DIR, template_filter=_FULL_TEMPLATES,
                            work_width=1000)
    if _full_alert.update(bool(hits)) and hits:
        print(f"[lie-check] ⚠️ 需真人處理畫面 {[(n, round(s, 2)) for n, s in hits]} -- ALARM (F8 暫停)")

def lie_check_tick():
    """Loop-top check: run both pipelines (fast covers transparent between states too)."""
    lie_check_fast_tick()
    lie_check_full_tick()

def lie_check_silence():
    """Stop both alarms (e.g. when the bot pauses -- a human is present)."""
    _fast_alert.update(False)
    _full_alert.update(False)


# --- game-logic safety, ported faithfully from the notebook (screen reads, no keys) ---
def get_enemy():
    """Red dots (other players) on the minimap band. Non-empty -> escape."""
    _, np_img = capture_pil_np()
    if np_img is None:
        return []
    ey = min(MM_Y + 145, np_img.shape[0]); ex = min(MM_X + 229, np_img.shape[1])
    mm = np_img[MM_Y:ey, MM_X:ex]                 # notebook uses the 145-tall band here
    try:
        return detect_red_dots(mm, templates_folder="assets/minimap_other_character/", threshold=0.75)
    except Exception:
        return []


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


def _minimap(np_img):
    ey = min(MM_Y + MM_H, np_img.shape[0]); ex = min(MM_X + MM_W, np_img.shape[1])
    return np_img[MM_Y:ey, MM_X:ex]


def get_character_color(np_img=None, near=None):
    """Character (x,y) via COLOR (bright yellow blob) on the full minimap. Far more
    robust than template matching, which returns a phantom (122,188) at some spots.
    If `near`=(x,y) is given and several yellow blobs exist, pick the closest one
    (temporal continuity); else pick the largest plausible blob. (-1,-1) if none."""
    if np_img is None:
        np_img = capture()
    if np_img is None:
        return -1, -1
    mm = _minimap(np_img)
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
    """Character (x, y) on the FULL minimap, same frame as the band. Color first
    (robust), template as a fallback. (-1,-1) if neither finds it."""
    if np_img is None:
        np_img = capture()
    if np_img is None:
        return -1, -1
    x, y = get_character_color(np_img, near=near)
    if x >= 0:
        return x, y
    centers = detect_character_on_minimap(_minimap(np_img), templates_folder=CHAR_TPL, threshold=0.7)
    if centers:
        return centers[0][0], centers[0][1]
    return -1, -1


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
    key = Key.left if edge.get("dismount") == "left" else Key.right
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
                     deplete_threshold=1, max_seconds=None):
    """Node-graph farming loop with a STAND/WALK state machine. Each iteration runs
    ONE state move on the current platform (STAND_SHOOT or WALK_SHOOT), then counts
    dragons at the resulting standstill; a low count (< deplete_threshold) rotates to
    the next platform via travel(), otherwise the states alternate. Recovery and
    rotation both go through navmap.travel(). Preserves EXP/red-dot safety, F8 pause,
    jittered breaks, and jittered skill/heal cadence. max_seconds bounds it."""
    import random as _r
    import monsters, navmap
    if not focus():
        print("[nav] could not focus"); return
    print("[nav] state machine (stand/walk) + motion-based dragon counting")
    t_start = time.time()
    next_break = time.time() + _r.uniform(*break_every)
    next_skill = [time.time()]                    # cast skills from the first stint (as in split)
    current = "TOP_FARM"
    farm_state = navmap.STAND_SHOOT

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

    def one_count(nd):
        roi = monsters.MOTION_ROI_BY_NODE.get(nd, monsters.DEFAULT_MOTION_ROI)
        f = capture()
        return None if f is None else len(monsters.detect_dragons_yolo(f, model, roi))

    def heal_skill():
        # Throttled to skill_interval (240-300s): the timed BUFFS A and J only. 'H' is
        # NOT here -- it is cast before every STAND_SHOOT (see cast_h), per user.
        if time.time() < next_skill[0]:
            return
        next_skill[0] = time.time() + _r.uniform(*skill_interval)
        kb.safe_press('a'); time.sleep(0.4); kb.safe_release('a')
        kb.safe_press('j'); time.sleep(0.4); kb.safe_release('j')

    def cast_h():
        # 'H' (heal/potion) before every STAND_SHOOT -- not throttled, per user request.
        kb.safe_press('h'); time.sleep(0.08); kb.safe_release('h')

    def go(dst):
        return navmap.travel(dst, locate_fn=_nav_locate, execute_fn=execute_edge)

    while True:
        if max_seconds is not None and time.time() - t_start > max_seconds:
            kb.safe_release_all(); print("[nav] max_seconds -> stop"); return
        if kb.pause:
            kb.safe_release_all(); lie_check_silence(); time.sleep(0.1); continue
        lie_check_tick()

        # locate; recover onto a farm node if off-map
        node = _nav_locate()
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
            print("[nav] another player -> panic")
            if panic: panic()
            time.sleep(1); continue
        if exp_check and exp_check():
            print("[nav] EXP too low -> panic")
            if panic: panic()
            time.sleep(1); continue

        # scheduled break: drop to REST, rest, climb back
        if time.time() >= next_break:
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
        try:
            farming_loop_nav(exp_check=None, enemy_check=_enemy_check, panic=_panic)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "nav":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        lis = Listener(on_press=kb.on_press); lis.start()
        try:
            farming_loop_nav(break_every=(9999, 9999), stand_secs=(4, 8), max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
    else:
        print("usage: python recovery.py <cmd>")
        print("  run:   runnav          full production farming run (F8 to start/pause)")
        print("  test:  nav [secs]      bounded runnav (default 90s)")
        print("  nav debug: focus | where | hop GX LY [dismount] | portal | drop | gobottom | recover")
        print("  route: record-route <map>   record a path -> routes/<map>.capture.jsonl")
        print("  other: farmbottom [s] | demo | break [s] | record-climb")
