"""READ-ONLY live sanity check for the human-like layer.

Does NOT send any key input and does NOT move the character. It only:
  - finds the MapleStory window
  - captures a screenshot
  - reports the character's current minimap (x, y) via detection.py
  - reports whether any red dots (other players) are on the minimap

Run from the auto_train folder:  python live_check.py
"""
import sys
import time

import cv2
import numpy as np
import mss
import pygetwindow as gw
from screeninfo import get_monitors
from PIL import Image

from detection import detect_character_on_minimap, detect_red_dots

WINDOW_TITLE = "MapleStory Worlds-Artale (?????)"
MINIMAP_X_OFFSET = 20
MINIMAP_Y_OFFSET = 171
MINIMAP_WIDTH = 229
MINIMAP_HEIGHT = 145


def capture_window_screenshot(window_title=WINDOW_TITLE):
    window = gw.getWindowsWithTitle(window_title)
    if len(window) == 0:
        return None, None
    window = window[0]
    left, top, right, bottom = window.left, window.top, window.right, window.bottom
    width = right - left
    height = bottom - top
    for m in get_monitors():
        if left >= m.x and right <= m.x + m.width and bottom >= m.y and top <= m.y + m.height:
            with mss.mss() as sct:
                monitor = {"left": left, "top": top, "width": width, "height": height}
                shot = sct.grab(monitor)
                np_img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
                img = Image.frombytes("RGB", shot.size, shot.rgb)
                return img, np_img
    return None, None


def _minimap(np_img):
    ex = min(MINIMAP_X_OFFSET + MINIMAP_WIDTH, np_img.shape[1])
    ey = min(MINIMAP_Y_OFFSET + MINIMAP_HEIGHT, np_img.shape[0])
    return np_img[MINIMAP_Y_OFFSET:ey, MINIMAP_X_OFFSET:ex]


def get_character():
    _, np_img = capture_window_screenshot()
    if np_img is None:
        return -1, -1
    centers = detect_character_on_minimap(
        _minimap(np_img), templates_folder="assets/minimap_character/", threshold=0.75)
    if centers:
        return centers[0][0], centers[0][1]
    return -1, -1


def get_enemy():
    _, np_img = capture_window_screenshot()
    if np_img is None:
        return []
    return detect_red_dots(
        _minimap(np_img), templates_folder="assets/minimap_other_character/", threshold=0.75)


def main():
    win = gw.getWindowsWithTitle(WINDOW_TITLE)
    if not win:
        print(f"[FAIL] window not found: {WINDOW_TITLE!r}")
        print("       Open windows containing 'Maple':")
        for w in gw.getAllTitles():
            if "Maple" in w:
                print("        -", repr(w))
        sys.exit(1)
    w = win[0]
    print(f"[OK] window found: pos=({w.left},{w.top}) size=({w.width}x{w.height})")

    print("\nReading character position (read-only, 6 samples)...")
    for i in range(6):
        x, y = get_character()
        enemies = get_enemy()
        print(f"  sample {i+1}: char=({x}, {y})   red_dots={len(enemies)}")
        time.sleep(0.4)


if __name__ == "__main__":
    main()
