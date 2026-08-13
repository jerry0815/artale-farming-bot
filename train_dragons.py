"""Train YOLO11n on the corrected dragon dataset. Writes deterministic train/val
list files + dragons.yaml, then trains to models/dragon_yolo.pt.

Usage:
    python train_dragons.py [epochs]     # default 90
Prereqs: datasets/dragons/images/*.png with corrected datasets/dragons/labels/*.txt
"""
import os, sys, glob

DATA_ROOT = os.path.join("datasets", "dragons")

def assign_split(paths, val_every=5):
    """Deterministic split: every `val_every`-th sorted path -> val, rest -> train.
    val_every=5 gives ~20% val."""
    train, val = [], []
    for i, p in enumerate(sorted(paths)):
        (val if i % val_every == 0 else train).append(p)
    return train, val

def _write_list(path, items):
    with open(path, "w", encoding="utf-8") as f:
        for p in items:
            f.write(os.path.abspath(p) + "\n")

def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    images = sorted(glob.glob(os.path.join(DATA_ROOT, "images", "*.png")))
    if not images:
        print(f"[train] no images in {DATA_ROOT}/images"); return
    train, val = assign_split(images)
    train_txt = os.path.join(DATA_ROOT, "train.txt")
    val_txt = os.path.join(DATA_ROOT, "val.txt")
    _write_list(train_txt, train)
    _write_list(val_txt, val)

    yaml_path = os.path.join(DATA_ROOT, "dragons.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"train: {os.path.abspath(train_txt)}\n")
        f.write(f"val: {os.path.abspath(val_txt)}\n")
        f.write("names:\n  0: dragon\n")
    print(f"[train] {len(train)} train / {len(val)} val -> {yaml_path}")

    from ultralytics import YOLO               # lazy import
    model = YOLO("yolo11n.pt")                  # pretrained nano, auto-downloads
    model.train(data=yaml_path, epochs=epochs, imgsz=960, batch=-1,
                device=0, fliplr=0.5, mosaic=1.0, name="dragon_yolo")
    best = os.path.join("runs", "detect", "dragon_yolo", "weights", "best.pt")
    os.makedirs("models", exist_ok=True)
    import shutil
    shutil.copy(best, os.path.join("models", "dragon_yolo.pt"))
    print(f"[train] copied {best} -> models/dragon_yolo.pt")
    print("[train] review runs/detect/dragon_yolo/results.png before trusting it")

if __name__ == "__main__":
    main()
