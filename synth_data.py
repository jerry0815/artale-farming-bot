"""Synthetic YOLO detection dataset for water mobs.

Composites green-screen mob sprites (bombing_fish_house, goby) onto map-background
frames at random position / scale / flip / colour-jitter, writing EXACT YOLO bounding-box
labels for free. Trains a detector specific to this game's mobs with no hand-labeling --
the technique behind MapleStoryDetectionSampleGenerator (~99.8% mAP).

Pure/offline; the geometry (paste + label math) is unit-tested. Run as a CLI to build
a dataset:

    python synth_data.py --bg assets/bg --out datasets/mobs --n 3000

Backgrounds: dump frames from a recording with `--extract <video> assets/bg`.
"""
import os
import glob
import random
import cv2
import numpy as np
import fish

CLASSES = ["fishhouse", "goby"]
SPRITE_SPECIES = {"fishhouse": "bombing_fish_house", "goby": "goby"}


def water_mask(bgr):
    """Isolate a mob in a LIVE crop by removing teal/cyan water. Keeps non-water
    components >=4% of the crop (drops bubbles/noise, keeps rock + eyeball tubes)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    water = cv2.inRange(hsv, (80, 40, 20), (115, 255, 255))
    mask = cv2.bitwise_not(water)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    area = mask.shape[0] * mask.shape[1]
    keep = np.zeros_like(mask)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] >= 0.04 * area:
            keep[lab == i] = 255
    return keep


def load_live_cutouts(live_dir):
    """Live crops (real rendering) grouped by filename prefix into class indices.
    {class_idx: [(bgr, mask)]}, mask via water removal. Files: <class>_<n>.png."""
    out = {}
    for ci, cls in enumerate(CLASSES):
        items = []
        for f in sorted(glob.glob(os.path.join(live_dir, f"{cls}_*.png"))):
            im = cv2.imread(f, cv2.IMREAD_COLOR)
            if im is None:
                continue
            items.append((im, water_mask(im)))
        if items:
            out[ci] = items
    return out


def load_cutouts(base=fish.DEFAULT_MONSTER_DIR):
    """{class_idx: [(bgr, mask), ...]} from the green-screen sprites (green keyed out,
    fringe eroded)."""
    out = {}
    for ci, cls in enumerate(CLASSES):
        items = []
        for f in sorted(glob.glob(os.path.join(base, SPRITE_SPECIES[cls], "*.png"))):
            im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
            if im is None or im.ndim != 3 or im.shape[2] < 3:
                continue
            mask = fish.mask_from_green(im)
            mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=1)   # trim green fringe
            items.append((im[:, :, :3].copy(), mask))
        if items:
            out[ci] = items
    return out


def _jitter(bgr, rng):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.int16)
    hsv[..., 0] = (hsv[..., 0] + rng.randint(-6, 6)) % 180            # hue
    hsv[..., 1] = np.clip(hsv[..., 1] * rng.uniform(0.85, 1.15), 0, 255)  # sat
    hsv[..., 2] = np.clip(hsv[..., 2] * rng.uniform(0.75, 1.2), 0, 255)   # val
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def paste(bg, sprite_bgr, mask, x, y):
    """Alpha-composite sprite onto bg at top-left (x, y). Returns True if it fit."""
    h, w = mask.shape
    H, W = bg.shape[:2]
    if x < 0 or y < 0 or x + w > W or y + h > H:
        return False
    a = (mask.astype(np.float32) / 255.0)[..., None]
    roi = bg[y:y + h, x:x + w].astype(np.float32)
    bg[y:y + h, x:x + w] = (roi * (1 - a) + sprite_bgr.astype(np.float32) * a).astype(np.uint8)
    return True


def synth_image(bg, cutouts, rng, n_range=(1, 8), scale_range=(0.7, 1.3), flip_classes=(1,)):
    """Composite a random scene. Returns (image, [(cls, xc, yc, w, h) normalized])."""
    img = bg.copy()
    labels = []
    H, W = img.shape[:2]
    for _ in range(rng.randint(*n_range)):
        ci = rng.choice(list(cutouts))
        bgr, mask = cutouts[ci][rng.randrange(len(cutouts[ci]))]
        s = rng.uniform(*scale_range)
        bw, bh = max(8, int(bgr.shape[1] * s)), max(8, int(bgr.shape[0] * s))
        rb = cv2.resize(bgr, (bw, bh))
        rm = cv2.resize(mask, (bw, bh), interpolation=cv2.INTER_NEAREST)
        if ci in flip_classes and rng.random() < 0.5:
            rb, rm = cv2.flip(rb, 1), cv2.flip(rm, 1)
        rb = _jitter(rb, rng)
        if W - bw <= 0 or H - bh <= 0:
            continue
        x, y = rng.randint(0, W - bw), rng.randint(0, H - bh)
        if paste(img, rb, rm, x, y):
            labels.append((ci, (x + bw / 2) / W, (y + bh / 2) / H, bw / W, bh / H))
    return img, labels


def write_sample(out_dir, split, idx, img, labels):
    imdir = os.path.join(out_dir, "images", split)
    ladir = os.path.join(out_dir, "labels", split)
    os.makedirs(imdir, exist_ok=True)
    os.makedirs(ladir, exist_ok=True)
    cv2.imwrite(os.path.join(imdir, f"{idx:06d}.jpg"), img)
    with open(os.path.join(ladir, f"{idx:06d}.txt"), "w") as fh:
        for ci, xc, yc, w, h in labels:
            fh.write(f"{ci} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")


def write_yaml(out_dir):
    p = os.path.join(out_dir, "data.yaml")
    with open(p, "w") as fh:
        fh.write(f"path: {os.path.abspath(out_dir)}\n")
        fh.write("train: images/train\nval: images/val\n")
        fh.write(f"nc: {len(CLASSES)}\nnames: {CLASSES}\n")
    return p


def generate(bg_dir, out_dir, n=3000, val_frac=0.1, seed=0, base=fish.DEFAULT_MONSTER_DIR,
             live_dir=None, **synth_kw):
    """Build the full dataset. Deterministic given seed. Returns the data.yaml path.
    `live_dir` uses your real in-game crops (recommended -- matches the game); otherwise
    the green-screen sprites."""
    rng = random.Random(seed)
    cutouts = load_live_cutouts(live_dir) if live_dir else load_cutouts(base)
    if not cutouts:
        raise RuntimeError(f"no cutouts ({'live ' + live_dir if live_dir else base})")
    print(f"[synth] cutouts: " + ", ".join(f"{CLASSES[k]}={len(v)}" for k, v in cutouts.items()))
    bgs = sorted(glob.glob(os.path.join(bg_dir, "*.jpg")) + glob.glob(os.path.join(bg_dir, "*.png")))
    if not bgs:
        raise RuntimeError(f"no background images in {bg_dir}")
    n_val = max(1, int(n * val_frac))
    for i in range(n):
        bg = cv2.imread(rng.choice(bgs))
        if bg is None:
            continue
        img, labels = synth_image(bg, cutouts, rng, **synth_kw)
        write_sample(out_dir, "val" if i < n_val else "train", i, img, labels)
    return write_yaml(out_dir)


def extract_backgrounds(video_path, out_dir, every=15, limit=200):
    """Dump frames from a recording to use as backgrounds."""
    os.makedirs(out_dir, exist_ok=True)
    v = cv2.VideoCapture(video_path)
    n = int(v.get(cv2.CAP_PROP_FRAME_COUNT))
    saved = 0
    for f in range(0, n, every):
        if saved >= limit:
            break
        v.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, fr = v.read()
        if ok:
            cv2.imwrite(os.path.join(out_dir, f"bg_{f:06d}.jpg"), fr)
            saved += 1
    v.release()
    return saved


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", nargs=2, metavar=("VIDEO", "OUTDIR"),
                    help="dump background frames from a video and exit")
    ap.add_argument("--bg", default="assets/bg")
    ap.add_argument("--out", default="datasets/mobs")
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--live", default=None,
                    help="use real in-game crops from this dir (e.g. assets/mob/deep_sea_2)")
    a = ap.parse_args()
    if a.extract:
        got = extract_backgrounds(a.extract[0], a.extract[1])
        print(f"[synth] extracted {got} background frames -> {a.extract[1]}")
    else:
        yaml = generate(a.bg, a.out, n=a.n, seed=a.seed, live_dir=a.live)
        print(f"[synth] wrote {a.n} samples -> {a.out}\n[synth] data.yaml: {yaml}")
