"""Kite engage (archer): she can shoot while walking, so each beat only decides walk or stand --
walk on shooting, stand and shoot when a mob ahead is inside the safe distance."""
import sweep


class _KB:
    def __init__(self):
        self.events = []
    def safe_press(self, k): self.events.append(("press", k))
    def safe_release(self, k): self.events.append(("release", k))
    def safe_release_all(self): self.events.append(("release_all", None))


KEYS = {"attack": "x", "dash": "z", "right": "R", "left": "L"}


def test_kite_no_mob_walks_shooting():
    assert sweep.kite_action([], 500, 1, safe_dist=150) == sweep.WALK_SHOOT


def test_kite_far_mob_ahead_walks_shooting():
    assert sweep.kite_action([800], 500, 1, safe_dist=150) == sweep.WALK_SHOOT


def test_kite_close_mob_ahead_stands():
    assert sweep.kite_action([800, 600], 500, 1, safe_dist=150) == sweep.STAND_SHOOT


def test_kite_close_mob_ahead_heading_left():
    assert sweep.kite_action([420], 500, -1, safe_dist=150) == sweep.STAND_SHOOT
    assert sweep.kite_action([580], 500, -1, safe_dist=150) == sweep.WALK_SHOOT   # behind


def test_kite_mob_overlapping_her_stands():
    assert sweep.kite_action([480], 500, 1, safe_dist=150, behind_tol=30) == sweep.STAND_SHOOT


def test_kite_mob_behind_ignored():
    assert sweep.kite_action([400], 500, 1, safe_dist=150, behind_tol=30) == sweep.WALK_SHOOT


def test_apply_kite_walk_holds_arrow_and_taps_attack():
    kb = _KB()
    sweep.apply_kite(sweep.WALK_SHOOT, 1, KEYS, kb, shot_press=0.05)
    assert kb.events == [("release", "L"), ("press", "R"), ("press", "x"), ("release", "x")]


def test_apply_kite_stand_releases_arrows_then_shoots():
    kb, slept = _KB(), []
    sweep.apply_kite(sweep.STAND_SHOOT, -1, KEYS, kb, sleep=slept.append, shot_press=0.05,
                     stand_secs=0.3)
    assert kb.events[:2] == [("release", "R"), ("release", "L")]
    assert ("press", "R") not in kb.events and ("press", "L") not in kb.events
    shots = [e for e in kb.events if e == ("press", "x")]
    assert len(shots) >= 2                         # keeps tapping through the stand window
    assert kb.events[-1] == ("release", "x")
    assert abs(sum(slept) - 0.3) < 0.06
