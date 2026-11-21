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
    assert kb.events[2:] == [("press", "x"), ("release", "x")]   # one continuous hold, not taps
    assert slept == [0.3]


def test_apply_kite_stand_hold_length_jitters():
    import random
    rng = random.Random(3)
    lens = []
    for _ in range(50):
        slept = []
        sweep.apply_kite(sweep.STAND_SHOOT, 1, KEYS, _KB(), sleep=slept.append, stand_secs=0.3,
                         jitter=0.25, rng=rng)
        assert len(slept) == 1
        lens.append(slept[0])
    assert len(set(round(x, 4) for x in lens)) > 20
    assert all(0.15 - 1e-9 <= x <= 0.45 + 1e-9 for x in lens)


def test_apply_kite_walk_secs_keeps_tapping_with_arrow_held():
    kb, slept = _KB(), []
    sweep.apply_kite(sweep.WALK_SHOOT, 1, KEYS, kb, sleep=slept.append, shot_press=0.05,
                     shot_gap=0.05, walk_secs=0.25)
    assert kb.events[:2] == [("release", "L"), ("press", "R")]
    assert kb.events[2:] == [("press", "x"), ("release", "x")] * 3     # arrow never released
    assert slept == [0.05, 0.05, 0.05, 0.05, 0.05]


def test_apply_kite_jitter_varies_each_tap_within_bounds():
    import random
    kb, slept = _KB(), []
    sweep.apply_kite(sweep.WALK_SHOOT, 1, KEYS, kb, sleep=slept.append, shot_press=0.035,
                     shot_gap=0.01, walk_secs=3.0, jitter=0.3, rng=random.Random(1))
    presses, gaps = slept[0::2], slept[1::2]
    assert len(set(round(p, 5) for p in presses)) > 10           # not one fixed rhythm
    assert all(0.02 <= p <= 0.035 * 1.6 + 1e-9 for p in presses)  # long enough to register
    assert all(0.005 - 1e-9 <= g <= 0.01 * 1.6 + 0.09 + 1e-9 for g in gaps)
    assert abs(sum(presses) / len(presses) - 0.035) < 0.006       # same pace on average
    assert abs(sum(slept) - 3.0) < 0.15                           # beat length kept


def test_apply_kite_jitter_sometimes_hesitates():
    import random
    kb, slept = _KB(), []
    sweep.apply_kite(sweep.WALK_SHOOT, 1, KEYS, kb, sleep=slept.append, shot_press=0.035,
                     shot_gap=0.01, walk_secs=20.0, jitter=0.3, rng=random.Random(2))
    gaps = slept[1::2]
    long_gaps = [g for g in gaps if g > 0.03]
    assert 0 < len(long_gaps) < len(gaps) * 0.2                   # rare short pauses only


def test_apply_kite_no_jitter_is_exact():
    kb, slept = _KB(), []
    sweep.apply_kite(sweep.WALK_SHOOT, 1, KEYS, kb, sleep=slept.append, shot_press=0.05,
                     shot_gap=0.05, walk_secs=0.25, jitter=0.0)
    assert slept == [0.05] * 5


def test_character_overrides_kite_tap_timing():
    import core
    merged = core.apply_character({"shot_press_secs": 0.05, "shot_gap_secs": 0.05},
                                  {"shot_press_secs": 0.04, "shot_gap_secs": 0.03})
    assert (merged["shot_press_secs"], merged["shot_gap_secs"]) == (0.04, 0.03)
