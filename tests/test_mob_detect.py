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
