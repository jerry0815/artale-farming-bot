"""farm_general player-x resolution: HP-bar anchor (default, character-free) with a fallback
to the model's player box (yolo_player mode), riding last_px on a miss."""
import pytest

farm_general = pytest.importorskip("farm_general")


class _StubAnchor:
    def __init__(self, loc):
        self._loc = loc
    def locate(self, frame):
        return self._loc


def test_hpbar_anchor_hit_gives_its_x():
    px, last = farm_general._player_x(_StubAnchor((432, 700)), frame=object(),
                                      pbox=(0.9, 100, 50, 40, 80), last_px=None)
    assert px == 432 and last == 432          # anchor wins over the model's player box


def test_hpbar_anchor_miss_rides_last_px():
    px, last = farm_general._player_x(_StubAnchor(None), frame=object(),
                                      pbox=None, last_px=310)
    assert px == 310 and last == 310


def test_hpbar_anchor_miss_no_last_returns_none():
    px, last = farm_general._player_x(_StubAnchor(None), frame=object(),
                                      pbox=None, last_px=None)
    assert px is None and last is None        # caller waits for a lock


def test_yolo_mode_uses_pbox_center_when_no_anchor():
    # anchor=None -> yolo_player mode: player from the model box center-x
    px, last = farm_general._player_x(None, frame=object(),
                                      pbox=(0.8, 200, 60, 40, 80), last_px=None)
    assert px == 220 and last == 220          # 200 + 40//2


def test_yolo_mode_no_box_rides_last_px():
    px, last = farm_general._player_x(None, frame=object(), pbox=None, last_px=150)
    assert px == 150 and last == 150
