"""mob_review: which detector boxes are worth saving for review (likely drops / pets / icons)."""
import mob_review

W, H = 1915, 1121


def test_short_box_away_from_edges_is_suspect():
    boxes = [(0.9, 800, 600, 110, 130),          # normal mob
             (0.8, 900, 680, 40, 38)]            # drop-sized, on the floor
    assert mob_review.suspects(boxes, (H, W), small_h=70) == [boxes[1]]


def test_short_boxes_cut_off_at_the_screen_edge_are_real_mobs():
    # the 520 middle_road2 frames: every box under 60 px tall touched an edge (half off-screen mob)
    assert mob_review.suspects([(0.9, 0, 600, 33, 54)], (H, W), small_h=70) == []
    assert mob_review.suspects([(0.9, 1880, 600, 35, 55)], (H, W), small_h=70) == []
    assert mob_review.suspects([(0.9, 700, 41, 90, 50)], (H, W), small_h=70) == []      # top edge


def test_throttle():
    r = mob_review.Reviewer(out_dir=None, every_secs=4, max_frames=2)
    assert r.due(0.0) and not r.due(1.0) and r.due(4.5)
    r.saved = 2
    assert not r.due(100.0)                       # cap reached
