"""Fallen -> home recovery foundation.

Provides full-minimap position sensing (sees the lower platforms the bot's narrow
band crop misses) in the SAME coordinate frame as the existing calibration
(offset 20,171 -> so ROPE_MINIMAP_X=91, PLATFORM_Y=101 stay valid).

CLI:
  python recovery.py sense <label> [samples]   # read-only: median pos + annotated map
  python recovery.py focus                      # bring game to foreground (verify)
"""
import sys, time, ctypes
import cv2
import numpy as np
from PIL import Image
import mss, pygetwindow as gw
from screeninfo import get_monitors
from detection import detect_character_on_minimap, detect_red_dots
from exp_processor import ExpProcessor
import pyautogui
from pynput.keyboard import Key, Listener
import random
import keyboard as kb   # safe_press/release/release_all + F8 pause listener
import navmap

# --- recovery map constants (calibrated 2026-08-09, full-minimap frame) ---
ROPE_X = 91              # rope minimap x (climbs to farming platform)
FARMING_Y_MAX = 95       # y <= this (and x in patrol range) == on farming platform
FALLEN_Y_MIN = 96        # y >= this == below the farming platform (fallen reads ~99-110,
                         # farming reads ~76-90; boundary ~95 with no dead-zone)
PATROL_X_L, PATROL_X_R = 66, 161
# The rope only reaches the fallen platforms directly beside it. Outside this box she
# is somewhere the rope-climb can't fix (deeper/other platform) -> DON'T flail; stop/panic.
RECOVER_X_MIN, RECOVER_X_MAX = 55, 152   # right fallen platform reads up to ~x148
RECOVER_Y_MAX = 156      # covers rest (~110), bottom farming (~143), AND the close LOWER
                         # bottom ledge (~150) she sometimes drops to; deep falls (200+) still
                         # rejected. Rope connects the levels, so climbing up reaches the top
ROPE_EXIT_TOP_Y = 92     # climb until y<=this before the up-jump (live-proven)
ROPE_CLIMB_MAX = 6.0     # bottom->top is a longer climb (y~143 up to ~85)
JUMP = Key.alt_l
FALL_ABORT_DY = 18       # if y jumps this much more than expected mid-walk -> abort
DROP_X = 67              # narrow drop-through gap: down-jump here drops straight down to
                         # the left fallen platform (x=64 and x=76 are solid; verified by trail)
RIGHT_EDGE_SAFETY = 128  # if detected at/beyond this x, force a left-return NOW
                         # (right edge/gap is ~x140; keeps a safe margin)
TOP_HOME_X = 74          # top farming home (park-left spot); recovery ends here facing right
BOTTOM_Y_MIN = 132       # y >= this == on the bottom farming platform (settles ~143;
                         # reads as low as ~133 mid-landing; rest platform is ~109)
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


def capture():
    win = gw.getWindowsWithTitle(TITLE)
    if not win:
        return None
    w = win[0]
    for m in get_monitors():
        if w.left >= m.x and w.right <= m.x + m.width and w.bottom >= m.y and w.top <= m.y + m.height:
            with mss.mss() as sct:
                shot = sct.grab({"left": w.left, "top": w.top, "width": w.width, "height": w.height})
                return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return None


def capture_pil_np():
    """Capture returning (PIL RGB, np BGR) -- get_exp() needs the PIL image."""
    win = gw.getWindowsWithTitle(TITLE)
    if not win:
        return None, None
    w = win[0]
    for m in get_monitors():
        if w.left >= m.x and w.right <= m.x + m.width and w.bottom >= m.y and w.top <= m.y + m.height:
            with mss.mss() as sct:
                shot = sct.grab({"left": w.left, "top": w.top, "width": w.width, "height": w.height})
                pil = Image.frombytes("RGB", shot.size, shot.rgb)
                return pil, cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return None, None


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
    if not blobs:
        return -1, -1
    if near is not None and near[0] >= 0:
        bx, by, _ = min(blobs, key=lambda b: (b[0]-near[0])**2 + (b[1]-near[1])**2)
    else:
        bx, by, _ = max(blobs, key=lambda b: b[2])
    return bx, by


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


def _median(v):
    return sorted(v)[len(v) // 2]


def sense(label, samples=8):
    xs, ys, last = [], [], None
    for i in range(samples):
        np_img = capture()
        x, y = get_character_full(np_img)
        print(f"  {label} {i+1}: ({x},{y})")
        if x >= 0:
            xs.append(x); ys.append(y); last = np_img
        time.sleep(0.3)
    if xs:
        mx, my = _median(xs), _median(ys)
        print(f"\n[{label}] median ({mx},{my})  x[{min(xs)}-{max(xs)}] y[{min(ys)}-{max(ys)}] n={len(xs)}")
        mm = _minimap(last)
        vis = cv2.resize(mm, (mm.shape[1]*3, mm.shape[0]*3), interpolation=cv2.INTER_NEAREST)
        cv2.circle(vis, (mx*3, my*3), 8, (0, 0, 255), 2)
        cv2.line(vis, (0, 145*3), (vis.shape[1], 145*3), (0, 255, 255), 1)  # old band cutoff
        Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)).save(SP + f"\\sense_{label}.png")
        print(f"saved sense_{label}.png")
        return mx, my
    print(f"[{label}] not detected")
    return -1, -1


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


def climb_and_jump():
    """Press Up to climb the rope; if she reaches the top, up-jump onto the platform.
    Returns True only if she actually climbed to the top -- if Up did nothing (she was
    beside the rope, not on it), returns False so the caller re-aligns and retries."""
    x0, y0 = get_character_full()
    kb.safe_press(Key.up)
    if y0 >= BOTTOM_Y_MIN:                       # bottom platform: rope base is above the
        kb.safe_press(JUMP); time.sleep(0.08)    # floor -> hop (Up+Jump) to catch the rope
        kb.safe_release(JUMP)
    last_y, no_grab, t0, climbed, climbing = None, 0, time.time(), False, False
    while time.time() - t0 < ROPE_CLIMB_MAX:
        if kb.pause:
            break
        x, y = get_character_full()
        if y >= 0:
            if y <= ROPE_EXIT_TOP_Y:
                climbed = True; break
            if y0 >= 0 and y <= y0 - 6:              # risen 6px -> she IS climbing the rope
                climbing = True
            if not climbing:                          # not climbing yet: did she grab at all?
                if last_y is not None and y >= last_y - 1:
                    no_grab += 1
                    if no_grab >= 6:                  # never grabbed -> give up (re-align)
                        break
                else:
                    no_grab = 0
            # once climbing, HOLD Up straight to the top. Do NOT release on a transient
            # "not rising" frame -- that release-mid-climb was the stutter.
            last_y = y
        time.sleep(0.08)
    if climbed:
        kb.safe_press(JUMP); time.sleep(0.15); kb.safe_release(JUMP); time.sleep(0.12)
    kb.safe_release(Key.up)
    time.sleep(0.1)
    return climbed


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
        kb.safe_press('c'); time.sleep(0.1); kb.safe_release('c'); time.sleep(0.1)


def _face_right():
    """Brief right tap so she faces right (dragons are to the right) after a left return
    -- mirrors the origin loop's click_press(Key.right, 0.1)."""
    if kb.pause:
        return
    kb.safe_press(Key.right); time.sleep(0.1); kb.safe_release(Key.right)


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
        kb.safe_press('c'); time.sleep(0.1); kb.safe_release('c'); time.sleep(0.1)
    kb.safe_release(key)
    return True


def farm_bottom(seconds, x_home=BOTTOM_HOME_X, x_far=BOTTOM_FAR_X, stand=12.0):
    """Bottom-platform farm: park left (x~63) and shoot, periodic right->back-left
    sweep. Uses bottom y-context (fell only if y >= BOTTOM_FALL_Y, since here she
    normally sits at y~143). Dragons are to the right, same as the top."""
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
            _shoot()
            r = _plausible_read(last)                 # rejects buff-glow phantom reads
            if r is not None:
                last = r
                if r[1] >= BOTTOM_FALL_Y:
                    kb.safe_release_all(); print(f"[farm_bottom] fell to {r}"); return False
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
    on_bottom = y >= BOTTOM_Y_MIN
    print(f"[bottom] after 2nd down-jump ({x},{y}) on_bottom={on_bottom}")
    kb.safe_release_all()
    return on_bottom


def _climb_from_bottom_continuous():
    """Continuous bottom->top: walk right INTO the rope, then jump+up on the fly to grab
    it (rope base is above the floor), climb, and up-jump onto the top. One smooth motion,
    no stop-align-retry. Returns True if she reached the top."""
    kb.safe_press(Key.right)
    t0, last = time.time(), (BOTTOM_HOME_X, 143)
    while time.time() - t0 < 3.5:                  # walk into the rope
        if kb.pause:
            kb.safe_release_all(); return False
        r = _plausible_read(last)
        if r is not None:
            last = r
            if r[0] >= ROPE_X:                     # reached the rope
                break
        time.sleep(0.04)
    kb.safe_press(JUMP); kb.safe_press(Key.up)     # grab on the fly (jump+up)
    time.sleep(0.1)
    kb.safe_release(JUMP); kb.safe_release(Key.right)
    last_y, stalled, t0, climbed = None, 0, time.time(), False
    while time.time() - t0 < ROPE_CLIMB_MAX:       # climb (Up held)
        if kb.pause:
            break
        x, y = get_character_full()
        if y >= 0:
            if y <= ROPE_EXIT_TOP_Y:
                climbed = True; break
            if last_y is not None and y >= last_y - 1:   # not rising this frame
                if abs(x - ROPE_X) <= 2:                  # ...but she's ON the rope (x~91):
                    stalled = 0                           # keep holding Up, she'll climb
                else:
                    stalled += 1                          # beside the rope -> not on it
                    if stalled >= 5:
                        break
            else:
                stalled = 0
            last_y = y
        time.sleep(0.08)
    if climbed:
        kb.safe_press(JUMP); time.sleep(0.15); kb.safe_release(JUMP); time.sleep(0.12)
    kb.safe_release(Key.up); time.sleep(0.1)
    return climbed


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
    on_bottom = y >= BOTTOM_Y_MIN
    print(f"[rest->bottom] after down-jump ({x},{y}) on_bottom={on_bottom}")
    kb.safe_release_all()
    return on_bottom


def _align_to_rope(lo=ROPE_X, hi=ROPE_X + 1, max_taps=16):
    """Tap left/right until x is in the tight rope grab-zone [91,92], using STABLE reads
    (rejects transient/mid-rope reads -- the y-guard idea). Small taps so she doesn't
    overshoot the ~2px zone the way walk_to_x (which accepts tol+2) did. True if aligned."""
    for _ in range(max_taps):
        if kb.pause:
            return False
        x, y = stable_char(2)
        if x < 0:
            continue
        if lo <= x <= hi:
            return True
        key = Key.right if x < lo else Key.left
        kb.safe_press(key); time.sleep(0.05); kb.safe_release(key)
        time.sleep(0.08)
    x, y = stable_char(2)
    return x >= 0 and lo <= x <= hi


def _idle_on_fallen(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if kb.pause:
            break
        time.sleep(0.3)


def _nav_locate():
    x, y = stable_char(3)
    return navmap.classify_node(x, y)

# (src,dst) -> a callable that performs the move using the tuned primitives.
# Rope/up moves reuse recover_to_farming (it climbs any-below -> top). Downjumps
# reuse the proven composites. Per-rope single-level moves are a live follow-up.
EDGE_ACTIONS = {
    ("TOP_FARM", "REST"):        lambda: drop_to_fallen(),
    ("TOP_FARM", "BOTTOM_FARM"): lambda: go_to_bottom(),
    ("REST", "BOTTOM_FARM"):     lambda: rest_to_bottom(),
    ("REST", "TOP_FARM"):        lambda: recover_to_farming(),
    ("MID", "TOP_FARM"):         lambda: recover_to_farming(),
    ("BOTTOM_FARM", "TOP_FARM"): lambda: recover_to_farming(),
    ("LOWER_LEDGE", "TOP_FARM"): lambda: recover_to_farming(),
}

def execute_edge(edge):
    fn = EDGE_ACTIONS.get((edge["src"], edge["dst"]))
    if fn is None:
        print(f"[nav] no executor for {edge['src']}->{edge['dst']}")
        return False
    return bool(fn())


def farming_loop(exp_check=None, enemy_check=None, panic=None,
                 break_every=(8 * 60, 15 * 60), rest_range=(30, 120),
                 farm_leg=(16, 34), skill_interval=(260, 340), max_seconds=None):
    """Integrated human-like farming loop:
      - farm short humanized stints (continuous patrol + shoot), heal each stint
      - skill ('a') on a jittered cadence
      - every break_every (jittered) take a break: drop to the fallen platform,
        rest, then recover back up
      - auto-recover if knocked off the platform
      - safety callbacks (injected by the notebook, which owns the OCR/minimap logic):
          enemy_check() -> True if another player is on the minimap  -> panic()
          exp_check()   -> True if EXP too low (stuck)               -> panic()
          panic()       -> goto_freemarket() (get to safety)
      max_seconds bounds the run for testing (None = run forever). F8 pauses (kb.pause).
    """
    import random as _r
    if not focus():
        print("[loop] could not focus"); return
    t_start = time.time()
    next_break = time.time() + _r.uniform(*break_every)
    next_skill = time.time() + _r.uniform(*skill_interval)
    while True:
        if max_seconds is not None and time.time() - t_start > max_seconds:
            kb.safe_release_all(); print("[loop] max_seconds reached -> stop"); return
        if kb.pause:
            kb.safe_release_all(); time.sleep(0.1); continue

        # 1. where is she? auto-recover if fallen / not clearly on the farming platform.
        #    If recovery FAILS (she's somewhere the rope can't fix), DON'T loop-flail:
        #    escape to Free Market if we can, otherwise stop and let the user look.
        x, y = stable_char(3)
        if y >= FALLEN_Y_MIN or (x >= 0 and not is_farming(x, y)):
            print(f"[loop] off platform ({x},{y}) -> recover")
            if not recover_to_farming():
                print("[loop] recovery failed -> escape / stop (not flailing)")
                kb.safe_release_all()
                if panic:
                    panic()
                    time.sleep(1); continue
                return                              # no escape callback -> stop safely
            continue
        if x < 0:
            time.sleep(0.2); continue

        # 2. safety (notebook-owned checks)
        if enemy_check and enemy_check():
            print("[loop] another player -> Free Market")
            if panic: panic()
            time.sleep(1); continue
        if exp_check and exp_check():
            print("[loop] EXP too low -> Free Market")
            if panic: panic()
            time.sleep(1); continue

        # 3. scheduled human break: drop to the safe fallen platform, rest, recover
        if time.time() >= next_break:
            print("[loop] break due -> drop to fallen platform")
            if drop_to_fallen():
                rest = _r.uniform(*rest_range)
                print(f"[loop] resting {int(rest)}s on fallen platform")
                _idle_on_fallen(rest)
                recover_to_farming()
            next_break = time.time() + _r.uniform(*break_every)
            continue

        # 4. farm a humanized stint, then heal; skill on jittered cadence
        farm(_r.uniform(*farm_leg))
        kb.safe_press('h'); time.sleep(0.08); kb.safe_release('h')
        if time.time() >= next_skill:
            kb.safe_press('a'); time.sleep(0.4); kb.safe_release('a')
            next_skill = time.time() + _r.uniform(*skill_interval)
        if _r.random() < 0.15:                       # occasional micro-pause
            time.sleep(_r.uniform(0.3, 1.5))


def farming_loop_split(exp_check=None, enemy_check=None, panic=None,
                       top_secs=(120, 240), bottom_secs=(120, 240),
                       break_every=(8 * 60, 15 * 60), rest_range=(30, 120),
                       skill_interval=(260, 340), max_seconds=None):
    """Top<->bottom split farming. Farm top a while -> go_to_bottom -> farm bottom a
    while -> recover to top -> repeat. Jittered breaks (drop to the left fallen platform
    and rest), heal each stint, skill on a jittered cadence, EXP/red-dot safety, and
    auto-recovery if knocked off. F8 pauses (kb.pause). max_seconds bounds it for testing."""
    import random as _r
    if not focus():
        print("[split] could not focus"); return
    t_start = time.time()
    next_break = time.time() + _r.uniform(*break_every)
    next_skill = [time.time() + _r.uniform(*skill_interval)]
    phase = "top"

    def heal_skill():
        kb.safe_press('h'); time.sleep(0.08); kb.safe_release('h')
        if time.time() >= next_skill[0]:
            kb.safe_press('a'); time.sleep(0.4); kb.safe_release('a')
            next_skill[0] = time.time() + _r.uniform(*skill_interval)

    while True:
        if max_seconds is not None and time.time() - t_start > max_seconds:
            kb.safe_release_all(); print("[split] max_seconds reached -> stop"); return
        if kb.pause:
            kb.safe_release_all(); time.sleep(0.1); continue

        x, y = stable_char(3)
        if x < 0:
            time.sleep(0.2); continue
        if enemy_check and enemy_check():
            print("[split] another player -> Free Market")
            if panic: panic()
            time.sleep(1); continue
        if exp_check and exp_check():
            print("[split] EXP too low -> Free Market")
            if panic: panic()
            time.sleep(1); continue

        # scheduled break: rest on the left fallen platform, then resume on top
        if time.time() >= next_break:
            print("[split] break -> left fallen platform, rest")
            if not is_farming(x, y):
                recover_to_farming()
            if drop_to_fallen():
                _idle_on_fallen(_r.uniform(*rest_range))
            recover_to_farming()
            next_break = time.time() + _r.uniform(*break_every)
            phase = "top"
            continue

        if phase == "top":
            if not is_farming(x, y):
                print(f"[split] off top ({x},{y}) -> recover")
                if not recover_to_farming() and panic:
                    panic(); time.sleep(1)
                continue
            if farm(_r.uniform(*top_secs)):          # farm a stint (False if she fell)
                heal_skill()
                print("[split] top stint done -> go to bottom")
                if go_to_bottom():
                    phase = "bottom"
        else:  # bottom
            if y < BOTTOM_Y_MIN:
                print(f"[split] not on bottom ({x},{y}) -> reposition")
                if not (recover_to_farming() and go_to_bottom()):
                    phase = "top"                    # couldn't get down -> farm top
                continue
            if farm_bottom(_r.uniform(*bottom_secs)):
                heal_skill()
                print("[split] bottom stint done -> climb to top")
                if recover_to_farming():
                    phase = "top"


def farming_loop_nav(exp_check=None, enemy_check=None, panic=None,
                     farm_secs=(20, 40), break_every=(8 * 60, 15 * 60),
                     rest_range=(30, 120), skill_interval=(260, 340),
                     deplete_threshold=2, count_samples=3, max_seconds=None):
    """Node-graph farming loop: farm the current platform, count dragons, and when
    depleted (< deplete_threshold) travel() to the other farming platform. Recovery
    and rotation both go through navmap.travel(). Preserves EXP/red-dot safety,
    F8 pause, jittered breaks, and jittered skill/heal cadence. max_seconds bounds it."""
    import random as _r
    import monsters, navmap
    if not focus():
        print("[nav] could not focus"); return
    tpls = monsters.load_templates()
    print(f"[nav] loaded {len(tpls)} dragon templates")
    t_start = time.time()
    next_break = time.time() + _r.uniform(*break_every)
    next_skill = [time.time() + _r.uniform(*skill_interval)]
    current = "TOP_FARM"

    def heal_skill():
        kb.safe_press('h'); time.sleep(0.08); kb.safe_release('h')
        if time.time() >= next_skill[0]:
            kb.safe_press('a'); time.sleep(0.4); kb.safe_release('a')
            next_skill[0] = time.time() + _r.uniform(*skill_interval)

    def go(dst):
        return navmap.travel(dst, locate_fn=_nav_locate, execute_fn=execute_edge)

    while True:
        if max_seconds is not None and time.time() - t_start > max_seconds:
            kb.safe_release_all(); print("[nav] max_seconds -> stop"); return
        if kb.pause:
            kb.safe_release_all(); time.sleep(0.1); continue

        # locate; recover onto a farm node if off-map
        node = _nav_locate()
        if node is None:
            time.sleep(0.2); continue
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

        # farm a stint on the current node
        current = node
        ok = farm(_r.uniform(*farm_secs)) if node == "TOP_FARM" else farm_bottom(_r.uniform(*farm_secs))
        if not ok:
            continue                                 # fell mid-stint -> re-locate/recover next round
        heal_skill()

        # count dragons; rotate if depleted
        n = monsters.count_dragons(capture, tpls, samples=count_samples)
        print(f"[nav] {node} dragons~{n}")
        target = navmap.next_farm_target(node, n, threshold=deplete_threshold)
        if target:
            print(f"[nav] {node} depleted ({n} < {deplete_threshold}) -> travel to {target}")
            if go(target):
                current = target


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
        if is_farming(x, y):
            print("[recover] on top -> move to left home, face right")
            walk_to_x(TOP_HOME_X, tol=3)         # land at the park-left home...
            _face_right()                        # ...facing right (toward the dragons)
            x2, y2 = stable_char(3)
            if is_farming(x2, y2):
                kb.safe_release_all(); return True
            print(f"[recover] slipped off top during home-walk ({x2},{y2}) -> retry")
            kb.safe_release_all(); time.sleep(0.2); continue
        if y < FALLEN_Y_MIN:
            print(f"[recover] y={y} between levels/ambiguous -> bail to be safe")
            kb.safe_release_all(); return False
        # ONLY the fallen platforms beside the rope are rope-recoverable. If she's
        # deeper or off to the side, walking toward the rope just cascades her further
        # down -- STOP instead (caller escapes to Free Market / stops).
        if not (RECOVER_X_MIN <= x <= RECOVER_X_MAX and y <= RECOVER_Y_MAX):
            print(f"[recover] ({x},{y}) outside recoverable region -> cannot rope-recover, bail")
            kb.safe_release_all(); return False
        # LOWER ledge: the rope doesn't reach it -> hop UP onto the bottom platform first,
        # then the next round does the normal bottom->top climb.
        if y >= LOWER_LEDGE_Y:
            print(f"[recover] on lower ledge ({x},{y}) -> jump up to bottom platform")
            kb.safe_press(JUMP); time.sleep(0.12); kb.safe_release(JUMP)
            time.sleep(0.4)
            kb.safe_release_all(); continue
        # line up ON the rope (aim 1px right of the grab x), then climb+jump. Reliable
        # but takes a few retries -- crisp de-steppy attempts all regressed, reverted.
        if not walk_to_x(ROPE_X + 1, tol=1):
            x2, y2 = stable_char(3)
            if not (RECOVER_X_MIN <= x2 <= RECOVER_X_MAX and FALLEN_Y_MIN <= y2 <= RECOVER_Y_MAX):
                print(f"[recover] slid to ({x2},{y2}) outside recoverable region -> bail")
                kb.safe_release_all(); return False
            print("[recover] walk to rope aborted (glitch) -> retry"); kb.safe_release_all()
            time.sleep(0.3); continue
        if not climb_and_jump():
            print("[recover] Up didn't climb -> re-align & retry")
            kb.safe_release_all(); time.sleep(0.2); continue
        time.sleep(0.1)
    x, y = get_character_full()
    ok = is_farming(x, y)
    print(f"[recover] final ({x},{y}) farming={ok}")
    kb.safe_release_all()
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sense"
    if cmd == "focus":
        print("focused:", focus())
    elif cmd == "recover":
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            print("RESULT:", recover_to_farming())
        finally:
            kb.safe_release_all(); lis.stop()
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
    elif cmd == "loop":
        # bounded integration test: short break interval so a full break happens
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            farming_loop(break_every=(35, 40), rest_range=(6, 8),
                         farm_leg=(20, 30), max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd in ("run", "runsplit"):
        # FULL production run. `run` = top-only; `runsplit` = top<->bottom split.
        # Humanized farm + jittered breaks + auto-recovery + EXP-stuck/red-dot safety.
        # Starts PAUSED -- press F8 to begin/pause.
        _exp_proc = ExpProcessor()
        _started = [None]

        def _exp_check():
            if _started[0] is None:
                _started[0] = time.time()
            eg = get_exp(_exp_proc)
            if eg is not None and eg[0] is not None:
                return eg[0] < 4000 and (time.time() - _started[0]) > 180
            return False

        def _enemy_check():
            return len(get_enemy()) > 0

        def _panic():
            # Free Market escape DISABLED per user -- just stop safely and pause (F8 resumes).
            kb.safe_release_all()
            print("[safety] trigger -> releasing keys and PAUSING (no Free Market). F8 to resume.")
            kb.pause = True

        lis = Listener(on_press=kb.on_press); lis.start()
        kb.pause = True
        loop = farming_loop_split if cmd == "runsplit" else farming_loop
        print(f"FULL RUN ({cmd}) ready. Switch to the game and press F8 to start / pause.")
        try:
            loop(exp_check=_exp_check, enemy_check=_enemy_check, panic=_panic)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "runnav":
        _exp_proc = ExpProcessor(); _started = [None]
        def _exp_check():
            if _started[0] is None: _started[0] = time.time()
            eg = get_exp(_exp_proc)
            if eg is not None and eg[0] is not None:
                return eg[0] < 4000 and (time.time() - _started[0]) > 180
            return False
        def _enemy_check(): return len(get_enemy()) > 0
        def _panic():
            kb.safe_release_all()
            print("[safety] trigger -> releasing keys and PAUSING (no Free Market). F8 to resume.")
            kb.pause = True
        lis = Listener(on_press=kb.on_press); lis.start(); kb.pause = True
        print("FULL RUN (runnav) ready. Switch to the game and press F8 to start / pause.")
        try:
            farming_loop_nav(exp_check=_exp_check, enemy_check=_enemy_check, panic=_panic)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "nav":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        lis = Listener(on_press=kb.on_press); lis.start()
        try:
            farming_loop_nav(break_every=(9999, 9999), farm_secs=(12, 16), max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "split":
        # bounded split test: short stints, no break, so you see top->bottom->top quickly
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        lis = Listener(on_press=kb.on_press); lis.start()   # F8 aborts
        try:
            farming_loop_split(top_secs=(14, 18), bottom_secs=(14, 18),
                               break_every=(9999, 9999), max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
    else:
        label = sys.argv[2] if len(sys.argv) > 2 else "pos"
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 8
        sense(label, n)
