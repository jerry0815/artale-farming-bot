"""Build the YOLO lie-check dataset: datasets/lie_check_yolo/{images,labels}.

Positives come from (a) real frames whose popup we can AUTO-LOCATE by re-running the
template that already detects them (all transparent captures, curse frames), (b) a small
checked-in manifest boxes.json for frames templates can't locate (the monster popups and
synthetic bases), and (c) SYNTHETIC composites of a popup crop alpha-blended onto
popup-free backgrounds for the data-poor monster/curse classes. Run:

    python make_lie_check_data.py [n_synth]
"""
import os, sys, glob, json, random
import cv2
import numpy as np
from detection import LIE_CHECK_FRACTIONS

CLASSES = {"monster_check": 0, "curse": 1, "transparent_shape": 2}
SAMPLE_FOLDERS = {"monster": "monster_check", "curse": "curse", "transparent": "transparent_shape"}


def locate_popup(frame, template_name, templates_folder="assets/lie_check/"):
    """Best-match pixel box of template_name in frame (fraction-scaled like detect_lie_check),
    padded to the popup panel, or None."""
    tpl = cv2.imread(os.path.join(templates_folder, template_name))
    if tpl is None:
        return None
    ih, iw = frame.shape[:2]
    frac = LIE_CHECK_FRACTIONS.get(template_name)
    if frac is None:
        return None
    h0, w0 = tpl.shape[:2]
    bw = max(1, int(frac * iw)); bh = max(1, int(bw * h0 / w0))
    best = None
    for s in [round(0.85 + i * 0.05, 3) for i in range(8)]:
        tw, th = int(bw * s), int(bh * s)
        if tw < 8 or th < 8 or tw > iw or th > ih:
            continue
        scaled = cv2.resize(tpl, (tw, th))
        res = cv2.matchTemplate(frame, scaled, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if best is None or mv > best[0]:
            best = (mv, ml[0], ml[1], tw, th)
    if best is None:
        return None
    _, x, y, tw, th = best
    # pad the located text strip out to a rough popup panel (wider + much taller, clamped)
    px, py = int(tw * 0.5), int(th * 2.5)
    x0 = max(0, x - px); y0 = max(0, y - py)
    x1 = min(iw, x + tw + px); y1 = min(ih, y + th + py * 2)
    return (x0, y0, x1, y1)


def to_yolo_label(box, img_w, img_h, cls):
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2 / img_w; cy = (y0 + y1) / 2 / img_h
    w = (x1 - x0) / img_w; h = (y1 - y0) / img_h
    return f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n"


def composite(bg, popup_crop, alpha, pos, scale):
    """Alpha-blend a (possibly scaled) popup_crop onto a copy of bg at top-left `pos`.
    Returns (image, (x0,y0,x1,y1)). Clamps the crop to fit."""
    img = bg.copy()
    ph, pw = popup_crop.shape[:2]
    tw, th = max(1, int(pw * scale)), max(1, int(ph * scale))
    crop = cv2.resize(popup_crop, (tw, th))
    x0, y0 = pos
    x0 = max(0, min(x0, img.shape[1] - 1)); y0 = max(0, min(y0, img.shape[0] - 1))
    x1 = min(img.shape[1], x0 + tw); y1 = min(img.shape[0], y0 + th)
    crop = crop[: y1 - y0, : x1 - x0]
    roi = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (roi * (1 - alpha) + crop.astype(np.float32) * alpha).astype(np.uint8)
    return img, (x0, y0, x1, y1)


def _write(out_root, name, img, label_line):
    cv2.imwrite(os.path.join(out_root, "images", name + ".png"), img)
    with open(os.path.join(out_root, "labels", name + ".txt"), "w") as f:
        f.write(label_line)


def build(out_root="datasets/lie_check_yolo", n_synth=100, seed=0):
    random.seed(seed)
    img_dir = os.path.join(out_root, "images"); lbl_dir = os.path.join(out_root, "labels")
    # Clear prior generated frames so a rebuild is reproducible (no stale synth accumulating).
    for d in (img_dir, lbl_dir):
        if os.path.isdir(d):
            for f in glob.glob(os.path.join(d, "*")):
                os.remove(f)
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)
    manifest_path = os.path.join(out_root, "boxes.json")
    manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else {}
    n = 0

    # (a) auto-boxed real frames: transparent captures
    for p in glob.glob("datasets/lie_check/captures/*transparent_title*.png"):
        frame = cv2.imread(p)
        if frame is None:
            continue
        box = locate_popup(frame, "transparent_title.png")
        if box is None:
            print(f"[data] skip (no locate): {p}"); continue
        _write(out_root, f"real_{n:05d}", frame,
               to_yolo_label(box, frame.shape[1], frame.shape[0], CLASSES["transparent_shape"]))
        n += 1

    # (b) manifest frames (monster popups, curse): filename -> [class, x0,y0,x1,y1]
    popup_crops = {"monster_check": [], "curse": []}
    for fname, entry in manifest.items():
        frame = cv2.imread(fname)
        if frame is None:
            print(f"[data] manifest file missing: {fname}"); continue
        cls_name = entry[0]; x0, y0, x1, y1 = entry[1:]
        _write(out_root, f"real_{n:05d}", frame,
               to_yolo_label((x0, y0, x1, y1), frame.shape[1], frame.shape[0], CLASSES[cls_name]))
        n += 1
        if cls_name in popup_crops:
            popup_crops[cls_name].append(frame[y0:y1, x0:x1].copy())

    # (b2) browser-labeled real frames from samples/<class>/ (label_lie_check.py wrote the
    # boxes as class 0; the FOLDER sets the TRUE class). These are the accurate hand-drawn
    # samples -- also collected as popup crops for synthesis.
    for folder, cls_name in SAMPLE_FOLDERS.items():
        cls_id = CLASSES[cls_name]
        sdir = os.path.join("datasets", "lie_check", "samples", folder)
        ldir = os.path.join(sdir, "labels")
        for imgp in sorted(glob.glob(os.path.join(sdir, "*.png"))):
            stem = os.path.splitext(os.path.basename(imgp))[0]
            lblp = os.path.join(ldir, stem + ".txt")
            if not os.path.exists(lblp):
                print(f"[data] no label yet, skip: {imgp}"); continue
            frame = cv2.imread(imgp)
            if frame is None:
                continue
            boxes = [ln.split() for ln in open(lblp) if ln.strip()]
            if not boxes:
                continue
            label = "".join(f"{cls_id} {cx} {cy} {w} {h}\n" for _c, cx, cy, w, h in boxes)
            _write(out_root, f"real_{n:05d}", frame, label)   # remapped to the folder's class
            n += 1
            if cls_name in popup_crops:                       # crop the first box for synthesis
                H, W = frame.shape[:2]
                cx, cy, bw, bh = map(float, boxes[0][1:])
                x0 = max(0, int((cx - bw / 2) * W)); y0 = max(0, int((cy - bh / 2) * H))
                x1 = int((cx + bw / 2) * W); y1 = int((cy + bh / 2) * H)
                crop = frame[y0:y1, x0:x1]
                if crop.size:
                    popup_crops[cls_name].append(crop.copy())

    # (c) synthetic monster/curse: paste a popup crop onto popup-free backgrounds
    bgs = [cv2.imread(p) for p in (glob.glob("datasets/mobs_real/**/*.png", recursive=True)
           + glob.glob("debug_output/*.png") + ["datasets/lie_check/normal_test.png"])]
    bgs = [b for b in bgs if b is not None]
    if bgs:
        for cls_name, crops in popup_crops.items():
            if not crops:
                continue
            for _ in range(n_synth):
                bg = random.choice(bgs); crop = random.choice(crops)
                scale = random.uniform(0.8, 1.15); alpha = random.uniform(0.7, 0.95)
                pos = (random.randint(0, max(1, bg.shape[1] - crop.shape[1])),
                       random.randint(0, max(1, bg.shape[0] - crop.shape[0])))
                img, box = composite(bg, crop, alpha, pos, scale)
                _write(out_root, f"synth_{cls_name}_{n:05d}", img,
                       to_yolo_label(box, img.shape[1], img.shape[0], CLASSES[cls_name]))
                n += 1
    print(f"[data] wrote {n} labeled frames to {out_root}")
    return n


if __name__ == "__main__":
    build(n_synth=int(sys.argv[1]) if len(sys.argv) > 1 else 200)
