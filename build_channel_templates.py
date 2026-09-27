"""Build the channel-hop button templates from a record_channel_switch.py session.

The game draws its OWN cursor, so each click frame has the cursor sitting on the button. For each
button we crop its box from every recorded frame, keep the crops whose surrounding GATE box looks like
the click frame's (NCC, and similar brightness -> no fade-in frames), and take the pixel-wise MEDIAN -> the moving cursor drops out.

Usage:  python build_channel_templates.py [session_dir]      (default: latest datasets/channel_switch/*)
Writes: assets/channel_hop/<name>.png + templates.json (box, click offset inside it) +  prints each template's match score over the stream
        (present vs absent) so the threshold in channel_hop.py can be sanity-checked.
"""
import glob, json, os, sys
import cv2
import numpy as np

OUT = os.path.join("assets", "channel_hop")
# name -> (click index in the session, template box, gate box, click point), all in window px of the
# 1940x1135 recording. The GATE box (bigger, so the cursor is a small part of it) picks which frames
# show the button; the template box is what gets matched live; the click point is where to click.
BOXES = {
    "menu":    (1, (1662, 1060, 1760, 1130), (1560, 1050, 1940, 1135), (1707, 1090)),  # 目錄 -- also "in map"
    "channel": (2, (1640, 975, 1800, 1025), (1630, 700, 1810, 1030), (1720, 1000)),    # 頻道 in the game menu
    "random":  (3, (1275, 230, 1360, 268), (560, 225, 1390, 320), (1316, 249)),        # random-channel icon
    "confirm": (4, (825, 535, 1115, 655), (825, 535, 1115, 655), (890, 634)),          # 是否要隨機前往頻道? 確定
    # login/select: the cursor sat ON the button in every frame, so these match a cursor-free
    # neighbour (account/password fields; the 新建角色/刪除角色 signs) and click at a fixed offset.
    "login":   (6, (1130, 685, 1315, 745), (1120, 665, 1410, 760), (1360, 716)),       # 登入 (bright, not fading in)
    "select":  (7, (1145, 530, 1315, 620), (1140, 450, 1320, 620), (1229, 491)),       # 選擇角色
}


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob("datasets/channel_switch/*/"))[-1]
    frames = sorted(glob.glob(os.path.join(d, "stream", "*.png"))) + \
             sorted(f for f in glob.glob(os.path.join(d, "clicks", "*.png")) if "_marked" not in f)
    imgs = [cv2.imread(f) for f in frames]
    os.makedirs(OUT, exist_ok=True)
    meta = {}
    for name, (ci, (x0, y0, x1, y1), (gx0, gy0, gx1, gy1), (cx, cy)) in BOXES.items():
        gate = cv2.imread(os.path.join(d, "clicks", f"click_{ci:02d}.png"))[gy0:gy1, gx0:gx1]
        g_mean = gate.mean()                          # brightness gate: skip fade-in (dim) frames
        crops = [im[y0:y1, x0:x1] for im in imgs
                 if cv2.matchTemplate(im[gy0:gy1, gx0:gx1], gate, cv2.TM_CCOEFF_NORMED)[0, 0] > 0.75
                 and abs(im[gy0:gy1, gx0:gx1].mean() - g_mean) < 0.1 * g_mean]
        tmpl = np.median(np.stack(crops), axis=0).astype(np.uint8)
        cv2.imwrite(os.path.join(OUT, f"{name}.png"), tmpl)
        meta[name] = {"box": [x0, y0, x1, y1], "click": [cx - x0, cy - y0]}
        scores = sorted(cv2.matchTemplate(im, tmpl, cv2.TM_CCOEFF_NORMED).max() for im in imgs[:-1])
        print(f"{name:8s} from {len(crops):3d} crops  | stream max-score quantiles "
              f"{' '.join(f'{s:.2f}' for s in np.quantile(scores, [0, .25, .5, .75, .9, 1]))}")
    with open(os.path.join(OUT, "templates.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)


if __name__ == "__main__":
    main()
