"""Pick a DIVERSE subset of captured frames for labeling/training. Frames grabbed at ~8fps
are mostly near-duplicates; training on them wastes effort and overfits. Farthest-point
sampling greedily selects the frames most different from those already chosen, so each kept
frame adds real variety (different mob layouts / positions). Reusable for any map's capture.

    python select_diverse_frames.py <images_dir> --n 100 [--out <subset_dir>] [--size 32]

Prints the selected filenames (and a diminishing-returns curve: the diversity gap at each
pick -- when it flattens, more frames from THIS capture add little; capture more varied play).
--out copies the selected images (+ any existing label .txt) into a subset dir.
"""
import glob
import os
import sys

import numpy as np


def frame_feature(img_bgr, size=32):
    """Cheap perceptual feature: downscaled grayscale, flattened float32. Captures overall
    scene (mob positions + scrolled background), so distance ~ how different two frames are."""
    import cv2
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, (size, size), interpolation=cv2.INTER_AREA)
    return small.astype("float32").flatten()


def farthest_point_select(feats, n):
    """Greedy farthest-point sampling. feats: (N, D) float array. Returns (indices, gaps):
    `indices` = up to n frame indices, well spread in feature space; `gaps[k]` = the distance
    from the k-th pick to the nearest already-selected frame (the diversity it added). gaps is
    non-increasing -- when it flattens, remaining frames are redundant with the selection."""
    feats = np.asarray(feats, dtype="float32")
    N = len(feats)
    if N == 0:
        return [], []
    n = min(n, N)
    # start from the frame farthest from the centroid (a distinctive extreme, deterministic)
    centroid = feats.mean(axis=0)
    first = int(np.argmax(((feats - centroid) ** 2).sum(axis=1)))
    idx = [first]
    gaps = [float("inf")]
    mind = ((feats - feats[first]) ** 2).sum(axis=1)          # min sq-dist to the selection
    for _ in range(1, n):
        nxt = int(np.argmax(mind))
        gaps.append(float(np.sqrt(mind[nxt])))
        idx.append(nxt)
        d = ((feats - feats[nxt]) ** 2).sum(axis=1)
        mind = np.minimum(mind, d)
    return idx, gaps


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    img_dir = args[0] if args else "datasets/flat_sweep/images"
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 100
    size = int(sys.argv[sys.argv.index("--size") + 1]) if "--size" in sys.argv else 32
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None

    import cv2
    files = sorted(glob.glob(os.path.join(img_dir, "*.png")))
    if not files:
        print(f"[select] no .png in {img_dir}"); return
    print(f"[select] {len(files)} frames -> computing features ...")
    feats = np.stack([frame_feature(cv2.imread(f), size) for f in files])
    idx, gaps = farthest_point_select(feats, n)
    sel = [files[i] for i in idx]

    # diminishing-returns curve: gap at a few checkpoints so you can see when it flattens
    print(f"[select] picked {len(sel)} diverse frames. diversity gap (bigger = more new variety):")
    for k in (1, 5, 10, 25, 50, 75, 100):
        if k <= len(gaps):
            print(f"    pick #{k:>3}: gap {gaps[k - 1]:8.1f}")
    print(f"[select] last pick gap {gaps[-1]:.1f} -- if this is small, more frames from this "
          f"capture add little; capture MORE VARIED play for further gains.")

    listing = os.path.join(os.path.dirname(img_dir) or ".", "diverse.txt")
    with open(listing, "w", encoding="utf-8") as fh:
        fh.write("\n".join(os.path.basename(s) for s in sel) + "\n")
    print(f"[select] wrote {listing} ({len(sel)} names)")

    # --prelabel <model.pt>: auto-label the selected frames that aren't already hand-labeled,
    # so you only REVIEW them (fast) instead of drawing from scratch. Hand labels are preserved.
    if "--prelabel" in sys.argv:
        model_path = sys.argv[sys.argv.index("--prelabel") + 1]
        from ultralytics import YOLO
        model = YOLO(model_path)
        lbl_dir = os.path.join(os.path.dirname(img_dir) or ".", "labels")
        os.makedirs(lbl_dir, exist_ok=True)
        conf = float(sys.argv[sys.argv.index("--conf") + 1]) if "--conf" in sys.argv else 0.4
        pre = kept = 0
        for s in sel:
            base = os.path.splitext(os.path.basename(s))[0]
            lp = os.path.join(lbl_dir, base + ".txt")
            if os.path.exists(lp) and [ln for ln in open(lp, encoding="utf-8") if ln.strip()]:
                kept += 1; continue                       # hand-labeled -> keep, don't overwrite
            im = cv2.imread(s); H, W = im.shape[:2]
            r = model.predict(im, imgsz=640, conf=conf, verbose=False)[0]
            lines = []
            for b in r.boxes:
                x0, y0, x1, y1 = b.xyxy[0].tolist()
                lines.append(f"0 {((x0 + x1) / 2) / W:.6f} {((y0 + y1) / 2) / H:.6f} "
                             f"{(x1 - x0) / W:.6f} {(y1 - y0) / H:.6f}")
            open(lp, "w", encoding="utf-8").write("\n".join(lines) + ("\n" if lines else ""))
            pre += 1
        print(f"[select] prelabeled {pre} frames with {model_path} ({kept} hand-labeled kept). "
              f"Review: python label_mobs.py --dir {os.path.dirname(img_dir)} --classes mob "
              f"--only {listing}")

    if out:
        os.makedirs(os.path.join(out, "images"), exist_ok=True)
        os.makedirs(os.path.join(out, "labels"), exist_ok=True)
        lbl_src = os.path.join(os.path.dirname(img_dir) or ".", "labels")
        import shutil
        for s in sel:
            base = os.path.splitext(os.path.basename(s))[0]
            shutil.copy(s, os.path.join(out, "images", os.path.basename(s)))
            lp = os.path.join(lbl_src, base + ".txt")
            if os.path.exists(lp):
                shutil.copy(lp, os.path.join(out, "labels", base + ".txt"))
        print(f"[select] copied {len(sel)} images (+ existing labels) -> {out}")


if __name__ == "__main__":
    main()
