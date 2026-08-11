"""Sense the full multi-layer platform structure on the minimap (read-only).
Detects all platforms + the character and saves an annotated, enlarged map.
"""
import time
import cv2
import numpy as np
from PIL import Image
import mss, pygetwindow as gw
from screeninfo import get_monitors
from detection import detect_platforms_on_minimap, detect_character_on_minimap

TITLE = "MapleStory Worlds-Artale (?????)"
SP = r"C:\Users\jerry\AppData\Local\Temp\claude\C--jerry-toy-work-maple\76b73c35-1984-400a-a0fc-417f80626173\scratchpad"
MM_X, MM_Y, MM_W, MM_H = 20, 171, 229, 259


def capture():
    w = gw.getWindowsWithTitle(TITLE)[0]
    for m in get_monitors():
        if w.left >= m.x and w.right <= m.x + m.width and w.bottom >= m.y and w.top <= m.y + m.height:
            with mss.mss() as sct:
                shot = sct.grab({"left": w.left, "top": w.top, "width": w.width, "height": w.height})
                return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return None


def main():
    np_img = capture()
    ey = min(MM_Y + MM_H, np_img.shape[0]); ex = min(MM_X + MM_W, np_img.shape[1])
    mm = np_img[MM_Y:ey, MM_X:ex]
    rects = detect_platforms_on_minimap(mm, templates_folder="assets/minimap_platforms/", threshold=0.6)
    chars = detect_character_on_minimap(mm, templates_folder="assets/minimap_character/", threshold=0.75)
    print(f"platforms detected: {len(rects)}")
    for i, (x1, y1, x2, y2) in enumerate(sorted(rects, key=lambda r: r[1])):
        print(f"  platform {i}: x[{x1}-{x2}] y[{y1}-{y2}]")
    print("character:", chars[0] if chars else None)

    vis = cv2.resize(mm, (mm.shape[1]*3, mm.shape[0]*3), interpolation=cv2.INTER_NEAREST)
    for (x1, y1, x2, y2) in rects:
        cv2.rectangle(vis, (x1*3, y1*3), (x2*3, y2*3), (0, 165, 255), 2)
    if chars:
        cx, cy = chars[0]
        cv2.circle(vis, (cx*3, cy*3), 8, (0, 0, 255), 2)
        cv2.putText(vis, f"{cx},{cy}", (cx*3+8, cy*3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1)
    cv2.line(vis, (0, 145*3), (vis.shape[1], 145*3), (0, 255, 255), 1)
    Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)).save(SP + r"\platform_map.png")
    print("saved platform_map.png")


if __name__ == "__main__":
    main()
