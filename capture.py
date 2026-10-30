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


# --- Canonical frames ------------------------------------------------------------------------
# The game sometimes renders a SMALLER client area inside an unchanged window border (1920x1080 ->
# 1918x1079 / 1893x1065 / 1888x1062 seen; border 11/45/11/11 at 150% DPI), and everything it draws
# shrinks with it -- minimap reads, templates and screen positions all drifted. Every capture is
# normalised to the canonical layout: the client resized to CANON_CLIENT inside the same border, so
# the bot always sees identical pixels. Clicks map canonical frame points back to the real screen.
CANON_CLIENT = (1920, 1080)


def canon_geometry(box, client):
    """Pure: window box {left,top,width,height} + client rect (left, top, w, h) on screen ->
    {box, off_x, off_y, right, bottom, cw, ch, W, H} where W x H is the canonical frame size.
    client None (unknown) -> treat the whole window as already canonical."""
    if client is None:
        return {"box": box, "off_x": 0, "off_y": 0, "right": 0, "bottom": 0,
                "cw": box["width"], "ch": box["height"], "W": box["width"], "H": box["height"],
                "canonical": True}
    cl, ct, cw, ch = client
    off_x, off_y = cl - box["left"], ct - box["top"]
    right = box["left"] + box["width"] - (cl + cw)
    bottom = box["top"] + box["height"] - (ct + ch)
    W, H = CANON_CLIENT[0] + off_x + right, CANON_CLIENT[1] + off_y + bottom
    return {"box": box, "off_x": off_x, "off_y": off_y, "right": right, "bottom": bottom,
            "cw": cw, "ch": ch, "W": W, "H": H, "canonical": (cw, ch) == CANON_CLIENT}


def canonicalize(img, g):
    """Pure: the grabbed window image -> the canonical frame (unchanged object when the client is
    already canonical). The border is stretched to the canonical size; the client is resized to
    CANON_CLIENT and placed at the border offset."""
    if g["canonical"]:
        return img
    cv2 = core.cv2
    ox, oy, cw, ch = g["off_x"], g["off_y"], g["cw"], g["ch"]
    canvas = cv2.resize(img, (g["W"], g["H"]), interpolation=cv2.INTER_LINEAR)
    client = img[oy:oy + ch, ox:ox + cw]
    canvas[oy:oy + CANON_CLIENT[1], ox:ox + CANON_CLIENT[0]] = cv2.resize(
        client, CANON_CLIENT, interpolation=cv2.INTER_LINEAR)
    return canvas


def frame_to_screen(x, y, g):
    """Pure: a canonical-frame point -> the real screen pixel (for clicks)."""
    b = g["box"]
    sx = b["left"] + g["off_x"] + (x - g["off_x"]) * g["cw"] / CANON_CLIENT[0]
    sy = b["top"] + g["off_y"] + (y - g["off_y"]) * g["ch"] / CANON_CLIENT[1]
    return round(sx), round(sy)


def screen_to_frame(sx, sy, g):
    """Pure: a real screen pixel -> the canonical-frame point (e.g. a recorded click)."""
    b = g["box"]
    x = g["off_x"] + (sx - b["left"] - g["off_x"]) * CANON_CLIENT[0] / g["cw"]
    y = g["off_y"] + (sy - b["top"] - g["off_y"]) * CANON_CLIENT[1] / g["ch"]
    return round(x), round(y)


def _client_rect():
    """The game window's client area on screen (left, top, w, h) in physical px, or None."""
    try:
        u = core.ctypes.windll.user32
        hwnd = u.FindWindowW(None, core.TITLE)
        if not hwnd:
            return None
        r = core.wintypes.RECT()
        if not u.GetClientRect(hwnd, core.ctypes.byref(r)):
            return None
        pt = core.wintypes.POINT(0, 0)
        u.ClientToScreen(hwnd, core.ctypes.byref(pt))
        return (pt.x, pt.y, r.right, r.bottom) if r.right > 0 and r.bottom > 0 else None
    except Exception:
        return None


def window_geometry():
    """Live canonical geometry of the game window, or None if the window is gone."""
    core._sct()                                 # mss makes the process DPI-aware: physical px
    box = core._window_box()
    if box is None:
        return None
    return canon_geometry(box, core._client_rect())


def game_to_screen(x, y):
    """Canonical-frame point -> real screen pixel for the live window (None if it's gone)."""
    g = core.window_geometry()
    return None if g is None else frame_to_screen(x, y, g)


def capture():
    g = core.window_geometry()
    if g is None:
        return None
    shot = core._sct().grab(g["box"])
    return canonicalize(core.cv2.cvtColor(core.np.array(shot), core.cv2.COLOR_BGRA2BGR), g)


def capture_pil_np():
    """Capture returning (PIL RGB, np BGR) -- get_exp() needs the PIL image. Canonical frame."""
    img = core.capture()
    if img is None:
        return None, None
    return core.Image.fromarray(core.cv2.cvtColor(img, core.cv2.COLOR_BGR2RGB)), img


def capture_minimap():
    """Grab ONLY the minimap ROI (MM_X/Y/W/H) as BGR -- ~37x fewer pixels than capture() plus
    it skips the full-frame cvtColor, so the hot position reads (stable_char does >=4 per read)
    get much cheaper. Equivalent to _minimap(capture()) but grabs just the sub-box. Clamped to
    the window rect so a smaller live window can't pull in neighbouring screen pixels (mirrors
    _minimap's clamp). Returns None if the window is gone / the ROI falls outside it."""
    g = core.window_geometry()
    if g is None:
        return None
    if not g["canonical"]:                       # the game shrank its client: the ROI fast path
        f = core.capture()                       # would read the shrunk picture -> canonical frame
        return core._minimap(f) if f is not None else None
    box = g["box"]
    k = core._minimap_scale(box["width"])
    if k is not None:                            # window differs from the reference: grab the box
        x0, y0 = round(core.MM_X * k), round(core.MM_Y * k)      # scaled with the window, resize
        w = min(round((core.MM_X + core.MM_W) * k), box["width"]) - x0   # back (charloc._minimap)
        h = min(round((core.MM_Y + core.MM_H) * k), box["height"]) - y0
        if w <= 0 or h <= 0:
            return None
        shot = core._sct().grab({"left": box["left"] + x0, "top": box["top"] + y0, "width": w, "height": h})
        crop = core.cv2.cvtColor(core.np.array(shot), core.cv2.COLOR_BGRA2BGR)
        return core.cv2.resize(crop, (core.MM_W, core.MM_H),
                               interpolation=core.cv2.INTER_AREA if k > 1 else core.cv2.INTER_LINEAR)
    if core.MM_X >= box["width"] or core.MM_Y >= box["height"]:
        return None
    w = min(core.MM_W, box["width"] - core.MM_X)
    h = min(core.MM_H, box["height"] - core.MM_Y)
    region = {"left": box["left"] + core.MM_X, "top": box["top"] + core.MM_Y, "width": w, "height": h}
    shot = core._sct().grab(region)
    return core.cv2.cvtColor(core.np.array(shot), core.cv2.COLOR_BGRA2BGR)
