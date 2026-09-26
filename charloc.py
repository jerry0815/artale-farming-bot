"""Extracted from core.py (behavior unchanged). Functions call the engine as core.<name>;
core.py re-exports them so callers/tests keep using core.<name> and monkeypatch still binds."""
import core


def _minimap(np_img):
    ey = min(core.MM_Y + core.MM_H, np_img.shape[0]); ex = min(core.MM_X + core.MM_W, np_img.shape[1])
    return np_img[core.MM_Y:ey, core.MM_X:ex]


def _char_blobs_from_mm(mm):
    """Yellow character-dot-like blobs on a MINIMAP crop: [(x, y, area)]. Tight to the real dot's
    signature (hue~29, sat~245, val~242); a translucent buff/heat-aura glow reads as lower
    saturation/value -> excluded. Size ~54px; keep 15-120. Value floor 205: her dot is ~248
    bright, while map art on some minimaps is the same hue but darker (flat_sweep's recycling-bin
    graphic, V ~165, sat at (208, 63)) -- at a floor of 165 it passed as a second "dot" in 638/646
    frames and the nearest-to-last tracker latched onto it. At 205: 1 blob in 731/796 frames
    (flat_sweep + deep_sea_2), no dots lost."""
    hsv = core.cv2.cvtColor(mm, core.cv2.COLOR_BGR2HSV)
    mask = core.cv2.inRange(hsv, core.np.array([20, 165, 205]), core.np.array([36, 255, 255]))
    num, labels, stats, cents = core.cv2.connectedComponentsWithStats(mask, 8)
    return [(int(cents[i][0]), int(cents[i][1]), int(stats[i][4]))
            for i in range(1, num) if 15 <= stats[i][4] <= 120]


def char_blobs(np_img):
    """All character-dot-like blobs in the minimap of a full BGR frame: [(x, y, area)]."""
    mm = core._minimap(np_img)
    return [] if mm is None else core._char_blobs_from_mm(mm)


def _char_color_from_mm(mm, near=None):
    """Character (x,y) via COLOR (bright yellow blob) from a MINIMAP crop `mm`. Shared by
    get_character_color / get_character_full so each does exactly one grab. (-1,-1) if none."""
    pick = core._pick_char_blob(core._char_blobs_from_mm(mm), near)
    if pick is None:
        return -1, -1
    return pick[0], pick[1]


def get_character_color(np_img=None, near=None):
    """Character (x,y) via COLOR (bright yellow blob) on the minimap. Far more robust than
    template matching (which returns a phantom (122,188) at some spots). If `near`=(x,y) is
    given and several yellow blobs exist, pick the closest (temporal continuity); else the
    largest plausible blob. With no `np_img` grabs ONLY the minimap ROI (capture_minimap);
    pass a full BGR frame to crop it instead (shared-frame callers). (-1,-1) if none."""
    mm = core._minimap(np_img) if np_img is not None else core.capture_minimap()
    if mm is None:
        return -1, -1
    return core._char_color_from_mm(mm, near=near)


def _pick_char_blob(blobs, near=None):
    """Choose the character dot among yellow blobs, rejecting buff-glow phantoms.
    Prefer blobs inside the valid map box (MAP_X/Y_MIN/MAX); fall back to all blobs
    only when NONE are in-box (e.g. a genuine deep fall) so recovery can still see
    her. Within the candidates: nearest to `near` if given, else the largest."""
    if not blobs:
        return None
    in_box = [b for b in blobs
              if core.MAP_X_MIN <= b[0] <= core.MAP_X_MAX and core.MAP_Y_MIN <= b[1] <= core.MAP_Y_MAX]
    cand = in_box or blobs
    if near is not None and near[0] >= 0:
        return min(cand, key=lambda b: (b[0]-near[0])**2 + (b[1]-near[1])**2)
    return max(cand, key=lambda b: b[2])


def get_character_full(np_img=None, near=None):
    """Character (x, y) on the FULL minimap. Color first (robust), template as a fallback --
    both read the SAME crop (one grab): the minimap ROI when no frame is passed, else the
    supplied full frame cropped. (-1,-1) if neither finds it."""
    mm = core._minimap(np_img) if np_img is not None else core.capture_minimap()
    if mm is None:
        return -1, -1
    x, y = core._char_color_from_mm(mm, near=near)
    if x >= 0:
        return x, y
    centers = core.detect_character_on_minimap(mm, templates_folder=core.CHAR_TPL, threshold=0.7)
    if centers:
        return centers[0][0], centers[0][1]
    return -1, -1


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
        a = core.get_character_full()
        core.time.sleep(0.02)
        b = core.get_character_full()
    else:
        a = core.get_character_full(near=n)
        core.time.sleep(0.02)
        b = core.get_character_full(near=n)
        def _far(p):
            return p[0] >= 0 and (abs(p[0] - n[0]) > core._SWIM_MAX_JUMP or abs(p[1] - n[1]) > core._SWIM_MAX_JUMP)
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
        hsv = core.cv2.cvtColor(mm, core.cv2.COLOR_BGR2HSV)
        dot = core.cv2.inRange(hsv, core.np.array([18, 120, 120]), core.np.array([45, 255, 255]))
        g = core.cv2.cvtColor(mm, core.cv2.COLOR_BGR2GRAY).astype(core.np.float32)
        g[dot > 0] = 0                                    # drop the dot -> terrain only
        return g * core.cv2.createHanningWindow((g.shape[1], g.shape[0]), core.cv2.CV_32F)
    (_sx, sy), resp = core.cv2.phaseCorrelate(_prep(prev_crop), _prep(cur_crop))
    return (sy if resp >= min_resp else 0.0), resp
