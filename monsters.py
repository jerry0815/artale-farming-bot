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

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
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
        print("usage: python monsters.py test")
