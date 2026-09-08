# tests/test_enemy_dots.py
"""Production another-player check reads red dots via HSV color, crop-clamped, crash-safe."""
import numpy as np
import recovery


def test_enemy_dots_crops_minimap_and_returns_color_centers(monkeypatch):
    seen = {}

    def fake_color(mm, *a, **k):
        seen["shape"] = mm.shape          # confirm it received the minimap crop, not the full frame
        return [[5, 6]]

    monkeypatch.setattr(recovery, "detect_red_dots_color", fake_color)
    frame = np.zeros((recovery.MM_Y + recovery.MM_H + 50,
                      recovery.MM_X + recovery.MM_W + 50, 3), dtype=np.uint8)
    dots = recovery._enemy_dots(frame)
    assert dots == [[5, 6]]
    assert seen["shape"][0] <= recovery.MM_H and seen["shape"][1] <= recovery.MM_W


def test_enemy_dots_none_frame_is_empty():
    assert recovery._enemy_dots(None) == []


def test_enemy_dots_swallows_detector_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cv2 blew up")

    monkeypatch.setattr(recovery, "detect_red_dots_color", boom)
    frame = np.zeros((recovery.MM_Y + recovery.MM_H + 50,
                      recovery.MM_X + recovery.MM_W + 50, 3), dtype=np.uint8)
    assert recovery._enemy_dots(frame) == []      # never propagates into the safety loop
