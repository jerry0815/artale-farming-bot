"""Grab a crisp, live-scale lie-check template from the running game window.

When a check screen (find-transparent-shape / name-the-monster / curse) is
actually showing in-game, run:

    python capture_lie_template.py transparent_title

It captures the game window, lets you drag a box around the distinctive part
(bold title text, an icon, the red banner), and saves the crop to
assets/lie_check/<name>.png. Templates cut this way match far more reliably
than the pre-seeded ones from low-res screenshots.

Controls in the selector window: drag a box, then ENTER/SPACE to save, C to
cancel.
"""
import os
import sys
import cv2
import numpy as np

TITLE = "MapleStory Worlds-Artale (?????)"
OUT_DIR = "assets/lie_check"


def capture(title=TITLE):
    import mss
    import pygetwindow as gw
    from screeninfo import get_monitors
    wins = gw.getWindowsWithTitle(title)
    if not wins:
        print(f"找不到視窗：{title}")
        return None
    w = wins[0]
    for m in get_monitors():
        if w.left >= m.x and w.right <= m.x + m.width and w.bottom >= m.y and w.top <= m.y + m.height:
            with mss.mss() as sct:
                shot = sct.grab({"left": w.left, "top": w.top, "width": w.width, "height": w.height})
                return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    # fallback: primary-monitor grab of the window rect
    with mss.mss() as sct:
        shot = sct.grab({"left": w.left, "top": w.top, "width": w.width, "height": w.height})
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


def main():
    if len(sys.argv) < 2:
        print("用法：python capture_lie_template.py <template_name>")
        print("例如：python capture_lie_template.py transparent_title")
        return
    name = sys.argv[1]
    if not name.endswith(".png"):
        name += ".png"

    img = capture()
    if img is None:
        return

    print("拖曳框選要當模板的區域，然後按 ENTER/SPACE 儲存，按 C 取消。")
    roi = cv2.selectROI("select lie-check template", img, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    x, y, w, h = roi
    if w == 0 or h == 0:
        print("未選取區域，取消。")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, name)
    cv2.imwrite(out_path, img[y:y + h, x:x + w])
    print(f"已儲存模板：{out_path}  ({w}x{h})")


if __name__ == "__main__":
    main()
