"""One-shot minimap diagnostic: capture once and dump EVERY yellow blob, so we can see
why the character read sticks/phantoms to a wrong (x,y). Run it while the bug is ON SCREEN
(the minimap shows the wrong dot / a stuck (95,136)).

Saves upscaled images to the repo dir:
  debug_mm.png        - the minimap crop (what the detector sees)
  debug_mm_tight.png  - the current character mask (bright yellow)
  debug_mm_loose.png  - a looser yellow mask (reveals dim dots / rope highlights)

Usage:  python debug_minimap.py
"""
import time
import cv2
import numpy as np
import recovery as R


def _dump(mm, lo, hi, name):
    hsv = cv2.cvtColor(mm, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(lo), np.array(hi))
    num, _lab, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    blobs = sorted(((int(cents[i][0]), int(cents[i][1]), int(stats[i][4]))
                    for i in range(1, num)), key=lambda b: -b[2])
    print(f"  {name:6s} blobs (cx,cy,area): {blobs[:12]}")
    big = cv2.resize(mask, (mask.shape[1] * 4, mask.shape[0] * 4), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(f"debug_mm_{name}.png", big)


def main():
    if not R.focus():
        print("could not focus game window"); return
    time.sleep(0.3)
    img = R.capture()
    if img is None:
        print("no capture"); return
    print("get_character_full ->", R.get_character_full(img))
    print("get_character_color ->", R.get_character_color(img))
    mm = R._minimap(img)
    _dump(mm, [20, 165, 165], [36, 255, 255], "tight")     # current char mask
    _dump(mm, [18, 80, 110], [40, 255, 255], "loose")       # dim yellow / rope highlights
    big = cv2.resize(mm, (mm.shape[1] * 4, mm.shape[0] * 4), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite("debug_mm.png", big)
    print("saved debug_mm.png, debug_mm_tight.png, debug_mm_loose.png "
          "(MM crop is 4x; divide coords by 4 for detector frame)")


if __name__ == "__main__":
    main()
