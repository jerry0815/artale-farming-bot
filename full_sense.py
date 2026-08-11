"""Feasibility test: detect the character on a FULL minimap crop (not the narrow
band the bot uses) and show ALL yellow-dot matches so we can tell which is the
character vs. other markers. Read-only.
"""
import ctypes, time
import cv2
import numpy as np
from PIL import Image
from detection import detect_character_on_minimap

TITLE = "MapleStory Worlds-Artale (?????)"
SP = r"C:\Users\jerry\AppData\Local\Temp\claude\C--jerry-toy-work-maple\76b73c35-1984-400a-a0fc-417f80626173\scratchpad"

# band the bot uses today (for reference): x 20..249, y 171..316
# full minimap map-area (generous): x 20..249, y 171..430
FX, FY, FW, FH = 20, 171, 229, 259


def capture():
    import mss, pygetwindow as gw
    from screeninfo import get_monitors
    w = gw.getWindowsWithTitle(TITLE)[0]
    for m in get_monitors():
        if w.left >= m.x and w.right <= m.x + m.width and w.bottom >= m.y and w.top <= m.y + m.height:
            with mss.mss() as sct:
                shot = sct.grab({"left": w.left, "top": w.top, "width": w.width, "height": w.height})
                return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return None


def main():
    np_img = capture()
    ey = min(FY + FH, np_img.shape[0]); ex = min(FX + FW, np_img.shape[1])
    mm = np_img[FY:ey, FX:ex]
    centers = detect_character_on_minimap(mm, templates_folder="assets/minimap_character/", threshold=0.75)
    print("full-crop size:", mm.shape[:2])
    print("all character-template matches (full-crop coords):", centers)
    # band-frame equivalents (band offset y=171 same x=20, but band height 145)
    print("  [band frame is identical x; y same since same offset] band cutoff at y=145")
    vis = cv2.resize(mm, (mm.shape[1]*3, mm.shape[0]*3), interpolation=cv2.INTER_NEAREST)
    for i, (cx, cy) in enumerate(centers):
        cv2.circle(vis, (cx*3, cy*3), 8, (0, 0, 255), 2)
        cv2.putText(vis, f"{i}:{cx},{cy}", (cx*3+8, cy*3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1)
    # draw the band cutoff line (y=145 in this crop == old crop bottom)
    cv2.line(vis, (0, 145*3), (vis.shape[1], 145*3), (0,255,255), 1)
    cv2.putText(vis, "old band cutoff", (5, 145*3-4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,255), 1)
    Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)).save(SP + r"\full_sense.png")
    print("saved full_sense.png")


if __name__ == "__main__":
    main()
