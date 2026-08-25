"""Tests for the player HP-bar anchor (synthetic frames)."""
import numpy as np
import player


def _frame(bars):
    """bars: list of (cx, cy, w, h) red bars painted on a teal frame."""
    f = np.full((1000, 1600, 3), (120, 90, 30), np.uint8)   # tealish bg (BGR)
    for cx, cy, w, h in bars:
        f[cy - h // 2:cy + h // 2, cx - w // 2:cx + w // 2] = (30, 30, 230)  # bright red
    return f


def test_bar_candidates_finds_bar_and_rejects_blob():
    f = _frame([(800, 400, 60, 6)])          # a bar
    f[500:560, 300:360] = (30, 30, 230)      # a big square red blob (not bar-shaped)
    cands = player.bar_candidates(f)
    assert any(abs(c[0] - 800) <= 3 and abs(c[1] - 400) <= 3 for c in cands)
    assert all(not (abs(c[0] - 330) <= 5 and abs(c[1] - 530) <= 5) for c in cands)


def test_pick_center_prefers_bar_near_screen_center():
    f = _frame([(300, 400, 60, 6), (805, 430, 60, 6)])   # off-center + near-center(800)
    cands = player.bar_candidates(f)
    chosen = player.pick_center(cands, f.shape)
    assert abs(chosen[0] - 805) <= 4          # picks the centered one (monster-bar reject)


def test_find_player_returns_feet_below_bar():
    f = _frame([(805, 430, 60, 6)])
    p = player.find_player(f, cfg={"foot_offset": 135})
    assert p is not None
    assert abs(p[0] - 805) <= 4
    assert abs(p[1] - (430 + 135)) <= 4       # feet = bar_y + offset


def test_find_player_none_when_no_bar():
    f = np.full((1000, 1600, 3), (120, 90, 30), np.uint8)
    assert player.find_player(f) is None


def test_find_player_debug_shape():
    f = _frame([(805, 430, 60, 6)])
    d = player.find_player(f, debug=True)
    assert set(d) == {"player", "bar", "candidates"}
    assert d["bar"] is not None and len(d["candidates"]) >= 1
