"""Train YOLO11n on the SYNTHETIC water-mob dataset (fishhouse, goby).

The HYBRID (--real) set is 3-class -- fishhouse, goby, AND player: hand-label the player's
box (INCLUDING the red HP bar above the head) in real frames so YOLO learns the HP bar as
the player feature and the same single inference locates both mobs and the player. Record
the label video with the HP bar visible on-screen for the player class to train.

Prereqs: build the dataset first --
    python synth_data.py --extract "<recording.mp4>" assets/bg   # backgrounds
    python synth_data.py --bg assets/bg --out datasets/mobs --n 3000

Then:
    python train_mobs.py [epochs] [imgsz]    # default 80 640
    # quick sanity run:  python train_mobs.py 25 640
    # HYBRID (mix in hand-labeled real frames from label_mobs.py):
    #   python train_mobs.py 80 640 --real

Writes models/mob_yolo.pt. Review runs/detect/mob_yolo/results.png before trusting it.
Fish are big-ish, so imgsz 640 trains ~2x faster than 960 AND infers faster in the loop.
Early-stops (patience 20) when val mAP plateaus, so you rarely need the full epoch count.
"""
import os
import sys
import shutil

DATA_ROOT = os.path.join("datasets", "mobs")
REAL_ROOT = os.path.join("datasets", "mobs_real")


def _hybrid_yaml():
    """Write a data.yaml training on BOTH the synthetic set and the hand-labeled real
    frames (both train + val), so a few real images fine-tune the synthetic detector."""
    import glob
    real_imgs = os.path.abspath(os.path.join(REAL_ROOT, "images"))
    if not glob.glob(os.path.join(real_imgs, "*.png")):
        print(f"[train] no real frames in {real_imgs} -- label some with label_mobs.py first")
        return None
    p = os.path.join(DATA_ROOT, "data_hybrid.yaml")
    syn = os.path.abspath(DATA_ROOT)
    with open(p, "w") as fh:
        fh.write(f"train:\n  - {syn}/images/train\n  - {real_imgs}\n")
        fh.write(f"val:\n  - {syn}/images/val\n  - {real_imgs}\n")
        fh.write("nc: 3\nnames: [fishhouse, goby, player]\n")
    print(f"[train] hybrid: synthetic + real frames ({real_imgs})")
    return p


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_real = "--real" in sys.argv
    epochs = int(args[0]) if len(args) > 0 else 80
    imgsz = int(args[1]) if len(args) > 1 else 640
    yaml_path = _hybrid_yaml() if use_real else os.path.join(DATA_ROOT, "data.yaml")
    if not yaml_path or not os.path.exists(yaml_path):
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
