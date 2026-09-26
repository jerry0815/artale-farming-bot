"""Build datasets/mob_generic/ = a single-class ("mob") detector dataset pooled from every
hand-labeled map, so ONE model detects mobs across maps (fish/goby AND dragons) and serves as
a reusable warm-start / auto-labeler for future maps. The player is NOT a class here -- it is
located map-generally by the HP-bar / nametag anchor, so YOLO capacity goes entirely to mobs.

Sources (all human-labeled, images gitignored/local-only):
  datasets/mobs_real  fishhouse(0),goby(1) -> mob(0); player(2) DROPPED
  datasets/dragons    dragon(0)            -> mob(0)
Add a new map by dropping its {images,labels} pair into SOURCES below and re-running.

Images are hardlinked (no disk duplication); labels are rewritten single-class. Output:
datasets/mob_generic/{images,labels}, train.txt/val.txt (85/15 seeded), mob_generic.yaml.
Then train with: python train_generic_mob.py
"""
import glob
import os
import random
import shutil

ROOT = "datasets/mob_generic"
IMG = os.path.join(ROOT, "images")
LBL = os.path.join(ROOT, "labels")

# (label_dir, image_dir, prefix) -- every class in these maps collapses to mob(0); class 2
# (player) is dropped. Append new maps here.
SOURCES = [
    ("datasets/mobs_real/labels", "datasets/mobs_real/images", "real_"),
    ("datasets/dragons/labels", "datasets/dragons/images", "dragon_"),
    ("datasets/flat_sweep/labels", "datasets/flat_sweep/images", "flat_"),
    ("datasets/middle_road2/labels", "datasets/middle_road2/images", "mroad2_"),
]

# Empty label files are SKIPPED, not treated as negatives: in this project an empty label means
# "frame navigated past, not labeled" (it usually still contains mobs), so including it would
# teach the model those mobs are background. Only frames with >=1 box are used.
SKIP_EMPTY = True


def link(src, dst):
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)              # hardlink: same volume, no admin, no duplication
    except OSError:
        shutil.copy(src, dst)


def img_for(stem, img_dir):
    for ext in (".png", ".jpg", ".jpeg"):
        p = os.path.join(img_dir, stem + ext)
        if os.path.exists(p):
            return p
    return None


def main():
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT)            # rebuild clean
    os.makedirs(IMG); os.makedirs(LBL)
    stems, boxes = [], 0
    for lbl_dir, img_dir, prefix in SOURCES:
        for lp in sorted(glob.glob(os.path.join(lbl_dir, "*.txt"))):
            base = os.path.splitext(os.path.basename(lp))[0]
            ip = img_for(base, img_dir)
            if ip is None:
                continue
            out = []
            for ln in open(lp, encoding="utf-8"):
                p = ln.split()
                if len(p) != 5 or p[0] == "2":          # drop player -> HP-bar anchor handles it
                    continue
                out.append(f"0 {p[1]} {p[2]} {p[3]} {p[4]}")   # any mob class -> mob(0)
            if SKIP_EMPTY and not out:                  # unlabeled/navigated-past frame -> skip
                continue
            stem = prefix + base.replace(prefix, "")
            open(os.path.join(LBL, stem + ".txt"), "w", encoding="utf-8").write(
                "\n".join(out) + ("\n" if out else ""))
            link(ip, os.path.join(IMG, stem + os.path.splitext(ip)[1]))
            stems.append(stem + os.path.splitext(ip)[1]); boxes += len(out)

    random.seed(0); random.shuffle(stems)
    n_val = max(1, int(len(stems) * 0.15))
    val, train = stems[:n_val], stems[n_val:]
    absimg = os.path.abspath(IMG)
    open(os.path.join(ROOT, "train.txt"), "w", encoding="utf-8").write(
        "\n".join(os.path.join(absimg, s) for s in train) + "\n")
    open(os.path.join(ROOT, "val.txt"), "w", encoding="utf-8").write(
        "\n".join(os.path.join(absimg, s) for s in val) + "\n")
    with open(os.path.join(ROOT, "mob_generic.yaml"), "w", encoding="utf-8") as fh:
        fh.write(f"train: {os.path.abspath(os.path.join(ROOT, 'train.txt'))}\n")
        fh.write(f"val: {os.path.abspath(os.path.join(ROOT, 'val.txt'))}\n")
        fh.write("nc: 1\nnames:\n  0: mob\n")
    print(f"[build] frames={len(stems)} mob_boxes={boxes} train={len(train)} val={len(val)}")
    print(f"[build] yaml: {os.path.abspath(os.path.join(ROOT, 'mob_generic.yaml'))}")


if __name__ == "__main__":
    main()
