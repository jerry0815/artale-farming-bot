"""HSV red-dot detector for the another-player minimap safety check."""
import cv2
import numpy as np
import detection


def _dark_minimap(h=120, w=200):
    """A dark minimap-like background (no red)."""
    return np.full((h, w, 3), 30, dtype=np.uint8)   # BGR near-black


def _paste(bg, tpl, x, y):
    h, w = tpl.shape[:2]
    out = bg.copy()
    out[y:y + h, x:x + w] = tpl
    return out


def test_detects_a_pasted_red_dot():
    bg = _dark_minimap()
    tpl = cv2.imread("assets/minimap_other_character/red_dot.png")   # real crop
    assert tpl is not None
    h, w = tpl.shape[:2]
    frame = _paste(bg, tpl, 100, 60)
    centers = detection.detect_red_dots_color(frame)
    assert len(centers) == 1
    cx, cy = centers[0]
    assert abs(cx - (100 + w // 2)) <= 4
    assert abs(cy - (60 + h // 2)) <= 4


def test_solo_background_is_empty():
    assert detection.detect_red_dots_color(_dark_minimap()) == []


def test_rejects_a_small_speck():
    bg = _dark_minimap()
    bg[10:12, 10:12] = (60, 60, 240)   # 4px of red -> below min_area
    assert detection.detect_red_dots_color(bg) == []


def test_two_players_two_centers():
    bg = _dark_minimap()
    tpl = cv2.imread("assets/minimap_other_character/red_dot.png")
    frame = _paste(_paste(bg, tpl, 40, 30), tpl, 140, 80)
    assert len(detection.detect_red_dots_color(frame)) == 2
