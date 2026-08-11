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
