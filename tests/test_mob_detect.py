"""player-box selection for the YOLO anchor (multi/false-detection robustness)."""
import mob_detect


def test_pick_player_none_when_empty():
    assert mob_detect._pick_player([]) is None
    assert mob_detect._pick_player([], near=(500, 300)) is None


def test_pick_player_highest_conf_without_prior():
    # No last position -> fall back to the highest-confidence box.
    players = [(0.4, 100, 200, 40, 20), (0.9, 1500, 210, 40, 20)]
    assert mob_detect._pick_player(players)[0] == 0.9


def test_pick_player_nearest_last_ignores_higher_conf_false_box():
    # A false HP-bar box far away scores HIGHER, but the box nearest last (the real player)
    # must win -- this is what stops the anchor snapping hundreds of px between detections.
    real = (0.5, 780, 200, 40, 20)     # center-x 800, near last
    false_hi = (0.95, 1400, 210, 40, 20)   # center-x 1420, far, higher conf
    players = [false_hi, real]
    picked = mob_detect._pick_player(players, near=(810, 500))
    assert picked is real


def test_pick_player_max_jump_rejects_far_leap():
    # Only OTHER players' boxes remain (her real HP bar was missed). Without a cap the anchor
    # would hop to the nearest one and drift; with max_jump it returns None so the caller rides
    # its last position instead of running to the map edge.
    near_box = (0.9, 100, 0, 20, 10)    # center-x 110
    far_box = (0.95, 800, 0, 20, 10)    # center-x 810
    # nearest is within the cap -> accepted
    assert mob_detect._pick_player([near_box, far_box], near=(115, 0), max_jump=250) is near_box
    # only a far box -> leap 695 > 250 -> rejected (no lock)
    assert mob_detect._pick_player([far_box], near=(115, 0), max_jump=250) is None
    # no cap -> legacy behavior, takes the far box
    assert mob_detect._pick_player([far_box], near=(115, 0)) is far_box


class _FB:  # fake ultralytics box: b.cls[0], b.conf[0], b.xyxy[0].tolist()
    def __init__(self, cls, conf, xyxy):
        self.cls = [cls]; self.conf = [conf]
        class _T:
            def __init__(s, v): s._v = v
            def tolist(s): return s._v
        self.xyxy = [_T(xyxy)]


class _FM:  # fake model with class names
    names = {0: "fishhouse", 1: "goby", 2: "player"}


def test_yolo_detect_mobs_equal_yolo_boxes_and_picks_player(monkeypatch):
    # Shared pass must yield mobs IDENTICAL to the separate yolo_boxes call, plus the player.
    boxes = [_FB(0, 0.40, [10, 10, 50, 50]),    # fishhouse, weak but >= class floor 0.3
             _FB(1, 0.70, [100, 100, 140, 150]),  # goby, >= 0.6
             _FB(2, 0.90, [200, 20, 240, 40]),   # player
             _FB(0, 0.20, [300, 10, 340, 50])]   # fishhouse, below floor -> dropped
    monkeypatch.setattr(mob_detect, "_predict", lambda *a, **k: (boxes, 0, 0))
    m = _FM(); cc = {0: 0.3}
    mobs_boxes = mob_detect.yolo_boxes(m, None, conf=0.6, class_conf=cc, with_class=True)
    mobs_det, player = mob_detect.yolo_detect(m, None, conf=0.6, class_conf=cc, with_class=True,
                                              player_conf=0.5, near=(210, 30))
    assert mobs_det == mobs_boxes                                   # identical mob filtering
    assert mobs_det == [(0.4, 10, 10, 40, 40, "fishhouse"),
                        (0.7, 100, 100, 40, 50, "goby")]
    assert player == (0.9, 200, 20, 40, 20)                        # the class-2 box
