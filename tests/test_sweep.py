import sweep

# Fixed frame for readability: attack_range=200 so an in-range mob is within 200px.
AR = 200
# dash = AR - dash_gap = 150; reach = dash + grp = 240 forward.
G = {"grp": 90, "min": 3, "dash_gap": 50}


def test_advance_when_nothing_ahead():
    # empty platform stretch -> move on to the next route segment
    assert sweep.decide([], px=100, heading=1, attack_range=AR) == sweep.ADVANCE


def test_advance_ignores_mob_behind_out_of_range():
    # a mob well behind (opposite heading) must NOT pull her backward -> still ADVANCE
    assert sweep.decide([250], px=500, heading=1, attack_range=AR) == sweep.ADVANCE


def test_fire_when_mob_in_range_no_gather():
    # gather disabled; a mob within attack_range -> just kill it in place
    assert sweep.decide([250], px=100, heading=1, attack_range=AR) == sweep.FIRE


def test_walk_toward_lone_mob_ahead_out_of_range():
    # gather disabled; lone mob ahead but out of range -> approach it (kill-don't-skip)
    assert sweep.decide([400], px=100, heading=1, attack_range=AR) == sweep.WALK


def test_gather_when_enough_spread_mobs_in_scoop_reach():
    # cd up, few in range but 3 within dash+grp reach -> scoop them together
    xs = [300, 320, 330]                 # dists 200,220,230; only 300 is in-range
    assert sweep.decide(xs, px=100, heading=1, attack_range=AR,
                        gather=G, cd_ok=True) == sweep.GATHER


def test_fire_not_gather_when_already_enough_in_range():
    # 3 already within attack_range -> gathering buys nothing, fire now
    xs = [250, 260, 270]                 # all within 200px of px=100
    assert sweep.decide(xs, px=100, heading=1, attack_range=AR,
                        gather=G, cd_ok=True) == sweep.FIRE


def test_no_gather_while_on_cooldown_degrades_to_fire():
    xs = [300, 320, 330]
    assert sweep.decide(xs, px=100, heading=1, attack_range=AR,
                        gather=G, cd_ok=False) == sweep.FIRE


def test_gather_none_never_gathers_even_when_clustered():
    # skill not enabled for this loop -> pure walk/fire sweep, never GATHER
    xs = [300, 320, 330]
    assert sweep.decide(xs, px=100, heading=1, attack_range=AR,
                        gather=None, cd_ok=True) == sweep.FIRE


def test_heading_left_is_symmetric():
    # facing left: a mob to the LEFT within range fires; nothing to the left advances
    assert sweep.decide([300], px=500, heading=-1, attack_range=AR) == sweep.FIRE
    # 800 is to the RIGHT (behind) and out of range -> don't turn back -> ADVANCE
    assert sweep.decide([800], px=500, heading=-1, attack_range=AR) == sweep.ADVANCE


def test_path_scoop_counts_mobs_along_the_dash_path():
    # path-scoop: a mob partway along the dash counts toward the scoop, not just the far ring
    xs = [305, 315, 335]                 # all out of range (>200) but within reach 240
    assert sweep.decide(xs, px=100, heading=1, attack_range=AR,
                        gather=G, cd_ok=True) == sweep.GATHER


class _FakeKB:
    def __init__(self):
        self.events = []
    def safe_press(self, k): self.events.append(("press", k))
    def safe_release(self, k): self.events.append(("release", k))
    def safe_release_all(self): self.events.append(("release_all", None))


KEYS = {"attack": "c", "dash": "z", "right": "R", "left": "L"}


def _presses(kb):
    return [k for e, k in kb.events if e == "press"]


def test_walk_holds_heading_direction():
    kb = _FakeKB()
    h = sweep.apply_action(sweep.WALK, 1, KEYS, kb)
    assert ("press", "R") in kb.events and h == 1


def test_walk_left_presses_left_key():
    kb = _FakeKB()
    sweep.apply_action(sweep.WALK, -1, KEYS, kb)
    assert ("press", "L") in kb.events


def test_fire_bursts_attack_but_never_dashes():
    kb = _FakeKB()
    sweep.apply_action(sweep.FIRE, 1, KEYS, kb)
    p = _presses(kb)
    assert "c" in p and "z" not in p


def test_gather_pushes_with_dash_then_attacks():
    kb = _FakeKB()
    sweep.apply_action(sweep.GATHER, 1, KEYS, kb)
    p = _presses(kb)
    assert "z" in p and "c" in p
    assert p.index("z") < p.index("c")          # push+group first, THEN the AoE


def test_advance_flips_heading_and_fires_nothing():
    kb = _FakeKB()
    h = sweep.apply_action(sweep.ADVANCE, 1, KEYS, kb)
    p = _presses(kb)
    assert h == -1 and "c" not in p and "z" not in p


def test_aoe_max_caps_gather_threshold():
    # gather min=10 but the AoE only hits 6 -> never wait past 6; 6 spread mobs -> GATHER
    g = {"grp": 90, "min": 10, "dash_gap": 50}
    six = [305, 312, 319, 326, 333, 340]     # all out of range, within reach 240
    assert sweep.decide(six, px=100, heading=1, attack_range=AR,
                        gather=g, cd_ok=True, aoe_max=6) == sweep.GATHER
    five = [305, 312, 319, 326, 333]
    assert sweep.decide(five, px=100, heading=1, attack_range=AR,
                        gather=g, cd_ok=True, aoe_max=6) == sweep.WALK


def test_resync_skips_floors_she_already_fell_off():
    # live 2026-10-21: on the ledge right of middle_road2's middle floor (x 292, y 108 = 15 below
    # y 93) the fall check (fall_tol 12) said "fell off", but resync (level_tol 15) picked the other
    # middle-floor segment -> segments 2 <-> 5 ping-ponged for minutes without a key press.
    route = [{"dir": "right", "until_x": 292, "y": 152}, {"dir": "left", "until_x": 89, "y": 93},
             {"dir": "right", "until_x": 98, "y": 93}, {"dir": "right", "until_x": 225, "y": 48},
             {"dir": "left", "until_x": 83, "y": 93}]
    assert sweep.resync_segment(route, 1, 292, 108, level_tol=15) == 4          # old behaviour
    assert sweep.resync_segment(route, 1, 292, 108, level_tol=15, fall_tol=12) is None
    assert sweep.resync_segment(route, 4, 292, 108, level_tol=15, fall_tol=12) is None
    # a read within fall_tol still resyncs normally
    assert sweep.resync_segment(route, 1, 150, 100, level_tol=15, fall_tol=12) == 4


def test_start_segment_index():
    route = [{"y": 1}, {"y": 2}, {"y": 3}]
    assert sweep.start_segment_index(route, None) == 0          # default: segment 1
    assert sweep.start_segment_index(route, 2) == 1             # 1-based in the map config
    assert sweep.start_segment_index(route, 9) == 0             # out of range -> segment 1
    assert sweep.start_segment_index(route, 0) == 0


def _stall_run(reads, walking=True):
    s = sweep.MinimapStall(beats=4, screen_px=60)
    return [s.feed(mx, sx, walking) for mx, sx in reads]


def test_minimap_stall_fires_when_screen_moves_but_minimap_does_not():
    # live 2026-10-29 (segment 5, walking left): minimap stuck at x=88 while her screen x slid
    # 883 -> 455 -- she walked off the middle floor before the drop point (83) was ever "reached"
    reads = [(88, 883), (88, 861), (88, 840), (88, 815), (88, 804), (88, 788), (88, 758)]
    fired = _stall_run(reads)
    assert fired.index(True) <= 5                                    # by ~0.5 s, before the fall


def test_minimap_stall_quiet_in_normal_walking_and_standing():
    walking = [(120 - i, 900 + (i % 2)) for i in range(10)]          # camera centred: minimap moves
    assert not any(_stall_run(walking))
    standing = [(90, 903)] * 10                                        # stopped: neither moves
    assert not any(_stall_run(standing))
    map_edge = [(80 - i, 900 - 25 * i) for i in range(10)]           # camera clamped: both move
    assert not any(_stall_run(map_edge))
    assert not any(_stall_run([(88, 883 - 30 * i) for i in range(8)], walking=False))  # not walking
