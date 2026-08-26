"""Train YOLO11n on the SYNTHETIC water-mob dataset (fishhouse, goby).

Prereqs: build the dataset first --
    python synth_data.py --extract "<recording.mp4>" assets/bg   # backgrounds
    python synth_data.py --bg assets/bg --out datasets/mobs --n 3000

Then:
    python train_mobs.py [epochs] [imgsz]    # default 80 640
    # quick sanity run:  python train_mobs.py 25 640

Writes models/mob_yolo.pt. Review runs/detect/mob_yolo/results.png before trusting it.
Fish are big-ish, so imgsz 640 trains ~2x faster than 960 AND infers faster in the loop.
Early-stops (patience 20) when val mAP plateaus, so you rarely need the full epoch count.
"""
import os
import sys
import shutil

DATA_ROOT = os.path.join("datasets", "mobs")


def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    imgsz = int(sys.argv[2]) if len(sys.argv) > 2 else 640
    yaml_path = os.path.join(DATA_ROOT, "data.yaml")
    if not os.path.exists(yaml_path):
        print(f"[train] {yaml_path} missing -- run synth_data.py first"); return

    from ultralytics import YOLO                  # lazy import
    model = YOLO("yolo11n.pt")                     # pretrained nano, auto-downloads
    print(f"[train] epochs={epochs} imgsz={imgsz} (early-stop patience 20)")
    model.train(data=yaml_path, epochs=epochs, imgsz=imgsz, batch=-1, patience=20,
                device=0, fliplr=0.0, mosaic=1.0, name="mob_yolo")
    best = str(model.trainer.best)                 # actual run dir (handles mob_yolo-N)
    os.makedirs("models", exist_ok=True)
    shutil.copy(best, os.path.join("models", "mob_yolo.pt"))
    print(f"[train] copied {best} -> models/mob_yolo.pt")
    print("[train] review runs/detect/mob_yolo/results.png before trusting it")


if __name__ == "__main__":
    main()
