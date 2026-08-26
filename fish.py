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


def templates_for(cfg, scale=None):
    """Pick the template set for a map config: LIVE crops from cfg['mob_template_dir']
    if that dir has PNGs (matched with CCOEFF), else the green KenYu sprites (masked
    CCORR). Returns (templates, mode) where mode is 'live' or 'sprite'."""
    scale = cfg.get("fish_scale", 1.0) if scale is None else scale
    d = cfg.get("mob_template_dir")
    if d and glob.glob(os.path.join(d, "*.png")):
        return load_live_templates(d, scale=scale, per_name=cfg.get("mob_template_limit")), "live"
    return (load_templates(species=cfg.get("fish_species", WATER_FISH),
                           per_species=cfg.get("fish_per_species", 3), scale=scale),
            "sprite")


def default_threshold(mode):
    """A sane starting match threshold per template mode."""
    return 0.6 if mode == "live" else 0.9


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


def load_live_templates(dirpath, scale=1.0, per_name=None):
    """Load LIVE mob crops (tight screenshots from this game, no green screen) as
    [(name, bgr, None)]. mask=None signals CCOEFF matching -- far more robust than
    green-sprite CCORR. Name is the filename minus a trailing _<n>. `per_name` caps how
    many crops per mob (speed: fewer templates -> faster scan)."""
    import re
    from collections import defaultdict
    counts = defaultdict(int)
    out = []
    for f in sorted(glob.glob(os.path.join(dirpath, "*.png"))):
        name = re.sub(r"_\d+$", "", os.path.splitext(os.path.basename(f))[0])
        if per_name and counts[name] >= per_name:
            continue
        im = cv2.imread(f, cv2.IMREAD_COLOR)      # BGR, drop any alpha
        if im is None:
            continue
        if scale != 1.0:
            im = cv2.resize(im, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        counts[name] += 1
        out.append((name, im, None))
    return out


def _match_res(frame_bgr, tmpl, mask):
    """Normalized match map: CCOEFF for live crops (mask None), masked CCORR for sprites."""
    if mask is None:
        res = cv2.matchTemplate(frame_bgr, tmpl, cv2.TM_CCOEFF_NORMED)
    else:
        res = cv2.matchTemplate(frame_bgr, tmpl, cv2.TM_CCORR_NORMED, mask=mask)
    return np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)


def _downscaled(frame_bgr, roi, templates, downscale):
    if not downscale or downscale == 1.0:
        return frame_bgr, roi, templates
    frame_bgr = cv2.resize(frame_bgr, None, fx=downscale, fy=downscale, interpolation=cv2.INTER_AREA)
    if roi is not None:
        roi = tuple(int(round(v * downscale)) for v in roi)
    templates = [(n,
                  cv2.resize(t, None, fx=downscale, fy=downscale, interpolation=cv2.INTER_AREA),
                  None if m is None else
                  cv2.resize(m, None, fx=downscale, fy=downscale, interpolation=cv2.INTER_NEAREST))
                 for n, t, m in templates]
    return frame_bgr, roi, templates


def scan(frame_bgr, templates, roi=None, threshold=0.9, iou_thr=0.4, downscale=1.0):
    """One (optionally downscaled) matching pass. Returns
    {'best': {name: max_score}, 'dets': [(score, x, y, w, h)]} in FULL-frame coords.
    `best` is per-name max even below threshold (for tuning); `dets` is NMS-deduped."""
    frame_bgr, roi, templates = _downscaled(frame_bgr, roi, templates, downscale)
    sub, ox, oy = frame_bgr, 0, 0
    if roi is not None:
        x0, y0, x1, y1 = roi
        sub, ox, oy = frame_bgr[y0:y1, x0:x1], x0, y0
    best, raw = {}, []
    for name, tmpl, mask in templates:
        if sub.shape[0] < tmpl.shape[0] or sub.shape[1] < tmpl.shape[1]:
            continue
        res = _match_res(sub, tmpl, mask)
        best[name] = round(max(best.get(name, 0.0), float(res.max())), 3)
        h, w = tmpl.shape[:2]
        ys, xs = np.where(res >= threshold)
        raw += [(float(res[y, x]), int(x) + ox, int(y) + oy, w, h) for y, x in zip(ys, xs)]
    kept = nms(raw, iou_thr)
    if downscale and downscale != 1.0:
        inv = 1.0 / downscale
        kept = [(s, int(x * inv), int(y * inv), int(w * inv), int(h * inv))
                for (s, x, y, w, h) in kept]
    return {"best": best, "dets": kept}


def detect_fish(frame_bgr, templates, roi=None, threshold=0.9, iou_thr=0.4, downscale=1.0):
    """NMS-deduped detections [(score, x, y, w, h)] in FULL-frame coords. `downscale`
    (<1) shrinks frame + templates before matching for speed, boxes scaled back."""
    return scan(frame_bgr, templates, roi, threshold, iou_thr, downscale)["dets"]


def count_fish(frame_bgr, templates, roi=None, threshold=0.9, iou_thr=0.4, downscale=1.0):
    """Count distinct fish in `frame_bgr` (BGR). `roi`=(x0,y0,x1,y1) limits the search
    (screen coords). Returns the number of NMS-deduped matches at/above `threshold`."""
    return len(detect_fish(frame_bgr, templates, roi, threshold, iou_thr, downscale))
