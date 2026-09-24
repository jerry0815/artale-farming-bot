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
