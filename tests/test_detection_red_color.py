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


# --- template detector: a hit must actually contain red (flat patches score spurious matches) ---

def test_template_detects_a_real_pasted_red_dot():
    rng = np.random.default_rng(0)
    bg = rng.integers(40, 90, size=(150, 400, 3), dtype=np.uint8)   # textured minimap-ish
    tpl = cv2.imread("assets/minimap_other_character/red_dot.png")
    frame = _paste(bg, tpl, 200, 60)
    assert len(detection.detect_red_dots(frame, "assets/minimap_other_character/", 0.75)) == 1


def test_template_rejects_the_flat_grey_sky_false_alarm():
    # flat_sweep 2026-09-24 02:05:19: the template scored >= 0.75 on featureless grey sky in the
    # minimap (0 red pixels) -> another-player ALARM at farm start with nobody there.
    f = cv2.imread("datasets/enemy_dots/samples/20260924_020519_424_enemy_t1_c7_agree.png")
    if f is None:
        import pytest
        pytest.skip("local-only alarm sample not present")
    mm = f[185:335, 15:415]
    assert detection.detect_red_dots(mm, "assets/minimap_other_character/", 0.75) == []
