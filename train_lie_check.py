"""Train YOLO11n on the lie-check dataset -> models/lie_check_yolo.pt.

Usage:  python train_lie_check.py [epochs]   # default 120
Prereqs: run `python make_lie_check_data.py` first (datasets/lie_check_yolo/{images,labels}).
"""
import os, sys, glob, shutil

DATA_ROOT = os.path.join("datasets", "lie_check_yolo")


def assign_split(paths, val_every=5):
    train, val = [], []
    for i, p in enumerate(sorted(paths)):
        (val if i % val_every == 0 else train).append(p)
    return train, val


def _write_list(path, items):
    with open(path, "w", encoding="utf-8") as f:
        for p in items:
            f.write(os.path.abspath(p) + "\n")


def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    # Keep synthetic frames out of val (they'd measure memorization, not generalization).
    real = sorted(glob.glob(os.path.join(DATA_ROOT, "images", "real_*.png")))
    synth = sorted(glob.glob(os.path.join(DATA_ROOT, "images", "synth_*.png")))
    if not real and not synth:
        print(f"[train] no images in {DATA_ROOT}/images -- run make_lie_check_data.py first"); return
    train_real, val = assign_split(real)
    train = train_real + synth
    _write_list(os.path.join(DATA_ROOT, "train.txt"), train)
    _write_list(os.path.join(DATA_ROOT, "val.txt"), val or train[:1])

    yaml_path = os.path.join(DATA_ROOT, "lie_check.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"train: {os.path.abspath(os.path.join(DATA_ROOT, 'train.txt'))}\n")
        f.write(f"val: {os.path.abspath(os.path.join(DATA_ROOT, 'val.txt'))}\n")
        f.write("names:\n  0: monster_check\n  1: curse\n  2: transparent_shape\n")

    from ultralytics import YOLO
    model = YOLO("yolo11n.pt")
    model.train(data=yaml_path, epochs=epochs, imgsz=1280, batch=-1,  # -1 = auto-fit GPU memory
                project="runs_lie_check", name="train", exist_ok=True)
    best = os.path.join("runs_lie_check", "train", "weights", "best.pt")
    os.makedirs("models", exist_ok=True)
    shutil.copy(best, os.path.join("models", "lie_check_yolo.pt"))
    print("[train] wrote models/lie_check_yolo.pt")


if __name__ == "__main__":
    main()
