"""The game redraws its UI scaled to the window (from the top-left corner), so minimap reads
shift with the window size. With a reference width set (map cfg "minimap_ref_w"), the minimap crop
follows the window and is resized back to the reference scale: reads match at any window size.
Live 2026-10-29: window 1942 wide vs middle_road2 tuned at 1915 -> middle floor read 96, cfg 93."""
import cv2
import numpy as np
import pytest

import core


@pytest.fixture
def ref1915():
    old = (core.MM_X, core.MM_Y, core.MM_W, core.MM_H, core.MM_REF_W[0])
    core.set_minimap(20, 172, 352, 176)
    core.set_minimap_ref(1915)
    yield
    core.set_minimap(*old[:4]); core.MM_REF_W[0] = old[4]


def _frame_with_dot(x, y, w=1915, h=1121):
    f = np.zeros((h, w, 3), np.uint8)
    f[172:172 + 176, 20:20 + 352] = (60, 40, 30)                     # minimap background
    cv2.circle(f, (20 + x, 172 + y), 4, (0, 230, 255), -1)            # yellow player dot
    return f


def test_reference_size_reads_as_before(ref1915):
    f = _frame_with_dot(100, 93)
    assert core.get_character_full(f) == (100, 93)


@pytest.mark.parametrize("w", [1942, 1880])
def test_other_window_sizes_read_the_same_position(ref1915, w):
    k = w / 1915
    f = cv2.resize(_frame_with_dot(100, 93), (w, round(1121 * k)))     # the game scales its UI
    x, y = core.get_character_full(f)
    assert abs(x - 100) <= 1 and abs(y - 93) <= 1


def test_no_reference_keeps_fixed_pixels():
    old = core.MM_REF_W[0]; core.MM_REF_W[0] = None
    try:
        core.set_minimap(20, 172, 352, 176)
        f = cv2.resize(_frame_with_dot(100, 93), (1942, 1137))
        x, y = core.get_character_full(f)
        assert (x, y) != (100, 93)                                    # unscaled: shifted, as before
    finally:
        core.MM_REF_W[0] = old
