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
