"""Tests for the YOLO lie-check dataset builder helpers."""
import glob
import cv2
import numpy as np
import make_lie_check_data as mk


def test_to_yolo_label_normalizes():
    # box (10,20)-(110,80) in a 200x100 image -> center (60,50), size (100,60)
    assert mk.to_yolo_label((10, 20, 110, 80), 200, 100, 0) == "0 0.300000 0.500000 0.500000 0.600000\n"


def test_composite_pastes_in_bounds_and_reports_box():
    bg = np.zeros((100, 200, 3), np.uint8)
    popup = np.full((20, 40, 3), 255, np.uint8)
    img, box = mk.composite(bg, popup, alpha=0.8, pos=(50, 30), scale=1.0)
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= img.shape[1] and 0 <= y0 < y1 <= img.shape[0]
    assert img[y0 + 1, x0 + 1].max() > 0            # something was blended in


def test_locate_popup_finds_template():
    caps = glob.glob("datasets/lie_check/captures/*transparent_title*.png")
    if not caps:
        return                                       # no captures on this machine -> skip
    frame = cv2.imread(caps[0])
    box = mk.locate_popup(frame, "transparent_title.png")
    assert box is not None
    x0, y0, x1, y1 = box
    assert x1 > x0 and y1 > y0


# --- background negatives: popup-free frames with EMPTY labels. The model was trained with none,
# so it boxed flat_sweep's skyscrapers as monster_check at 0.84-0.97.

def _png(path, val):
    cv2.imwrite(str(path), np.full((40, 60, 3), val, np.uint8))


def test_add_backgrounds_writes_images_with_empty_labels(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    for i in range(6):
        _png(src / f"f{i}.png", i * 40)
    neg = tmp_path / "neg"; neg.mkdir()
    _png(neg / "fp.png", 7)
    out = tmp_path / "ds"
    n = mk.add_backgrounds(str(out), sources=[(str(src), 3)], negatives_dir=str(neg))
    imgs = sorted(p.name for p in (out / "images").glob("bg_*.png"))
    assert n == 4 and len(imgs) == 4                  # 3 diverse picks + 1 known false alarm
    for name in imgs:
        lbl = out / "labels" / name.replace(".png", ".txt")
        assert lbl.exists() and lbl.read_text() == ""   # empty label = "no popup here"


def test_add_backgrounds_replaces_old_backgrounds_but_keeps_positives(tmp_path):
    out = tmp_path / "ds"; (out / "images").mkdir(parents=True); (out / "labels").mkdir()
    _png(out / "images" / "bg_old.png", 1); (out / "labels" / "bg_old.txt").write_text("")
    _png(out / "images" / "real_00000.png", 1); (out / "labels" / "real_00000.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    src = tmp_path / "src"; src.mkdir(); _png(src / "a.png", 50)
    mk.add_backgrounds(str(out), sources=[(str(src), 5)], negatives_dir=None)
    names = sorted(p.name for p in (out / "images").glob("*.png"))
    assert "bg_old.png" not in names and "real_00000.png" in names


def test_held_out_frames_are_the_ones_not_picked(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    for i in range(5):
        _png(src / f"f{i}.png", i * 50)
    picked = mk.pick_backgrounds(str(src), 2)
    held = mk.held_out_backgrounds(str(src), 2)
    assert len(picked) == 2 and len(held) == 3 and not set(picked) & set(held)
