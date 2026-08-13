"""Blue-wing-dragon detection & counting (offline-testable).

Ported from MapleStoryAutoLevelUp get_monsters_in_range: match green-screen
dragon sprites (chroma masked) against a screen-frame ROI, NMS, count.
"""
import os, glob, re
import cv2
import numpy as np
from detection import apply_nms

GREEN_BGR = (0, 255, 0)
DEFAULT_TEMPLATE_DIR = r"C:\jerry\toy_work\maple\MapleStoryAutoLevelUp\monster\blue_wing_dragon"

def build_mask(img_bgr):
    green = cv2.inRange(img_bgr, np.array(GREEN_BGR), np.array(GREEN_BGR))
    return cv2.bitwise_not(green)

def _downscale(img, mask, max_dim):
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= max_dim:
        return img, mask
    s = max_dim / float(m)
    size = (max(1, int(w * s)), max(1, int(h * s)))
    img2 = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    mask2 = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    return img2, mask2

def _frame_num(path):
    m = re.search(r'(\d+)', os.path.basename(path))
    return int(m.group(1)) if m else 0

def load_templates(folder=DEFAULT_TEMPLATE_DIR, max_templates=12, max_dim=120):
    paths = sorted(glob.glob(os.path.join(folder, "blue_wing_dragon*.png")), key=_frame_num)
    out, seen = [], set()
    for p in paths:
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            continue
        key = (img.shape[1], img.shape[0], int(img.sum()))   # cheap dedup
        if key in seen:
            continue
        seen.add(key)
        mask = build_mask(img)
        out.append(_downscale(img, mask, max_dim))
        if len(out) >= max_templates:
            break
    return out

def detect_dragons(frame_bgr, roi, templates, diff_thres=0.35):
    x0, y0, x1, y1 = roi
    sub = frame_bgr[y0:y1, x0:x1]
    if sub.size == 0:
        return []
    boxes = []
    for img, mask in templates:
        th, tw = img.shape[:2]
        if th > sub.shape[0] or tw > sub.shape[1]:
            continue
        res = cv2.matchTemplate(sub, img, cv2.TM_SQDIFF_NORMED, mask=mask)
        res = np.nan_to_num(res, nan=1.0, posinf=1.0, neginf=1.0)  # masked SQDIFF can NaN
        ys, xs = np.where(res <= diff_thres)
        for px, py in zip(xs, ys):
            boxes.append([x0 + int(px), y0 + int(py),
                          x0 + int(px) + tw, y0 + int(py) + th])
    return [tuple(b) for b in apply_nms(boxes, overlapThresh=0.3)]

def count_from_frames(frames, roi, templates, diff_thres=0.35):
    counts = [len(detect_dragons(f, roi, templates, diff_thres)) for f in frames]
    if not counts:
        return 0
    counts.sort()
    return counts[len(counts) // 2]

def is_depleted(count, threshold=2):
    return count < threshold

import time

DEFAULT_ROI = (700, 250, 1900, 950)   # LIVE-TUNE: excludes minimap + bottom UI

# Per-node playfield ROIs. The camera follows the character and the two farming
# platforms are vertically stacked, so a single ROI would let the other platform's
# dragons inflate the count and suppress rotation. Scope the count to the node she
# is on. LIVE-TUNE: split the y so each ROI covers only its own platform's band.
ROI_BY_NODE = {
    "TOP_FARM":    (700, 250, 1900, 600),
    "BOTTOM_FARM": (700, 580, 1900, 950),
}

def count_dragons(capture_fn, templates, roi=DEFAULT_ROI, samples=3, interval=0.3, diff_thres=0.35):
    frames = []
    for i in range(samples):
        f = capture_fn()
        if f is not None:
            frames.append(f)
        if i < samples - 1 and interval:
            time.sleep(interval)
    return count_from_frames(frames, roi, templates, diff_thres)

# ---------------------------------------------------------------------------
# Motion-based counting (primary method).
#
# Template matching fails on this map: the reference sprites match the blue ice
# cave (false positives), and real-game templates only catch their exact pose
# (dragons animate + overlap). Since the dragons are the ONLY things moving while
# the character is parked, a per-count background plate (pixel median of a short
# burst of frames) cancels the static ice/platforms/UI/character, and the moving
# dragons remain as foreground. Foreground AREA / typical-dragon-area gives a
# pose-invariant, overlap-tolerant count. Verified live 2026-08-11.
# ---------------------------------------------------------------------------

# Playfield band to search (excludes the top-left minimap and the bottom UI).
# LIVE-TUNE per home spot; motion-subtraction ignores static scenery anyway.
DEFAULT_MOTION_ROI = (360, 70, 1910, 720)
# Per-node motion ROIs. The camera follows the character, so the dragons she is
# actually farming sit in a band relative to her home spot. Scope the count to that
# band so the other platform's dragons don't leak in. LIVE-TUNE both bands.
# Her-level farming lane per node (screen coords). Scoped to the band where her
# arrows reach on THAT platform, excluding other ledges visible in the same frame
# (e.g. the higher ledge above the top platform). Both measured live 2026-08-11
# with the character parked at each home (TOP x74,y85 / BOTTOM x63,y143..150).
# The camera clamps per area, so the OTHER platform is mostly off-screen; these
# bands exclude the extra ledges that remain in-frame. Still fine to LIVE-TUNE.
MOTION_ROI_BY_NODE = {
    "TOP_FARM":    (380, 500, 1910, 790),
    "BOTTOM_FARM": (380, 400, 1910, 820),
}
DRAGON_AREA = 16000      # typical foreground px per dragon (LIVE-TUNE)
FG_DIFF_THR = 45         # abs per-pixel diff (gray) counted as motion
FG_MIN_BLOB = 1500       # ignore foreground specks smaller than this

def background_plate(frames):
    """Per-pixel median of a burst of frames = the static scene (ice, platforms,
    UI, a parked character). Moving dragons average out. Needs >= 3 frames."""
    return np.median(np.stack(frames), axis=0).astype(np.uint8)

def foreground_area(frame, bg, roi, diff_thr=FG_DIFF_THR, min_blob=FG_MIN_BLOB):
    """Total moving-foreground pixel area inside `roi` (dragons), plus the blob
    boxes (full-frame coords). Morphology joins dragon parts and drops specks."""
    x0, y0, x1, y1 = roi
    d = cv2.absdiff(frame, bg)
    g = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
    m = (g > diff_thr).astype(np.uint8) * 255
    m[:y0, :] = 0; m[y1:, :] = 0; m[:, :x0] = 0; m[:, x1:] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    n, _lab, st, _cen = cv2.connectedComponentsWithStats(m, 8)
    boxes, area = [], 0
    for i in range(1, n):
        a = int(st[i, 4])
        if a >= min_blob:
            area += a
            boxes.append((int(st[i, 0]), int(st[i, 1]),
                          int(st[i, 0] + st[i, 2]), int(st[i, 1] + st[i, 3])))
    return area, boxes

def estimate_count(area, dragon_area=DRAGON_AREA):
    return int(round(area / float(dragon_area)))

def count_dragons_motion(capture_fn, roi=DEFAULT_MOTION_ROI, samples=12, interval=0.08,
                         dragon_area=DRAGON_AREA, diff_thr=FG_DIFF_THR, min_blob=FG_MIN_BLOB):
    """Robust dragon count via motion. Grabs a short burst, builds a background
    plate, and returns the median per-frame foreground-area estimate. Returns 0 if
    it cannot get enough frames."""
    frames = []
    for i in range(samples):
        f = capture_fn()
        if f is not None:
            frames.append(f)
        if i < samples - 1 and interval:
            time.sleep(interval)
    if len(frames) < 3:
        return 0
    bg = background_plate(frames)
    areas = [foreground_area(f, bg, roi, diff_thr, min_blob)[0] for f in frames]
    areas.sort()
    return estimate_count(areas[len(areas) // 2], dragon_area)

# ---------------------------------------------------------------------------
# YOLO detection (primary once a model exists). Boxes -> count now, positions
# later. ultralytics/torch are imported LAZILY so offline tests never need them.
# ---------------------------------------------------------------------------
DRAGON_MODEL_PATH = os.path.join("models", "dragon_yolo.pt")

def box_center_in_roi(box, roi):
    x1, y1, x2, y2 = box[:4]
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx0, ry0, rx1, ry1 = roi
    return rx0 <= cx <= rx1 and ry0 <= cy <= ry1

def run_yolo(model, frame, conf=0.35):
    """Raw dragon boxes from the YOLO model: [(x1,y1,x2,y2,score), ...]."""
    res = model.predict(frame, conf=conf, verbose=False)[0]
    out = []
    for b in res.boxes:
        x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
        out.append((x1, y1, x2, y2, float(b.conf[0])))
    return out

def detect_dragons_yolo(frame, model, roi, conf=0.35):
    """YOLO boxes whose CENTER falls inside `roi` — the center filter replaces the
    old ROI mask and drops the other platform's dragons that leak into frame."""
    return [b for b in run_yolo(model, frame, conf) if box_center_in_roi(b, roi)]

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "motion":
        import recovery, time as _t
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
        frames = []
        t0 = _t.time()
        while _t.time() - t0 < secs:
            f = recovery.capture()
            if f is not None:
                frames.append(f)
        bg = background_plate(frames)
        roi = DEFAULT_MOTION_ROI
        areas = [foreground_area(f, bg, roi)[0] for f in frames]
        areas.sort(); med = areas[len(areas) // 2]
        n = estimate_count(med)
        print(f"frames={len(frames)}  median_fg_area={med}  est_dragons={n}  ROI={roi}")
        os.makedirs("debug_output", exist_ok=True)
        last = frames[-1]; _, boxes = foreground_area(last, bg, roi)
        vis = last.copy(); cv2.rectangle(vis, roi[:2], roi[2:], (0, 255, 255), 2)
        for (a, b, c, d) in boxes:
            cv2.rectangle(vis, (a, b), (c, d), (0, 0, 255), 3)
        cv2.putText(vis, f"est {n} dragons", (400, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
        cv2.imwrite("debug_output/dragons_motion.png", vis)
        print("wrote debug_output/dragons_motion.png")
    elif cmd == "test":
        import recovery                          # live capture lives here
        tpls = load_templates()
        frame = recovery.capture()
        boxes = detect_dragons(frame, DEFAULT_ROI, tpls)
        print(f"templates={len(tpls)}  dragons detected={len(boxes)}  ROI={DEFAULT_ROI}")
        os.makedirs("debug_output", exist_ok=True)
        vis = frame.copy()
        cv2.rectangle(vis, DEFAULT_ROI[:2], DEFAULT_ROI[2:], (0, 255, 255), 2)
        for (x1, y1, x2, y2) in boxes:
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.imwrite("debug_output/dragons_detected.png", vis)
        print("wrote debug_output/dragons_detected.png")
    else:
        print("usage: python monsters.py [motion <secs> | test]")
