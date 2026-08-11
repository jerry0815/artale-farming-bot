"""Pure-logic tests for recovery helpers that don't need the live game.

recovery.py imports mss/pyautogui/pynput (installed here), so importing it is
fine; nothing at import time touches the screen or sends keys.
"""
import pytest

recovery = pytest.importorskip("recovery")


def test_pick_char_blob_prefers_in_box_over_corner_phantom():
    # real dot on a platform + a heavy-glow phantom at the minimap corner
    real = (64, 109, 54)
    phantom = (2, 242, 60)          # bigger, but out of the valid map box
    # even though the phantom is larger, the in-box real dot must win
    assert recovery._pick_char_blob([phantom, real])[:2] == (64, 109)


def test_pick_char_blob_rejects_documented_phantoms():
    real = (63, 143, 50)
    for ph in [(22, 232, 70), (2, 242, 40), (36, 153, 55), (28, 235, 80), (122, 188, 90)]:
        got = recovery._pick_char_blob([ph, real])
        assert got[:2] == (63, 143), f"phantom {ph} was picked over the real dot"


def test_pick_char_blob_near_prefers_in_box():
    real = (70, 90, 45)
    phantom = (30, 240, 45)
    # a stale `near` close to the phantom must NOT drag the pick out of the map box
    assert recovery._pick_char_blob([phantom, real], near=(28, 238))[:2] == (70, 90)


def test_pick_char_blob_falls_back_when_none_in_box():
    # genuine off-map read (deep fall): no in-box blob -> still return something so
    # recovery/panic can act, rather than losing her entirely
    deep = (120, 205, 50)
    assert recovery._pick_char_blob([deep])[:2] == (120, 205)


def test_pick_char_blob_empty_is_none():
    assert recovery._pick_char_blob([]) is None
