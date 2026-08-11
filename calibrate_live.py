"""READ-ONLY calibration reader. Sends NO key input, moves nothing.

Usage:  python calibrate_live.py <label> [samples]
Reads the character's minimap (x, y) N times and prints the median of each,
so you can capture ROPE_MINIMAP_X / the real platform y without eyeballing pixels.
"""
import sys
import time

import cv2
import numpy as np
import mss
import pygetwindow as gw
from screeninfo import get_monitors
from PIL import Image

from detection import detect_character_on_minimap

WINDOW_TITLE = "MapleStory Worlds-Artale (?????)"
MINIMAP_X_OFFSET, MINIMAP_Y_OFFSET = 20, 171
MINIMAP_WIDTH, MINIMAP_HEIGHT = 229, 145


def _capture():
    win = gw.getWindowsWithTitle(WINDOW_TITLE)
    if not win:
        return None
    w = win[0]
    for m in get_monitors():
        if w.left >= m.x and w.right <= m.x + m.width and w.bottom >= m.y and w.top <= m.y + m.height:
            with mss.mss() as sct:
                shot = sct.grab({"left": w.left, "top": w.top, "width": w.width, "height": w.height})
                return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return None


def get_character():
    np_img = _capture()
    if np_img is None:
        return -1, -1
    ex = min(MINIMAP_X_OFFSET + MINIMAP_WIDTH, np_img.shape[1])
    ey = min(MINIMAP_Y_OFFSET + MINIMAP_HEIGHT, np_img.shape[0])
    mm = np_img[MINIMAP_Y_OFFSET:ey, MINIMAP_X_OFFSET:ex]
    centers = detect_character_on_minimap(mm, templates_folder="assets/minimap_character/", threshold=0.75)
    return (centers[0][0], centers[0][1]) if centers else (-1, -1)


def _median(vals):
    s = sorted(vals)
    return s[len(s) // 2]


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "position"
    samples = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    xs, ys = [], []
    for i in range(samples):
        x, y = get_character()
        print(f"  {label} sample {i+1}: x={x}, y={y}")
        if x >= 0:
            xs.append(x)
            ys.append(y)
        time.sleep(0.35)
    if xs:
        print(f"\n[{label}] median x={_median(xs)}  y={_median(ys)}   "
              f"(x range {min(xs)}-{max(xs)}, y range {min(ys)}-{max(ys)}, n={len(xs)})")
    else:
        print(f"\n[{label}] character not detected on minimap")


if __name__ == "__main__":
    main()
