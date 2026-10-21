"""Build the channel-hop button templates from a record_channel_switch.py session.

The game draws its OWN cursor, so each click frame has the cursor sitting on the button. For each
button we crop its box from every recorded frame, keep the crops whose surrounding GATE box looks like
the click frame's (NCC, and similar brightness -> no fade-in frames), and take the pixel-wise MEDIAN -> the moving cursor drops out.

Usage:  python build_channel_templates.py [session_dir]      (default: latest datasets/channel_switch/*)
Writes: assets/channel_hop/<name>.png + templates.json (recording window size; per button: box,
        click offset inside it) + prints each template's match score over the stream (present vs
        absent) so the threshold in channel_hop.py can be sanity-checked.
"""
import glob, json, os, sys
import cv2
import numpy as np

OUT = os.path.join("assets", "channel_hop")
# name -> (click index in the session, template box, gate box, click point), all in window px of the
# 1915x1121 recording (2026-10-20, after the game update). The GATE box (bigger, so the cursor is a
# small part of it) picks which frames show the button; the template box is what gets matched live;
# the click point is where to click.
BOXES = {
    "menu":    (0, (1650, 1052, 1748, 1118), (1550, 1042, 1915, 1121), (1695, 1082)),  # 目錄 -- also "in map"
    "channel": (1, (1615, 958, 1775, 996), (1600, 870, 1785, 1015), (1695, 985)),      # 頻道 (above the cursor)
    "random":  (2, (1256, 225, 1341, 263), (541, 220, 1371, 315), (1297, 244)),        # random-channel icon
    "confirm": (3, (819, 531, 1109, 598), (819, 531, 1109, 651), (884, 630)),          # question text -> 確定
    # The cursor sits ON the button in every click frame, so these match a cursor-free neighbour
    # and click at a fixed offset from it.
    "login":   (4, (1080, 590, 1232, 655), (1060, 580, 1330, 700), (1276, 619)),       # fields -> 登入 (bright)
    "charsel": (5, (1240, 495, 1410, 600), (1240, 495, 1410, 600), (780, 644)),        # 新建/刪除 signs -> the character
    "select":  (6, (648, 405, 708, 512), (640, 395, 900, 550), (1352, 463)),           # info scroll labels -> 選擇角色
}


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob("datasets/channel_switch/*/"))[-1]
    frames = sorted(glob.glob(os.path.join(d, "stream", "*.png"))) + \
             sorted(f for f in glob.glob(os.path.join(d, "clicks", "*.png")) if "_marked" not in f)
    imgs = [cv2.imread(f) for f in frames]
    os.makedirs(OUT, exist_ok=True)
    # The game redraws its UI scaled to the window size, so channel_hop scales these templates by
    # (live window width / this width) at run time.
    meta = {"_window": [imgs[0].shape[1], imgs[0].shape[0]]}
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
