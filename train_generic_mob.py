"""Train the generic single-class ("mob") detector on the pooled dataset built by
build_generic_mob_dataset.py. Warm-starts from yolo11n and mirrors train_mobs.py params
(imgsz 640, fliplr off, patience 20). Writes models/mob_generic.pt (gitignored/local-only,
like the other trained models -- keep a backup).

    python build_generic_mob_dataset.py     # pool every map's mob labels first
    python train_generic_mob.py [epochs] [imgsz]   # default 80 640

Point a map config at it with "mob_model": "models/mob_generic.pt". It detects mobs across
maps; the player comes from the HP-bar / nametag anchor, not from this model.
"""
import os
import shutil
import sys


def main():
    from ultralytics import YOLO
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    imgsz = int(sys.argv[2]) if len(sys.argv) > 2 else 640
    yaml = os.path.abspath("datasets/mob_generic/mob_generic.yaml")
    if not os.path.exists(yaml):
        print("[train] run build_generic_mob_dataset.py first"); return
    model = YOLO("yolo11n.pt")
    print(f"[train] generic mob-only: data={yaml} epochs={epochs} imgsz={imgsz} (early-stop 20)")
    model.train(data=yaml, epochs=epochs, imgsz=imgsz, batch=-1, patience=20,
                device=0, fliplr=0.0, mosaic=1.0, name="mob_generic")
    best = str(model.trainer.best)
    os.makedirs("models", exist_ok=True)
    shutil.copy(best, os.path.join("models", "mob_generic.pt"))
    m = model.val(data=yaml, imgsz=imgsz, device=0)
    print(f"[train] copied {best} -> models/mob_generic.pt")
    print(f"[val] mAP50={m.box.map50:.3f} mAP50-95={m.box.map:.3f} P={m.box.mp:.3f} R={m.box.mr:.3f}")


if __name__ == "__main__":
    main()
