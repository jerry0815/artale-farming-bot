"""Template-match fish counter for water maps (深海峽谷2).

The KenYu sprite set (bombing_fish_house / goby / bone_fish) ships as BGRA PNGs with a
baked-in pure-green chroma-key background. We build a match mask by keying out the green,
then masked-template-match each sprite in a screen ROI and NMS the hits to a count. This
plugs into recovery.farming_loop_water as the depletion `count_fn` for maps whose
monsters the dragon YOLO model doesn't know.

Everything here is pure/offline except that it operates on frames captured elsewhere;
mask building, IoU/NMS, and counting are unit-tested with synthetic images.

Scale, threshold, and ROI are MAP-SPECIFIC and need live tuning -- use
`python recovery.py fishcount --map maps/<name>.json` to dial them in.
"""
import glob
import os
import cv2
import numpy as np

DEFAULT_MONSTER_DIR = r"C:/jerry/toy_work/maple/MapleStoryAutoLevelUp/monster"
WATER_FISH = ["bombing_fish_house", "goby", "bone_fish"]


def mask_from_green(bgra_or_bgr):
    """uint8 mask (255=sprite, 0=background) by removing the pure-green chroma key.
    Accepts BGRA (alpha also honored) or BGR."""
    im = bgra_or_bgr
    b = im[:, :, 0].astype(int)
    g = im[:, :, 1].astype(int)
    r = im[:, :, 2].astype(int)
    green = (g > 120) & (r < 100) & (b < 100)
    m = np.where(green, 0, 255).astype(np.uint8)
    if im.ndim == 3 and im.shape[2] == 4:
        m[im[:, :, 3] == 0] = 0
    return m


def load_templates(species=WATER_FISH, base=DEFAULT_MONSTER_DIR, per_species=None, scale=1.0):
    """Return [(name, bgr, mask)] for the given species. `per_species` caps the count
    per species (speed); `scale` resizes sprites toward the on-screen fish size."""
    out = []
    for sp in species:
        files = sorted(glob.glob(os.path.join(base, sp, "*.png")))
        if per_species:
            files = files[:per_species]
        for f in files:
            im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
            if im is None or im.ndim != 3 or im.shape[2] < 3:
                continue
            mask = mask_from_green(im)
            bgr = im[:, :, :3].copy()
            if scale != 1.0:
                bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                mask = cv2.resize(mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            out.append((sp, bgr, mask))
    return out


def iou(a, b):
    """IoU of two (x, y, w, h) boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter == 0:
        return 0.0
    return inter / (aw * ah + bw * bh - inter)


def nms(dets, iou_thr=0.4):
    """Greedy non-max suppression. dets: [(score, x, y, w, h)]; keeps the highest score
    and drops boxes overlapping a kept one by more than iou_thr."""
    kept = []
    for d in sorted(dets, key=lambda d: -d[0]):
        if all(iou(d[1:], k[1:]) < iou_thr for k in kept):
            kept.append(d)
    return kept


def _match_one(frame_bgr, tmpl, mask, threshold):
    if frame_bgr.shape[0] < tmpl.shape[0] or frame_bgr.shape[1] < tmpl.shape[1]:
        return []
    res = cv2.matchTemplate(frame_bgr, tmpl, cv2.TM_CCORR_NORMED, mask=mask)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    ys, xs = np.where(res >= threshold)
    h, w = tmpl.shape[:2]
    return [(float(res[y, x]), int(x), int(y), w, h) for y, x in zip(ys, xs)]


def detect_fish(frame_bgr, templates, roi=None, threshold=0.9, iou_thr=0.4):
    """NMS-deduped detections as [(score, x, y, w, h)] in FULL-frame coords (the roi
    offset is added back), so callers can draw them."""
    sub = frame_bgr
    ox, oy = 0, 0
    if roi is not None:
        x0, y0, x1, y1 = roi
        sub = frame_bgr[y0:y1, x0:x1]
        ox, oy = x0, y0
    dets = []
    for _name, tmpl, mask in templates:
        dets += _match_one(sub, tmpl, mask, threshold)
    return [(s, x + ox, y + oy, w, h) for (s, x, y, w, h) in nms(dets, iou_thr)]


def count_fish(frame_bgr, templates, roi=None, threshold=0.9, iou_thr=0.4):
    """Count distinct fish in `frame_bgr` (BGR). `roi`=(x0,y0,x1,y1) limits the search
    (screen coords). Returns the number of NMS-deduped matches at/above `threshold`."""
    return len(detect_fish(frame_bgr, templates, roi, threshold, iou_thr))
