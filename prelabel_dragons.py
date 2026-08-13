"""Bootstrap YOLO labels from the existing motion detector. Reads the captured
frame sequence, builds ONE background plate over all frames, and writes each
moving blob as a rough dragon box. These labels are deliberately noisy — open
them in LabelImg and CORRECT (drag/delete) rather than draw from scratch.

Usage:
    python prelabel_dragons.py [images_dir] [labels_dir]
      images_dir : default datasets/dragons/images
      labels_dir : default datasets/dragons/labels
"""
import os, sys, glob
import cv2
import monsters


def to_yolo_line(box, img_w, img_h, cls=0):
    x1, y1, x2, y2 = box[:4]
    cx = ((x1 + x2) / 2.0) / img_w
    cy = ((y1 + y2) / 2.0) / img_h
    w = (x2 - x1) / float(img_w)
    h = (y2 - y1) / float(img_h)
    return f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def main():
    images_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join("datasets", "dragons", "images")
    labels_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join("datasets", "dragons", "labels")
    os.makedirs(labels_dir, exist_ok=True)

    paths = sorted(glob.glob(os.path.join(images_dir, "*.png")))
    if not paths:
        print(f"[prelabel] no PNGs in {images_dir}"); return
    frames = [cv2.imread(p, cv2.IMREAD_COLOR) for p in paths]
    frames = [f for f in frames if f is not None]
    bg = monsters.background_plate(frames)
    roi = monsters.DEFAULT_MOTION_ROI

    total = 0
    for p, f in zip(paths, frames):
        h, w = f.shape[:2]
        _area, boxes = monsters.foreground_area(f, bg, roi)
        stem = os.path.splitext(os.path.basename(p))[0]
        with open(os.path.join(labels_dir, stem + ".txt"), "w", encoding="utf-8") as out:
            for b in boxes:
                out.write(to_yolo_line(b, w, h) + "\n")
        total += len(boxes)
    print(f"[prelabel] wrote labels for {len(paths)} frames, {total} rough boxes -> {labels_dir}/")
    print("[prelabel] now CORRECT them: labelImg", images_dir, os.path.join("datasets", "dragons", "classes.txt"))


if __name__ == "__main__":
    main()
