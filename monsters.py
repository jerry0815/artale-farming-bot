"""Blue-wing-dragon detection & counting (offline-testable).

Ported from MapleStoryAutoLevelUp get_monsters_in_range: match green-screen
dragon sprites (chroma masked) against a screen-frame ROI, NMS, count.
"""
import os, glob
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

def load_templates(folder=DEFAULT_TEMPLATE_DIR, max_templates=12, max_dim=120):
    paths = sorted(glob.glob(os.path.join(folder, "blue_wing_dragon*.png")))
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
