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


def test_aoe_max_caps_gather_threshold():
    # gather min=10 but the AoE only hits 6 -> never wait past 6; 6 spread mobs -> GATHER
    g = {"grp": 90, "min": 10, "dash_gap": 50}
    six = [305, 312, 319, 326, 333, 340]     # all out of range, within reach 240
    assert sweep.decide(six, px=100, heading=1, attack_range=AR,
                        gather=g, cd_ok=True, aoe_max=6) == sweep.GATHER
    five = [305, 312, 319, 326, 333]
    assert sweep.decide(five, px=100, heading=1, attack_range=AR,
                        gather=g, cd_ok=True, aoe_max=6) == sweep.WALK
