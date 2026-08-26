"""Train YOLO11n on the SYNTHETIC water-mob dataset (fishhouse, goby).

Prereqs: build the dataset first --
    python synth_data.py --extract "<recording.mp4>" assets/bg   # backgrounds
    python synth_data.py --bg assets/bg --out datasets/mobs --n 3000

Then:
    python train_mobs.py [epochs]        # default 80

Writes models/mob_yolo.pt. Review runs/detect/mob_yolo/results.png before trusting it.
"""
import os
import sys
import shutil

DATA_ROOT = os.path.join("datasets", "mobs")


def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    yaml_path = os.path.join(DATA_ROOT, "data.yaml")
    if not os.path.exists(yaml_path):
        print(f"[train] {yaml_path} missing -- run synth_data.py first"); return

    from ultralytics import YOLO                  # lazy import
    model = YOLO("yolo11n.pt")                     # pretrained nano, auto-downloads
    model.train(data=yaml_path, epochs=epochs, imgsz=960, batch=-1,
                device=0, fliplr=0.0, mosaic=1.0, name="mob_yolo")
    best = os.path.join("runs", "detect", "mob_yolo", "weights", "best.pt")
    os.makedirs("models", exist_ok=True)
    shutil.copy(best, os.path.join("models", "mob_yolo.pt"))
    print(f"[train] copied {best} -> models/mob_yolo.pt")
    print("[train] review runs/detect/mob_yolo/results.png before trusting it")


if __name__ == "__main__":
    main()
