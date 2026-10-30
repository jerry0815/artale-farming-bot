"""Channel hop: switch to random channels until one lands on a map with NO other player.

One hop, by hand, is: 目錄 -> 頻道 -> random-channel icon -> 確定 -> (loading ~16 s) -> 登入 ->
click the character -> 選擇角色 -> back in the map (~30 s total). Since the 2026-10 game update the
character must be clicked first: its info scroll appears, and only then does 選擇角色 log in. Instead of a fixed click script, each tick reads
WHICH screen is up (template match, assets/channel_hop/ from build_channel_templates.py) and clicks
that screen's button -- so a swallowed click (登入 clicked while still fading in) or a slow load just
repeats the tick instead of derailing the sequence.

Arrival check: after landing (目錄 visible again after 確定 was clicked), wait `settle` s for the
minimap, then sample one GENERAL box for red other-player dots a few times. The minimap's corner is
fixed and only its size varies per map, so MM_BOX covers the largest one (deep_sea_2 400 tall,
flat_sweep 400 wide) -- no per-map config. Checked offline: on 1638 frames of 4 maps + 111 red-heavy
false-alarm samples, the big box never found a dot outside the real minimap and always agreed with
the per-map box. Uses detect_red_dots directly -- NOT get_enemy(), which returns [] when the panel's
another-player mode is 'off' (that would read every channel as empty).

    python channel_hop.py [max_hops]     # CLI; the panel's Channel tab calls run()
"""
import json, os, sys, time
import cv2
import numpy as np
import capture
import core

TMPL_DIR = os.path.join("assets", "channel_hop")
MATCH_THR = 0.8          # recorded: present >= 0.90, absent <= 0.63
BRIGHT_TOL = 0.10        # matched patch mean within 10% of the template's: the login screen fades
                         # in over ~5 s and a 登入 click at ~83% brightness was ignored
SEARCH_PAD = 120         # search this far around the recorded box (dialogs barely move)
# Checked in priority order: a dialog sits over the HUD, so 目錄 (menu) is visible under all of
# the first three; the login screens never co-occur with the in-map ones. On character select,
# 'select' (the info scroll -> click 選擇角色) wins over 'charsel' (signs only -> click the character).
ORDER = ("confirm", "random", "channel", "login", "select", "charsel", "menu")
RECLICK_SECS = 2.5       # don't re-click the same button sooner than this (let the UI react)
UNKNOWN_WARN_SECS = 8.0  # nothing recognised this long before 確定 -> warn (templates don't match)
HOP_TIMEOUT = 150.0      # one hop (click 確定 -> back in a map) normally ~35 s
MM_BOX = (15, 168, 420, 410)   # x, y, w, h: covers every map's minimap (see module doc), measured at
MM_REF_W = 1940                # this window width; scaled like the templates
DOT_THR = 0.75           # red-dot template threshold (same as core.ENEMY_THRESHOLD / the map configs)
# Second dot rule (2026-10-22): the strict templates missed real dots sitting on a platform edge /
# the portal (scored 0.72 / 0.67) -> hops reported "empty" with people there. A dot-sized ROUND red
# blob also counts if the templates score >= BLOB_TSCORE around it. Measured: real dots >= 0.67
# (164 recorded dots >= 0.78); red scenery that passes the blob shape test (STOP signs, damage
# numbers, a poster's red rectangle) <= 0.40.
# Blobs come from a BRIGHT-red mask (S, V >= BLOB_SV): a dot touching the dull-red flower box on
# the top floor merged with it into one 27x10 blob and was missed (live 2026-10-27). Dots are pure
# red (median S 255, V 238); the flower box is S 194 / V 153 -> excluded, the dot stands alone.
BLOB_SV = 180
BLOB_WH = (7, 14)        # dot blob width / height range (px, at the ~1940 reference scale)
BLOB_MIN_AREA = 40       # bright-red px (a dot's dimmer rim drops out of the bright mask)
BLOB_TSCORE = 0.55
HOP_CHECK_DIR = os.path.join("datasets", "hop_checks")   # minimap behind every "empty" call

# The game redraws its whole UI scaled to the window size, from the window's top-left corner:
# between two recordings the window went 1940 -> 1915 wide (-1.3%) and every element shrank 1-1.5%.
# Unscaled templates then scored 0.64-0.86 (< MATCH_THR), so everything below is scaled by
# live width / recording width.
_T = {}
_REC_W = [None]


def _load():
    if not _T:
        with open(os.path.join(TMPL_DIR, "templates.json"), encoding="utf-8") as f:
            meta = json.load(f)
        _REC_W[0] = float(meta.pop("_window", [MM_REF_W])[0])
        for name, m in meta.items():
            t = cv2.imread(os.path.join(TMPL_DIR, f"{name}.png"))
            _T[(name, 1.0)] = (t, m["box"], tuple(m["click"]), float(t.mean()))


def templates(width=None):
    """name -> (BGR template, box [x0,y0,x1,y1], click offset (dx,dy), template mean), scaled to a
    window `width` px wide (None = the recording's own size). Cached per scale."""
    _load()
    s = 1.0 if width is None else round(width / _REC_W[0], 3)
    if (ORDER[0], s) not in _T:
        for name in ORDER:
            t, box, (dx, dy), _ = _T[(name, 1.0)]
            ts = cv2.resize(t, None, fx=s, fy=s,
                            interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            _T[(name, s)] = (ts, [round(v * s) for v in box], (round(dx * s), round(dy * s)),
                             float(ts.mean()))
    return {name: _T[(name, s)] for name in ORDER}


def find(frame, name):
    """Window-relative click point for `name` if its button is on screen (and fully faded in),
    else None."""
    H, W = frame.shape[:2]
    t, (x0, y0, x1, y1), (dx, dy), t_mean = templates(W)[name]
    sx0, sy0 = max(0, x0 - SEARCH_PAD), max(0, y0 - SEARCH_PAD)
    sx1, sy1 = min(W, x1 + SEARCH_PAD), min(H, y1 + SEARCH_PAD)
    roi = frame[sy0:sy1, sx0:sx1]
    th, tw = t.shape[:2]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return None
    res = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
    _, score, _, (mx, my) = cv2.minMaxLoc(res)
    if score < MATCH_THR:
        return None
    if abs(roi[my:my + th, mx:mx + tw].mean() - t_mean) > BRIGHT_TOL * t_mean:
        return None
    return sx0 + mx + dx, sy0 + my + dy


def screen(frame):
    """(name, click point) of the top-most recognised screen, or (None, None) (loading/black)."""
    for name in ORDER:
        p = find(frame, name)
        if p is not None:
            return name, p
    return None, None


def minimap_drawn(frame):
    """The minimap's white top border (rows ~171-172 across x 25-95) is up. The big box also covers
    game scene, so 'no dots' alone can't tell an empty map from a minimap not drawn yet. Present on
    every in-map frame after a login in the recording; a buff aura over the minimap can hide it
    (-> read as not drawn, never as empty)."""
    k = frame.shape[1] / MM_REF_W
    band = frame[round(155 * k):round(195 * k), round(25 * k):round(95 * k)].min(axis=2)
    return band.size > 0 and bool(((band >= 215).mean(axis=1) >= 0.7).any())


_DOT_T = []


def _dot_templates():
    if not _DOT_T:
        import glob
        _DOT_T.extend(t for t in (cv2.imread(p) for p in
                      sorted(glob.glob("assets/minimap_other_character/*.png"))) if t is not None)
    return _DOT_T


def _round_red_blobs(mm):
    """Centres of dot-sized, roughly round, filled BRIGHT red blobs (detection.RED_HSV_RANGES with
    saturation and value floored at BLOB_SV)."""
    import detection
    hsv = cv2.cvtColor(mm, cv2.COLOR_BGR2HSV)
    m = None
    for lo, hi in detection.RED_HSV_RANGES:
        lo = np.array([lo[0], max(int(lo[1]), BLOB_SV), max(int(lo[2]), BLOB_SV)], np.uint8)
        q = cv2.inRange(hsv, lo, hi)
        m = q if m is None else cv2.bitwise_or(m, q)
    n, _, st, cen = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        _, _, w, h, a = st[i]
        if (BLOB_WH[0] <= w <= BLOB_WH[1] and BLOB_WH[0] <= h <= BLOB_WH[1]
                and max(w, h) / min(w, h) <= 1.4 and a >= BLOB_MIN_AREA
                and 0.55 <= a / (w * h) <= 0.92):           # a dot is round: fills ~0.8 of its box
            out.append((int(cen[i][0]), int(cen[i][1])))
    return out


def _template_score(mm, cx, cy, r=14):
    win = mm[max(0, cy - r):cy + r, max(0, cx - r):cx + r]
    best = -1.0
    for t in _dot_templates():
        if win.shape[0] >= t.shape[0] and win.shape[1] >= t.shape[1]:
            best = max(best, float(cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED).max()))
    return best


def minimap_dots(mm):
    """Other-player dots in a minimap crop at the reference scale: strict template matches, plus
    round red blobs the templates still half-recognise (see BLOB_TSCORE)."""
    dots = [tuple(d) for d in core.detect_red_dots(mm, templates_folder="assets/minimap_other_character/",
                                                   threshold=DOT_THR)]
    for cx, cy in _round_red_blobs(mm):
        if any(abs(cx - x) <= 8 and abs(cy - y) <= 8 for x, y in dots):
            continue
        if _template_score(mm, cx, cy) >= BLOB_TSCORE:
            dots.append((cx, cy))
    return dots


def _minimap_crop(frame):
    k = frame.shape[1] / MM_REF_W
    x, y, w, h = (round(v * k) for v in MM_BOX)
    mm = frame[y:y + h, x:x + w]
    if abs(k - 1) > 0.02:            # the red-dot templates are pixel-sized for ~1940 wide: bring
        mm = cv2.resize(mm, (MM_BOX[2], MM_BOX[3]))   # the crop back to that scale first
    return mm


def other_players(frame):
    """Red other-player dots inside MM_BOX, or None if the minimap isn't drawn yet."""
    if not minimap_drawn(frame):
        return None
    return minimap_dots(_minimap_crop(frame))


def _save_check(frame, verdict):
    """Keep the minimap an "empty" call was based on (datasets/hop_checks/<ts>_<verdict>.png) so a
    channel that turns out busy can be checked afterwards. Only empty calls are saved: the occupied
    ones were never wrong in review (551/551) and just piled up. Never raises."""
    try:
        os.makedirs(HOP_CHECK_DIR, exist_ok=True)
        t = time.time()
        name = time.strftime("%Y%m%d_%H%M%S", time.localtime(t)) + f"_{int(t % 1 * 1000):03d}_{verdict}.png"
        cv2.imwrite(os.path.join(HOP_CHECK_DIR, name), _minimap_crop(frame))
    except Exception:
        pass


def _click(p):
    """Click a canonical-frame point (capture() frames are canonical, see capture.py): mapped to the
    real screen for the live window, so it lands right even when the game shrank its client."""
    g = core.window_geometry()
    if g is None:
        return False
    core._click_screen(*capture.frame_to_screen(p[0], p[1], g))
    time.sleep(0.15)
    core.ctypes.windll.user32.SetCursorPos(*capture.frame_to_screen(40, g["H"] // 2, g))
    return True                                   # parked off the buttons: the game draws its own
                                                  # cursor, which would sit on the next template


def _stopped():
    return core.STOP.is_set()


def check_empty(settle=3.0, samples=4, gap=0.5, max_reads=12):
    """After landing: True = no other player, False = someone's here, None = stopped/unreadable.
    Needs `samples` readable minimap reads; unreadable ones (minimap not drawn yet) are retried,
    up to `max_reads` grabs, so a slow minimap doesn't skip a possibly-empty channel."""
    end = time.time() + settle
    while time.time() < end:
        if _stopped():
            return None
        time.sleep(0.1)
    seen, last = 0, None
    for _ in range(max_reads):
        if seen >= samples:
            break
        if _stopped():
            return None
        f = core.capture()
        dots = other_players(f) if f is not None else None
        if dots is None:
            time.sleep(gap); continue
        seen, last = seen + 1, f
        if dots:
            print(f"[hop] {len(dots)} other player(s) on the minimap")
            return False
        time.sleep(gap)
    if seen and last is not None:
        _save_check(last, "empty")
    return True if seen else None


def hop_once():
    """Drive one channel change. True = back in a map, False = timed out / stopped."""
    t0, launched, last = time.time(), False, {}
    seen, warned = t0, False
    while time.time() - t0 < HOP_TIMEOUT:
        if _stopped():
            return False
        f = core.capture()
        if f is None:
            time.sleep(0.5); continue
        name, p = screen(f)
        now = time.time()
        if name is not None:
            seen = now
        elif not launched and not warned and now - seen > UNKNOWN_WARN_SECS:
            # before 確定 nothing should be unrecognised for long (loading only comes after it):
            # say so instead of waiting out HOP_TIMEOUT silently (2026-10-21: 目錄 stopped matching)
            warned = True
            print(f"[hop] no known screen for {now - seen:.0f}s -- button templates may not match "
                  f"this game version / window; re-record with record_channel_switch.py")
        if name == "menu" and launched:
            print(f"[hop] landed after {now - t0:.0f}s")
            return True
        if name is not None and now - last.get(name, 0) >= RECLICK_SECS:
            if name == "confirm":
                launched = True
            if name in ("login", "charsel", "select"):
                launched = True                   # already past 確定 (e.g. started mid-hop)
            core.STATUS["node"] = f"hop: {name}"
            print(f"[hop] {name} -> click {p}")
            # focus() taps Alt (the foreground-steal trick) and Alt is the jump key -> the character
            # jumped on every click. Only refocus when the game actually lost focus.
            if not core.is_focused() and not core.focus():
                print("[hop] could not focus the game"); time.sleep(1); continue
            _click(p)
            last[name] = now
        time.sleep(0.3)
    print(f"[hop] hop timed out after {HOP_TIMEOUT:g}s")
    return False


def _discord(key, msg):
    """Best-effort Discord post (notify.json webhook); never breaks the hop."""
    try:
        core.notify.send(key, msg)
    except Exception as e:
        print(f"[hop] discord failed: {e}")


def run(max_hops=30, settle=3.0):
    """Hop until a channel with no other player on the map, then stop. Checks the current channel
    first. Returns True if an empty channel was found."""
    core.STOP.clear()
    core.STATUS["state"] = "hopping"
    t0 = time.time()
    try:
        for hop in range(max_hops + 1):
            if hop:
                print(f"[hop] --- hop {hop}/{max_hops} ---")
                if not hop_once():
                    if _stopped():
                        print("[hop] STOP"); return False
                    continue                      # timed out: re-read the screen and try again
            empty = check_empty(settle)
            if empty is None:
                if _stopped():
                    print("[hop] STOP"); return False
                print("[hop] minimap not readable -> hop again")
            elif empty:
                mins = (time.time() - t0) / 60
                print(f"[hop] empty channel found after {hop} hop(s), {mins:.1f} min")
                _discord("channel_hop_found",
                         f"✅ Channel hop: empty channel found after {hop} hop(s) ({mins:.1f} min) "
                         f"— no other player on the minimap. Hopping stopped; start farming.")
                return True
        mins = (time.time() - t0) / 60
        print(f"[hop] gave up after {max_hops} hops")
        _discord("channel_hop_gave_up",
                 f"⚠️ Channel hop: gave up after {max_hops} hops ({mins:.1f} min) — "
                 f"every channel had another player. Standing in the last one.")
        return False
    finally:
        core.STATUS["state"] = "idle"
        core.STATUS["node"] = None


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
