"""Unit tests for the 3-class mob YOLO split (mobs vs player) without loading ultralytics.

We fake the ultralytics result shape (`.boxes` with `.cls/.conf/.xyxy`) so the class
routing and the YoloPlayerAnchor geometry are testable on CPU with no model file.
"""
import numpy as np
import mob_detect


class _B:
    def __init__(self, cls, conf, xyxy):
        self.cls = [float(cls)]
        self.conf = [float(conf)]
        self.xyxy = [np.array(xyxy, dtype=float)]


class _Res:
    def __init__(self, boxes):
        self.boxes = boxes


class _FakeModel:
    """Returns preset boxes; records the sub-image it was asked to predict on."""
    def __init__(self, boxes):
        self._boxes = boxes
        self.calls = []

    def predict(self, sub, imgsz=640, conf=0.3, verbose=False):
        self.calls.append(sub.shape)
        return [_Res(self._boxes)]


def _frame():
    return np.zeros((400, 600, 3), dtype=np.uint8)


def test_yolo_detect_splits_mobs_and_player():
    boxes = [
        _B(0, 0.9, [10, 10, 30, 40]),      # fishhouse (mob)
        _B(1, 0.8, [50, 50, 70, 90]),      # goby (mob)
        _B(2, 0.7, [100, 100, 140, 200]),  # player
    ]
    mobs, player = mob_detect.yolo_detect(_FakeModel(boxes), _frame())
    assert len(mobs) == 2
    assert {m[0] for m in mobs} == {0.9, 0.8}       # scores of the two mobs
    assert player is not None
    assert player == (0.7, 100, 100, 40, 100)        # (score,x,y,w,h)


def test_yolo_boxes_excludes_player_class():
    boxes = [_B(0, 0.9, [10, 10, 30, 40]), _B(2, 0.99, [100, 100, 140, 200])]
    dets = mob_detect.yolo_boxes(_FakeModel(boxes), _frame())
    assert len(dets) == 1 and dets[0][0] == 0.9      # player (class 2) dropped


def test_yolo_detect_picks_highest_conf_player():
    boxes = [_B(2, 0.4, [0, 0, 10, 10]), _B(2, 0.85, [100, 100, 140, 200])]
    _mobs, player = mob_detect.yolo_detect(_FakeModel(boxes), _frame())
    assert player[0] == 0.85


def test_anchor_feet_is_box_bottom_center():
    boxes = [_B(2, 0.7, [100, 100, 140, 200])]        # x0=100,y0=100,x1=140,y1=200
    a = mob_detect.YoloPlayerAnchor(_FakeModel(boxes))
    assert a.locate(_frame()) == (120, 200)           # center x, bottom y
    assert a.last == (120, 200)


def test_anchor_foot_offset_applied():
    boxes = [_B(2, 0.7, [100, 100, 140, 200])]
    a = mob_detect.YoloPlayerAnchor(_FakeModel(boxes), foot_offset=5)
    assert a.locate(_frame()) == (120, 205)


def test_anchor_returns_none_when_no_player():
    boxes = [_B(0, 0.9, [10, 10, 30, 40])]            # only a mob, never any player
    a = mob_detect.YoloPlayerAnchor(_FakeModel(boxes), stale_grace=0)
    assert a.locate(_frame()) is None


def test_anchor_stale_grace_rides_brief_miss():
    model = _FakeModel([_B(2, 0.7, [100, 100, 140, 200])])
    a = mob_detect.YoloPlayerAnchor(model, stale_grace=2)
    assert a.locate(_frame()) == (120, 200)           # lock
    model._boxes = []                                 # player now missing
    assert a.locate(_frame()) == (120, 200)           # miss 1 -> hold last
    assert a.locate(_frame()) == (120, 200)           # miss 2 -> hold last
    assert a.locate(_frame()) is None                 # grace exhausted -> lost
    model._boxes = [_B(2, 0.7, [100, 100, 140, 200])]
    assert a.locate(_frame()) == (120, 200)           # re-acquire resets grace


def test_anchor_push_avoids_second_inference():
    model = _FakeModel([_B(2, 0.7, [100, 100, 140, 200])])
    a = mob_detect.YoloPlayerAnchor(model)
    a.push((0.9, 200, 50, 20, 60))                    # supply a box from a shared detect
    assert a.locate(_frame()) == (210, 110)           # uses pushed box, not the model
    assert model.calls == []                          # model was NOT queried
    a.locate(_frame())                                # push consumed -> falls back to own predict
    assert len(model.calls) == 1


def test_yolo_boxes_per_class_conf():
    # fishhouse(0) weak @0.35 kept (floor 0.3); goby(1) weak @0.4 dropped (floor 0.5)
    boxes = [_B(0, 0.35, [10, 10, 30, 40]), _B(1, 0.4, [50, 50, 70, 90]),
             _B(0, 0.9, [80, 80, 100, 120])]
    dets = mob_detect.yolo_boxes(_FakeModel(boxes), _frame(), conf=0.5,
                                 class_conf={0: 0.3, 1: 0.5})
    scores = sorted(d[0] for d in dets)
    assert scores == [0.35, 0.9]          # both fishhouse kept, weak goby filtered out


def test_yolo_boxes_with_class_appends_name():
    boxes = [_B(0, 0.9, [10, 10, 30, 40]), _B(1, 0.8, [50, 50, 70, 90])]
    m = _FakeModel(boxes); m.names = {0: "fishhouse", 1: "goby", 2: "player"}
    dets = mob_detect.yolo_boxes(m, _frame(), with_class=True)
    assert len(dets) == 2
    assert dets[0] == (0.9, 10, 10, 20, 30, "fishhouse")   # 6-tuple: (score,x,y,w,h,cls_name)
    assert dets[1][5] == "goby"


def test_yolo_boxes_without_class_stays_5tuple():
    boxes = [_B(0, 0.9, [10, 10, 30, 40])]
    dets = mob_detect.yolo_boxes(_FakeModel(boxes), _frame())   # default with_class=False
    assert len(dets[0]) == 5                                     # unchanged 5-tuple
