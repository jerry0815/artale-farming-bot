"""farm_general player-position resolution: HP-bar anchor (default, character-free) with a
fallback to the model's player box (yolo_player mode), riding the last position on a miss.
Returns (x, feet_y) -- feet_y feeds the same-platform filter."""
import pytest

farm_general = pytest.importorskip("farm_general")


class _StubAnchor:
    def __init__(self, loc):
        self._loc = loc
    def locate(self, frame):
        return self._loc


def test_hpbar_anchor_hit_gives_its_position():
    pos, last = farm_general._player_pos(_StubAnchor((432, 700)), frame=object(),
                                         pbox=(0.9, 100, 50, 40, 80), last=None)
    assert pos == (432, 700) and last == (432, 700)     # anchor wins over the model box


def test_hpbar_anchor_miss_rides_last_position():
    pos, last = farm_general._player_pos(_StubAnchor(None), frame=object(),
                                         pbox=None, last=(310, 890))
    assert pos == (310, 890) and last == (310, 890)


def test_hpbar_anchor_miss_no_last_returns_none():
    pos, last = farm_general._player_pos(_StubAnchor(None), frame=object(), pbox=None, last=None)
    assert pos is None and last is None                 # caller waits for a lock


def test_yolo_mode_uses_pbox_center_x_and_bottom_as_feet():
    # anchor=None -> yolo_player mode: (x + w//2, y + h) of the model's player box
    pos, last = farm_general._player_pos(None, frame=object(),
                                         pbox=(0.8, 200, 60, 40, 80), last=None)
    assert pos == (220, 140) and last == (220, 140)


def test_yolo_mode_no_box_rides_last_position():
    pos, last = farm_general._player_pos(None, frame=object(), pbox=None, last=(150, 890))
    assert pos == (150, 890)


def test_dump_attack_writes_an_annotated_frame(tmp_path):
    import numpy as np
    frame = np.zeros((300, 600, 3), np.uint8)
    mobs = [(0.9, 300, 100, 40, 60), (0.5, 100, 10, 40, 30)]      # counted + dropped (other level)
    path = farm_general._dump_attack(frame, mobs, [(320, 160)], (250, 170), "GATHER", 4, 195,
                                     200, 240, 1, outdir=str(tmp_path))
    assert path is not None and path.endswith("_seg4_GATHER.jpg")
    assert (frame == 0).all()                                     # annotates a copy, not the live frame


# --- after a portal the camera pans and the HP bar jumps far on screen; the sticky tracker lost
# her, re-acquired a red blob nearest screen center and followed it ~3 s (live: p drifted 821 -> 557
# while the minimap showed her walking right, then snapped to ~1030). Re-seed it at her usual spot.

class _Anchor:
    last = (557, 812)


def test_reanchor_seeds_the_tracker_at_her_usual_screen_spot():
    a = _Anchor()
    recent = [(1010, 712), (1030, 711), (1020, 710), (1147, 709), (1015, 712)]
    farm_general._reanchor(a, recent)
    assert a.last == (1020, 711)                         # median, not the stale wrong lock


def test_reanchor_without_history_forgets_the_lock():
    a = _Anchor()
    farm_general._reanchor(a, [])
    assert a.last is None                                 # -> find_player re-acquires fresh


def test_reanchor_ignores_yolo_mode():
    farm_general._reanchor(None, [(1, 2)])               # no HP-bar anchor: nothing to do
