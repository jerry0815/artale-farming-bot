"""Extracted from core.py (behavior unchanged). Functions call the engine as core.<name>;
core.py re-exports them so callers/tests keep using core.<name> and monkeypatch still binds."""
import core


def _window_box():
    """The game window's screen rect as an mss grab box, or None. Prefers pygetwindow;
    falls back to the Win32 finder focus() uses (FindWindowW + GetWindowRect). Does NOT
    require the window to sit fully inside one monitor -- a maximized / monitor-straddling
    window used to make capture() return None (character read (-1,-1) -> recovery bailed)."""
    win = core.gw.getWindowsWithTitle(core.TITLE)
    if win:
        w = win[0]
        box = {"left": w.left, "top": w.top, "width": w.width, "height": w.height}
    else:                                                # pygetwindow missed the title -> Win32
        hwnd = core.ctypes.windll.user32.FindWindowW(None, core.TITLE)
        if not hwnd:
            return None
        r = core.wintypes.RECT()
        core.ctypes.windll.user32.GetWindowRect(hwnd, core.ctypes.byref(r))
        box = {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}
    if box["width"] <= 0 or box["height"] <= 0:
        return None
    return box


def _sct():
    """One reusable mss handle PER THREAD. mss.mss() allocates (and its context-exit frees)
    a Windows device context every call, so doing it per grab dominated capture cost; a
    persistent handle skips that. Thread-local because mss is not thread-safe and the farming
    loop and panel can grab concurrently -- each thread gets its own handle, lazily created."""
    s = getattr(core._sct_tls, "sct", None)
    if s is None:
        s = core.mss.mss()
        core._sct_tls.sct = s
    return s


def capture():
    box = core._window_box()
    if box is None:
        return None
    shot = core._sct().grab(box)
    return core.cv2.cvtColor(core.np.array(shot), core.cv2.COLOR_BGRA2BGR)


def capture_pil_np():
    """Capture returning (PIL RGB, np BGR) -- get_exp() needs the PIL image."""
    box = core._window_box()
    if box is None:
        return None, None
    shot = core._sct().grab(box)
    pil = core.Image.frombytes("RGB", shot.size, shot.rgb)
    return pil, core.cv2.cvtColor(core.np.array(shot), core.cv2.COLOR_BGRA2BGR)


def capture_minimap():
    """Grab ONLY the minimap ROI (MM_X/Y/W/H) as BGR -- ~37x fewer pixels than capture() plus
    it skips the full-frame cvtColor, so the hot position reads (stable_char does >=4 per read)
    get much cheaper. Equivalent to _minimap(capture()) but grabs just the sub-box. Clamped to
    the window rect so a smaller live window can't pull in neighbouring screen pixels (mirrors
    _minimap's clamp). Returns None if the window is gone / the ROI falls outside it."""
    box = core._window_box()
    if box is None:
        return None
    if core.MM_X >= box["width"] or core.MM_Y >= box["height"]:
        return None
    w = min(core.MM_W, box["width"] - core.MM_X)
    h = min(core.MM_H, box["height"] - core.MM_Y)
    region = {"left": box["left"] + core.MM_X, "top": box["top"] + core.MM_Y, "width": w, "height": h}
    shot = core._sct().grab(region)
    return core.cv2.cvtColor(core.np.array(shot), core.cv2.COLOR_BGRA2BGR)
