"""Extracted from core.py (behavior unchanged). Functions call the engine as core.<name>;
core.py re-exports them so callers/tests keep using core.<name> and monkeypatch still binds."""
import core


def focus():
    u = core.ctypes.windll.user32
    hwnd = u.FindWindowW(None, core.TITLE)
    if not hwnd:
        return False
    cur = core.ctypes.windll.kernel32.GetCurrentThreadId()
    gt = u.GetWindowThreadProcessId(hwnd, None)
    u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)
    u.AttachThreadInput(cur, gt, True)
    u.ShowWindow(hwnd, 9); u.BringWindowToTop(hwnd); u.SetForegroundWindow(hwnd)
    u.AttachThreadInput(cur, gt, False)
    core.time.sleep(0.3)
    return u.GetForegroundWindow() == hwnd


def is_focused():
    """True iff the game window is the CURRENT foreground window. Read-only -- unlike focus() it
    never raises/steals the window, so it's cheap to poll at each iteration. Returns True when it
    can't tell (non-Windows / API error) so a detection glitch never blocks farming."""
    try:
        u = core.ctypes.windll.user32
        hwnd = u.FindWindowW(None, core.TITLE)
        return bool(hwnd) and u.GetForegroundWindow() == hwnd
    except Exception:
        return True


def ensure_game_focused(check=None, refocus=None, label="focus"):
    """Guarantee the GAME holds focus at an iteration boundary so key presses can't leak into
    another window (e.g. the user alt-tabbed, a popup stole focus). If focus was lost, release
    all keys and try to bring the game back. Returns True if the game is (now) focused, False if
    it still isn't -- the caller must NOT send keys that iteration. `check`/`refocus` injectable
    for tests."""
    check = check or core.is_focused
    refocus = refocus or core.focus
    if check():
        return True
    core.kb.safe_release_all()                     # never hold a key down while focus is elsewhere
    print(f"[{label}] game lost focus -> refocus")
    refocus()
    return check()


def focus_ok_or_bail(label="attack", check=None, refocus=None):
    """Beat-level focus guard for the ATTACK loops (they run many beats without returning to the
    boundary guard). True -> keep firing. On focus loss: release all keys, refocus, and return
    False so the loop BAILS to its caller -- which re-seats and re-enters the beat with keys
    pressed fresh. Bailing (vs a mid-loop continue) is what keeps a continuous-HOLD attack from
    being left released after the refocus. `check`/`refocus` injectable for tests."""
    check = check or core.is_focused
    if check():
        return True
    core.kb.safe_release_all()
    print(f"[{label}] lost focus mid-attack -> release + refocus, re-seat")
    (refocus or core.focus)()
    return False


def stable_char(n=3):
    """Robust position: take reads and return the center of the DENSEST cluster, so
    scattered buff-glow phantoms are rejected even if a few slip past the color filter
    (the real position recurs; phantoms are scattered). (-1,-1) if none detect."""
    reads = []
    for _ in range(max(n, 4)):                     # a few extra so a cluster can form
        x, y = core.get_character_full()
        if x >= 0:
            reads.append((x, y))
        core.time.sleep(0.05)
    if not reads:
        return -1, -1
    best, best_n = reads, -1
    for r in reads:                                # densest cluster within 12px
        cl = [q for q in reads if abs(q[0] - r[0]) <= 12 and abs(q[1] - r[1]) <= 12]
        if len(cl) > best_n:
            best_n, best = len(cl), cl
    xs = sorted(c[0] for c in best); ys = sorted(c[1] for c in best)
    return xs[len(xs) // 2], ys[len(ys) // 2]
