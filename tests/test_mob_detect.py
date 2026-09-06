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
