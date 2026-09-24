"""Route mode for the sweep loop: a fixed list of segments (dir, until_x on the minimap,
transition at the end, attack or just travel). Heading is fixed per segment -- no ping-pong."""
import sweep


def test_seg_heading():
    assert sweep.seg_heading({"dir": "right"}) == 1
    assert sweep.seg_heading({"dir": "left"}) == -1


def test_seg_done_right_when_minimap_x_reaches_until():
    seg = {"dir": "right", "until_x": 275}
    assert not sweep.seg_done(seg, 250)
    assert sweep.seg_done(seg, 275) and sweep.seg_done(seg, 290)


def test_seg_done_left_when_minimap_x_reaches_until():
    seg = {"dir": "left", "until_x": 214}
    assert not sweep.seg_done(seg, 240)
    assert sweep.seg_done(seg, 214) and sweep.seg_done(seg, 200)


class _KB:
    def __init__(self):
        self.events = []
    def safe_press(self, k): self.events.append(("press", k))
    def safe_release(self, k): self.events.append(("release", k))
    def safe_release_all(self): self.events.append(("release_all", None))


KEYS = {"attack": "x", "dash": "z", "right": "R", "left": "L", "up": "U", "jump": "J"}


def _reader(seq):
    it = iter(seq)
    last = [seq[-1]]
    def read():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return read


def test_transition_none_is_a_noop_success():
    kb = _KB()
    assert sweep.route_transition(None, 1, 1, KEYS, kb, read_xy=_reader([(0, 0)])) is True
    assert not [e for e in kb.events if e[0] == "press"]


def test_transition_up_confirms_on_level_change():
    kb = _KB()
    # baseline on the bottom floor, then the portal lands her on the upper level (y 106 -> 55)
    ok = sweep.route_transition("up", -1, -1, KEYS, kb,
                                read_xy=_reader([(214, 106), (214, 106), (140, 55)]))
    assert ok is True and ("press", "U") in kb.events


def test_transition_up_retries_once_then_reports_failure():
    kb = _KB()
    ok = sweep.route_transition("up", -1, -1, KEYS, kb, read_xy=_reader([(214, 106)]),
                                up_wait=0.3, poll=0.1)
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is False
    assert presses.count("U") == 2            # first try + one retry
    assert "R" in presses or "L" in presses   # nudged before the retry


def test_transition_jump_double_jumps_holding_next_direction():
    kb = _KB()
    ok = sweep.route_transition("jump", 1, 1, KEYS, kb, read_xy=_reader([(195, 55)]))
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is True
    assert presses.count("J") == 2
    assert presses.index("R") < presses.index("J")   # moving toward the next segment first


def test_transition_up_ignores_failed_minimap_reads():
    # (-1,-1) = no read; it must NOT count as a level change (106 -> -1 is not the portal)
    kb = _KB()
    ok = sweep.route_transition("up", -1, -1, KEYS, kb, read_xy=_reader([(214, 106), (-1, -1)]),
                                up_wait=0.3, poll=0.1)
    assert ok is False


# --- same-platform filter: decide() is x-only, so mobs on another LEVEL must be dropped first ---

def test_same_platform_drops_mobs_on_other_levels():
    # (x, feet_y). Player feet at 890 (bottom floor). Upper-floor mobs sit ~630px higher.
    mobs = [(838, 891), (946, 264), (1019, 266), (1250, 891)]
    xs = sweep.same_platform(mobs, pfeet=890, band_up=180, band_down=180)
    assert xs == [838, 1250]


def test_upper_level_mob_in_x_range_no_longer_pins_her_in_fire():
    # the live bug: only upper-floor mobs within 200px in x -> she FIREd forever
    mobs = [(946, 264), (1019, 266)]
    xs = sweep.same_platform(mobs, pfeet=890, band_up=180, band_down=180)
    assert sweep.decide(xs, px=1012, heading=1, attack_range=200) == sweep.ADVANCE


def test_same_platform_asymmetric_band():
    mobs = [(100, 800), (120, 950)]            # 90 above, 60 below her feet (890)
    assert sweep.same_platform(mobs, 890, band_up=50, band_down=100) == [120]


# --- route attack rhythm: one push (z) then N AoE (x); no push near the segment end (edge) ---

def test_near_seg_end_within_margin_in_heading_direction():
    seg = {"dir": "right", "until_x": 275}
    assert sweep.near_seg_end(seg, 255, margin=25)       # 20 px to the edge
    assert not sweep.near_seg_end(seg, 240, margin=25)   # 35 px away
    seg_l = {"dir": "left", "until_x": 214}
    assert sweep.near_seg_end(seg_l, 230, margin=25) and not sweep.near_seg_end(seg_l, 260, 25)


def test_route_attack_pushes_then_aoe_when_mobs_in_reach():
    # mob 150px ahead, reach 240 -> the combo (push + AoE) = GATHER
    assert sweep.route_attack([650], px=500, heading=1, attack_range=200, reach=240,
                              near_edge=False) == sweep.GATHER


def test_route_attack_no_push_near_edge_just_aoe():
    assert sweep.route_attack([650], px=500, heading=1, attack_range=200, reach=240,
                              near_edge=True) == sweep.FIRE


def test_route_attack_walks_when_no_mob():
    assert sweep.route_attack([], px=500, heading=1, attack_range=200, reach=240,
                              near_edge=False) == sweep.WALK


def test_route_attack_walks_to_close_in_on_a_far_mob():
    assert sweep.route_attack([900], px=500, heading=1, attack_range=200, reach=240,
                              near_edge=False) == sweep.WALK


def test_route_attack_hits_a_mob_just_behind_without_turning():
    # 120px behind (inside AoE range) -> attack in place facing the heading; never walk back
    assert sweep.route_attack([380], px=500, heading=1, attack_range=200, reach=240,
                              near_edge=False) == sweep.GATHER


def test_combo_presses_push_once_then_two_aoe():
    kb = _KB()
    sweep.apply_action(sweep.GATHER, 1, {"attack": "x", "dash": "z", "right": "R", "left": "L"},
                       kb, burst=2)
    p = [k for e, k in kb.events if e == "press"]
    assert p.count("z") == 1 and p.count("x") == 2 and p.index("z") < p.index("x")


# --- continuous AoE: hold x for a duration instead of discrete taps ---

def test_attack_hold_is_one_continuous_press_of_the_given_length():
    kb, slept = _KB(), []
    sweep.apply_action(sweep.GATHER, 1, {"attack": "x", "dash": "z", "right": "R", "left": "L"},
                       kb, sleep=slept.append, attack_hold=1.05, pull_settle=0.6)
    p = [k for e, k in kb.events if e == "press"]
    assert p.count("x") == 1 and p.index("z") < p.index("x")
    assert 0.6 in slept and 1.05 in slept              # push settle, then the x hold


# --- portal precision: re-align on the exact portal x before each UP press ---

def test_transition_up_aligns_before_pressing_up():
    kb, calls = _KB(), []
    ok = sweep.route_transition("up", -1, -1, KEYS, kb,
                                read_xy=_reader([(214, 106), (214, 106), (140, 55)]),
                                align=lambda: calls.append("align"))
    assert ok is True and calls == ["align"]


def test_transition_up_realigns_instead_of_blind_nudge_on_retry():
    kb, calls = _KB(), []
    ok = sweep.route_transition("up", -1, -1, KEYS, kb, read_xy=_reader([(214, 106)]),
                                up_wait=0.3, poll=0.1, align=lambda: calls.append("align"))
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is False and calls == ["align", "align"]
    assert "R" not in presses and "L" not in presses   # align replaces the blind nudge
