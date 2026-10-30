import pytest
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
    # ...but it lets go of the walk key: WALK holds the arrow until released, so a held LEFT
    # carried her off middle_road2's middle floor after segment 2 ended (live).
    assert ("release_all", None) in kb.events


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


KEYS_D = dict(KEYS, down="D")


def test_transition_down_jump_holds_down_and_jumps_and_waits_for_the_landing():
    # middle_road2: middle floor y 96 -> drop through -> bottom floor y 156
    kb = _KB()
    ok = sweep.route_transition("down_jump", -1, 1, KEYS_D, kb,
                                read_xy=_reader([(78, 96), (78, 98), (78, 130), (78, 156),
                                                 (78, 156), (78, 156)]), poll=0.1)
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is True
    assert presses.index("D") < presses.index("J")
    assert ("release", "D") in kb.events


def test_transition_down_jump_reports_failure_when_she_never_drops():
    kb = _KB()
    ok = sweep.route_transition("down_jump", -1, 1, KEYS_D, kb, read_xy=_reader([(78, 96)]),
                                up_wait=0.3, poll=0.1)
    assert ok is False


def test_transition_climb_jumps_holds_up_until_she_stands_on_the_floor_above():
    # middle_road2 ladder at x~99: y 96 -> climbs -> 51, stays there
    kb = _KB()
    seq = [(99, 96), (99, 90), (99, 80), (99, 70), (99, 60), (99, 51), (99, 51), (99, 51)]
    ok = sweep.route_transition("climb", 1, 1, KEYS, kb, read_xy=_reader(seq), poll=0.1)
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is True
    assert presses.index("J") < presses.index("U")
    assert ("release", "U") in kb.events


def test_transition_climb_retries_after_aligning_when_she_misses_the_ladder():
    kb = _KB()
    aligned = []
    ok = sweep.route_transition("climb", 1, 1, KEYS, kb, read_xy=_reader([(99, 96)]),
                                up_wait=0.3, poll=0.1, align=lambda: aligned.append(1))
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is False
    assert presses.count("J") == 3 and len(aligned) == 3      # climb_tries default 3


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


def test_route_attack_mob_overlapping_her_counts_as_ahead():
    # within behind_tol (her own body width) the forward push/AoE still hits it
    assert sweep.route_attack([480], px=500, heading=1, attack_range=200, reach=240,
                              near_edge=False) == sweep.GATHER


def test_route_attack_mob_only_behind_is_left_alone():
    # push and AoE fire forward; turning back to hit it flips her direction (user: keep one
    # direction), and attacking forward hits air -> walk on
    assert sweep.route_attack([380], px=500, heading=1, attack_range=200, reach=240,
                              near_edge=False) == sweep.WALK


def test_route_attack_mobs_ahead_win_over_mobs_behind():
    assert sweep.route_attack([380, 600], px=500, heading=1, attack_range=200, reach=240,
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


# --- edge_jump: walk off the platform edge, then air-jump across (flat_sweep upper -> right) ---
# The minimap dot PINS at x=195 while the minimap scrolls, so x can't locate the edge; the walk-off
# shows up as the minimap y dropping (55 -> 57+). Jump right then, once, while holding the heading.

def _logged_reader(seq, events):
    read = _reader(seq)
    def r():
        xy = read(); events.append(("read", xy)); return xy
    return r


def test_edge_jump_waits_for_the_slope_bottom_then_jumps():
    kb = _KB()
    read = _logged_reader([(195, 55), (195, 55), (195, 57), (195, 60)], kb.events)
    ok = sweep.route_transition("edge_jump", 1, 1, KEYS, kb, read_xy=read, poll=0.01)
    assert ok is True
    presses = [k for e, k in kb.events if e == "press"]
    # ONE jump at the slope bottom (level + 5), like recorded laps 2-3
    assert presses.count("J") == 1
    j = kb.events.index(("press", "J"))
    assert ("read", (195, 60)) in kb.events[:j]              # not before she reached the bottom
    assert ("read", (195, 57)) in kb.events[:j]
    assert kb.events.index(("press", "R")) < j               # holding the heading the whole way


def test_edge_jump_ignores_phantom_reads():
    # phantom minimap reads (x far from her) must not look like a fall
    kb = _KB()
    read = _logged_reader([(195, 55), (8, 82), (33, 76), (195, 55), (195, 60)], kb.events)
    sweep.route_transition("edge_jump", 1, 1, KEYS, kb, read_xy=read, poll=0.01)
    j = kb.events.index(("press", "J"))
    assert ("read", (195, 60)) in kb.events[:j]


def test_edge_jump_no_walk_off_still_jumps_and_reports_failure():
    kb = _KB()
    ok = sweep.route_transition("edge_jump", 1, 1, KEYS, kb, read_xy=_reader([(195, 55)]),
                                poll=0.1, edge_wait=0.3)
    assert ok is False
    assert [k for e, k in kb.events if e == "press"].count("J") == 1


# --- walk_to_edge in short steps: the pinned zone before the jump is still an attack zone, so the
# loop walks a short step, re-checks mobs, and only jumps once she actually walks off the edge.

def test_walk_to_edge_step_without_fall_releases_and_does_not_jump():
    kb = _KB()
    fell = sweep.walk_to_edge(1, KEYS, kb, read_xy=_reader([(195, 55)]), walk=0.3, poll=0.1)
    assert fell is False
    assert ("press", "J") not in kb.events                  # no edge yet -> never jump
    assert kb.events[-1] == ("release", "R")                # stops so the loop can look for mobs


def test_walk_to_edge_step_with_fall_jumps():
    kb = _KB()
    fell = sweep.walk_to_edge(1, KEYS, kb, read_xy=_reader([(195, 55), (195, 60)]),
                              walk=0.3, poll=0.1)
    assert fell is True and ("press", "J") in kb.events


# --- recovery: a failed jump leaves her on the bottom while the route expects the upper-right
# platform, so she walks into the bottom wall forever. Stuck (walking, no progress) -> resync to the
# next segment on the level she is actually on.

def test_progress_flags_stuck_after_walking_without_gain():
    pr = sweep.Progress(stuck_secs=4.0)
    t = 0.0
    assert pr.update(t, 280, 1) is False
    for _ in range(9):                      # 9 walking beats x 0.5 s, x frozen at the wall
        pr.mark_walking(); t += 0.5
        stuck = pr.update(t, 281, 1)
    assert stuck is True


def test_progress_fighting_in_place_is_not_stuck():
    pr = sweep.Progress(stuck_secs=4.0)
    t = 0.0
    pr.update(t, 150, 1)
    for _ in range(20):                     # AoE beats (not walking): no time accrues
        t += 1.6
        assert pr.update(t, 150, 1) is False


def test_progress_gain_resets_the_clock():
    pr = sweep.Progress(stuck_secs=4.0)
    t = 0.0
    pr.update(t, 100, 1)
    for i in range(20):
        pr.mark_walking(); t += 0.5
        assert pr.update(t, 100 + 3 * (i + 1), 1) is False



def _walk_frozen(pr, t, x, beats, dt=0.25):
    flags = []
    for _ in range(beats):
        pr.mark_walking(); t += dt
        flags.append(pr.update(t, x, 1))
    return t, flags


def test_progress_after_a_rescue_rechecks_quickly():
    """Live 17:57: a hop climbed ONE stair of the low steps left of middle_road2's middle floor
    (y 111 -> 103), then she walked into the next riser for the full 4 s before the next hop. After
    a rescue move the next stuck check uses the short quick window."""
    pr = sweep.Progress(stuck_secs=4.0)
    pr.rescue(quick_secs=1.0)                # the loop just hopped (after progress.reset())
    pr.update(0.0, 57, 1)
    t, flags = _walk_frozen(pr, 0.0, 57, 4)  # 1.0 s against the next stair
    assert flags[-1] is True and not any(flags[:-1])


def test_progress_quick_window_ends_once_she_moves_on():
    pr = sweep.Progress(stuck_secs=4.0)
    pr.rescue(quick_secs=1.0)
    pr.update(0.0, 57, 1)
    pr.mark_walking(); pr.update(0.25, 62, 1)      # real gain: the rescue worked
    t, flags = _walk_frozen(pr, 0.25, 62, 8)       # 2 s frozen: back to the normal 4 s window
    assert not any(flags)


def test_progress_reset_clears_the_quick_window():
    pr = sweep.Progress(stuck_secs=4.0)
    pr.rescue(quick_secs=1.0)
    pr.reset()                                     # e.g. a normal segment advance
    pr.update(0.0, 57, 1)
    t, flags = _walk_frozen(pr, 0.0, 57, 8)
    assert not any(flags)

ROUTE = [{"dir": "right", "until_x": 275, "y": 106}, {"dir": "left", "until_x": 216, "y": 106},
         {"dir": "left", "until_x": 120, "y": 55}, {"dir": "right", "until_x": 195, "y": 55},
         {"dir": "right", "until_x": 307, "y": 62}, {"dir": "left", "until_x": 214, "y": 106},
         {"dir": "left", "until_x": 45, "y": 99}]


def test_resync_failed_jump_bottom_right_goes_to_the_bottom_portal_leg():
    # stuck in seg 5 (index 4) at the bottom-right wall -> seg 6 (left to the portal), normal lap
    assert sweep.resync_segment(ROUTE, 4, 285, 106) == 5


def test_resync_skips_segments_already_past_their_end():
    # on the bottom (y 106) at x=200: seg 6 (left to 214) is already done -> the next bottom-floor
    # segment, seg 1 (right to 275). (Seg 7's floor, y 99 here, is a different floor from 106.)
    assert sweep.resync_segment(ROUTE, 4, 200, 106) == 0


def test_resync_none_when_no_segment_on_this_level():
    assert sweep.resync_segment(ROUTE, 4, 285, 20) is None


def test_minimap_gate_drops_phantom_jumps_then_accepts_after_stale():
    g = sweep.MinimapGate(max_jump=40, stale=2.0)
    assert g.feed(212, 106, 0.0) == (212, 106)
    assert g.feed(32, 76, 0.3) == (-1, -1)          # phantom: 180 px jump in 0.3 s
    assert g.feed(210, 106, 0.6) == (210, 106)
    assert g.feed(135, 55, 3.0) == (135, 55)        # a real teleport is accepted once stale
    g.reset()
    assert g.feed(30, 76, 3.1) == (30, 76)          # after reset (post-transition) take any read


# --- push on cooldown: walk on until it is ready (don't stand waiting, don't AoE without the push)

def test_gather_on_cooldown_walks_instead():
    assert sweep.push_ready_or_walk(sweep.GATHER, cd_left=0.6) == sweep.WALK


def test_gather_ready_pushes():
    assert sweep.push_ready_or_walk(sweep.GATHER, cd_left=0.0) == sweep.GATHER
    assert sweep.push_ready_or_walk(sweep.GATHER, cd_left=-0.2) == sweep.GATHER


def test_fire_and_walk_ignore_the_push_cooldown():
    assert sweep.push_ready_or_walk(sweep.FIRE, cd_left=0.6) == sweep.FIRE
    assert sweep.push_ready_or_walk(sweep.WALK, cd_left=0.6) == sweep.WALK


def test_edge_jump_holds_the_heading_until_landing():
    kb = _KB()
    slept = []
    sweep.walk_to_edge(1, KEYS, kb, read_xy=_reader([(195, 55), (195, 60)]), walk=0.3, poll=0.1,
                       sleep=slept.append)
    last_j = max(i for i, e in enumerate(kb.events) if e == ("press", "J"))
    rel = kb.events.index(("release", "R"))
    assert rel > last_j                                   # heading held through both jumps
    assert sum(slept[-3:]) >= 1.0                         # ~1 s of flight before letting go


def test_attack_without_face_tap_never_touches_the_direction_keys():
    # at the edge lip the 0.03 s facing tap walks her OFF (seen live: fell during a FIRE)
    kb = _KB()
    sweep.apply_action(sweep.FIRE, 1, KEYS, kb, attack_hold=1.05, face_tap=False)
    assert ("press", "R") not in kb.events and ("press", "L") not in kb.events
    assert ("press", "x") in kb.events



# --- "too late" (live): she stopped part-way down the slope (a step ended / an AoE), and the next
# step re-based on HER height, so it only jumped after walking off the lower lip. The trigger is
# now relative to the PLATFORM level, and once the descent starts the step is not cut short.

def test_edge_jump_at_the_slope_bottom_already_jumps_immediately():
    kb = _KB()
    reads = []
    def read():
        reads.append(1); return (195, 61)                  # standing at the slope bottom
    fell = sweep.walk_to_edge(1, KEYS, kb, read_xy=read, walk=0.4, poll=0.03,
                              base=(195, 61), level=55)
    assert fell is True and len(reads) == 1               # first read -> jump, no extra walking


def test_edge_jump_descent_started_is_not_cut_short_by_the_step_timer():
    kb = _KB()
    # the step is only 0.06 s (2 polls) but the descent starts on read 2 -> keep walking to 60
    fell = sweep.walk_to_edge(1, KEYS, kb,
                              read_xy=_reader([(195, 55), (195, 57), (195, 58), (195, 59), (195, 60)]),
                              walk=0.06, poll=0.03, level=55)
    assert fell is True and ("press", "J") in kb.events



# --- portal: the press spot reads differently depending on the side she arrives from (user).
# Recorded presses were all walking LEFT into it; overshooting then walk_to_x-ing back RIGHT made UP
# miss and retry (6-10 s per portal, live). approach_x always finishes moving in the heading.

class _World:
    """Tiny sim: holding a direction key moves minimap x at `speed` px/s while sleep() runs."""
    def __init__(self, x, speed=14.0):
        self.x, self.speed, self.held, self.events = float(x), speed, None, []
    def safe_press(self, k): self.held = k; self.events.append(("press", k))
    def safe_release(self, k):
        if self.held == k: self.held = None
        self.events.append(("release", k))
    def safe_release_all(self): self.held = None
    def sleep(self, dt):
        if self.held in ("R", "L"):
            self.x += self.speed * dt * (1 if self.held == "R" else -1)
            self.min_x = min(getattr(self, "min_x", self.x), self.x)
    def read(self): return (round(self.x), 106)


def _last_dir(events):
    return [k for e, k in events if e == "press" and k in ("R", "L")][-1]


def test_approach_from_past_the_portal_backs_up_then_arrives_moving_left():
    w = _World(209)                                  # overshot the 219 portal walking left
    ok = sweep.approach_x(219, -1, KEYS, w, w.read, w.sleep, tol=2)
    assert ok is True
    assert ("press", "R") in w.events                # backed up past it
    assert _last_dir(w.events) == "L"                # ...and came back in walking LEFT
    assert 217 <= w.x <= 221


def test_approach_from_the_right_side_only_moves_left():
    w = _World(235)
    assert sweep.approach_x(219, -1, KEYS, w, w.read, w.sleep, tol=2) is True
    assert ("press", "R") not in w.events
    assert 217 <= w.x <= 221


def test_approach_from_far_walks_in_one_hold_then_taps():
    # the line-up used to TAP the whole way (0.3 s tap + stop each), ~5 s from 20 px out = the
    # stall at the start of middle_road2's portal/ladder legs. Far away: one continuous walk.
    w = _World(79)
    t = [0.0]
    def sleep(dt):
        t[0] += dt; w.sleep(dt)
    assert sweep.approach_x(99, 1, KEYS, w, w.read, sleep, tol=1) is True
    assert 98 <= w.x <= 100
    assert [k for e, k in w.events if e == "press"].count("R") <= 6     # 1 walk + a few taps
    assert t[0] < 2.2                                 # 20 px at 14 px/s = 1.4 s of walking


# --- push from a walk: pushing from a standstill sometimes glitches (user) -> walk >= pre_walk s
# in the heading first and push while still moving (the recording holds the arrow through z).

def test_push_is_preceded_by_a_short_walk_and_fired_while_moving():
    w = _World(150)
    ev = w.events
    slept = []
    def sleep(dt):
        slept.append(dt); w.sleep(dt)
    sweep.apply_action(sweep.GATHER, 1, KEYS, w, sleep=sleep, attack_hold=1.05, pre_walk=0.2)
    zi = ev.index(("press", "z"))
    assert ("press", "R") in ev[:zi]                    # walking right before the push
    assert ("release", "R") not in ev[ev.index(("press", "R")):zi]   # still moving at the push
    assert 0.2 in slept and w.x > 150                   # actually walked ~0.2 s first


def test_no_pre_walk_by_default_keeps_the_old_sequence():
    kb = _KB()
    sweep.apply_action(sweep.GATHER, 1, KEYS, kb, attack_hold=1.05)
    p = [k for e, k in kb.events if e == "press"]
    assert p.index("z") < p.index("x")


# --- until_y: the last leg drops from the upper floor to the bottom-left start; walking on left to
# until_x after landing wastes time (the next leg heads right). End as soon as she is on that floor.

def test_seg_done_on_reaching_the_target_floor():
    seg = {"dir": "left", "until_x": 45, "until_y": 99}
    assert sweep.seg_done(seg, 80, 55) is False          # still on the upper floor
    assert sweep.seg_done(seg, 80, 99) is True           # landed on the bottom: done, x not needed
    assert sweep.seg_done(seg, 80, 103) is True          # within the floor tolerance


def test_seg_done_until_x_still_ends_it_as_a_fallback():
    seg = {"dir": "left", "until_x": 45, "until_y": 99}
    assert sweep.seg_done(seg, 44, 55) is True


def test_seg_done_without_y_is_unchanged():
    seg = {"dir": "left", "until_x": 45}
    assert sweep.seg_done(seg, 80, 99) is False and sweep.seg_done(seg, 45) is True


# --- finish the fight before moving on: reaching until_x mid-fight moved her to the next segment
# with the mobs she was hitting still alive (user). At a segment end: AoE again while a mob on her
# level is in AoE range (overshooting the point a bit is fine), capped at max_rounds.

def test_finish_keeps_fighting_while_a_mob_is_in_aoe_range():
    assert sweep.finish_or_move([560], px=500, heading=1, attack_range=200,
                                rounds=0, max_rounds=3) == sweep.FIRE


def test_finish_moves_on_when_nothing_is_in_range():
    assert sweep.finish_or_move([900], px=500, heading=1, attack_range=200,
                                rounds=0, max_rounds=3) == sweep.ADVANCE      # beyond AoE range
    assert sweep.finish_or_move([], px=500, heading=1, attack_range=200,
                                rounds=0, max_rounds=3) == sweep.ADVANCE


def test_finish_is_capped():
    assert sweep.finish_or_move([560], px=500, heading=1, attack_range=200,
                                rounds=3, max_rounds=3) == sweep.ADVANCE


def test_finish_ignores_mobs_well_behind_her():
    # forward AoE can't hit them; turning back is not wanted (one direction per segment)
    assert sweep.finish_or_move([300], px=500, heading=1, attack_range=200,
                                rounds=0, max_rounds=3) == sweep.ADVANCE


# --- AoE until clear: after the minimum hold keep x held while a mob is still in range (re-checked
# while holding), instead of releasing and re-deciding every 1.05 s (dead time between holds).

def test_aoe_keeps_holding_while_mobs_remain_then_releases():
    kb, checks = _KB(), iter([True, True, False])
    sweep.apply_action(sweep.FIRE, 1, KEYS, kb, attack_hold=1.05,
                       keep_holding=lambda: next(checks), hold_max=6.0)
    ev = kb.events
    assert [e for e in ev if e == ("press", "x")] == [("press", "x")]      # ONE continuous press
    assert ev.index(("release", "x")) > ev.index(("press", "x"))


def test_aoe_hold_is_capped():
    kb, n = _KB(), [0]
    def always():
        n[0] += 1; return True
    sweep.apply_action(sweep.FIRE, 1, KEYS, kb, attack_hold=1.05, keep_holding=always,
                       hold_max=1.0, hold_poll=0.25)
    assert n[0] <= 4 and ("release", "x") in kb.events


def test_aoe_without_keep_holding_is_the_fixed_hold():
    kb, slept = _KB(), []
    sweep.apply_action(sweep.FIRE, 1, KEYS, kb, sleep=slept.append, attack_hold=1.05)
    assert 1.05 in slept and [k for e, k in kb.events if e == "press"].count("x") == 1


# --- edge_x: the platform edge is not always the segment end. flat_sweep segment 6 walks off the
# upper floor's left edge at minimap x ~83 (recording, 3 laps) but ends on LANDING (until_y), with
# until_x 45 only a fallback -- so the no-push zone must be measured from the edge, not until_x.

def test_near_seg_end_uses_edge_x_when_given():
    seg = {"dir": "left", "until_x": 45, "until_y": 99, "edge_x": 83}
    assert sweep.near_seg_end(seg, 100, margin=18) is True      # 17 px before the real edge
    assert sweep.near_seg_end(seg, 110, margin=18) is False
    assert sweep.near_seg_end({"dir": "left", "until_x": 45}, 100, margin=18) is False


# --- smooth edge walk: one continuous walk to the slope bottom that only stops when a mob comes
# into range (interrupt), instead of 0.4 s steps with a full stop + detection beat between them.

def test_edge_walk_stops_only_when_interrupted():
    kb = _KB()
    calls = iter([False, False, True])
    fell = sweep.walk_to_edge(1, KEYS, kb, read_xy=_reader([(195, 55)]), walk=5.0, poll=0.05,
                              level=55, interrupt=lambda: next(calls), check_every=0.1)
    assert fell is False and ("press", "J") not in kb.events
    assert kb.events[-1] == ("release", "R")               # stopped to fight


def test_edge_walk_never_checks_mobs_once_on_the_slope():
    kb = _KB()
    checked = []
    def interrupt():
        checked.append(1); return False
    # descent starts on the 2nd read: no mob checks may delay the jump after that
    fell = sweep.walk_to_edge(1, KEYS, kb,
                              read_xy=_reader([(195, 55), (195, 57), (195, 58), (195, 59), (195, 60)]),
                              walk=5.0, poll=0.05, level=55, interrupt=interrupt, check_every=0.05)
    assert fell is True and len(checked) <= 1


# --- after the jump: releasing the arrow on landing left her standing ~0.9 s while the loop
# re-anchored and ran a detection beat (user: "stops a bit after jump"). keep_moving leaves the
# heading held so she walks on until the next action takes over.

def test_edge_jump_keep_moving_leaves_the_heading_held():
    kb = _KB()
    fell = sweep.walk_to_edge(1, KEYS, kb, read_xy=_reader([(195, 55), (195, 60)]), walk=0.3,
                              poll=0.1, level=55, keep_moving=True)
    assert fell is True
    j = kb.events.index(("press", "J"))
    assert ("release", "R") not in kb.events[j:]           # still walking right after landing


def test_edge_jump_default_still_releases_after_landing():
    kb = _KB()
    sweep.walk_to_edge(1, KEYS, kb, read_xy=_reader([(195, 55), (195, 60)]), walk=0.3, poll=0.1,
                       level=55)
    assert kb.events[-1] == ("release", "R")


# --- landing check after the edge jump (recorded flights: take off ~y60, rise to ~52, land at the
# right platform's y 62 after 0.70-0.77 s; a miss drops toward the bottom floor y 99-106).

def test_landing_state_in_the_air_early():
    assert sweep.landing_state(0.3, 52, target_y=62) == "air"
    assert sweep.landing_state(0.2, 61, target_y=62) == "air"      # take-off height, too soon


def test_landing_state_landed_on_the_platform():
    assert sweep.landing_state(0.7, 62, target_y=62) == "landed"
    assert sweep.landing_state(0.6, 59, target_y=62) == "landed"


def test_landing_state_fell_below():
    assert sweep.landing_state(0.5, 80, target_y=62) == "fell"


def test_landing_state_gives_up_waiting():
    assert sweep.landing_state(2.5, 55, target_y=62) == "timeout"


def test_segment_without_an_edge_never_blocks_the_push():
    # flat_sweep segment 1 ends at a turnaround in the middle of the bottom floor (it continues to
    # ~315): overshooting with a push is harmless, so no no-push zone before its end.
    seg = {"dir": "right", "until_x": 275, "edge": False}
    assert sweep.near_seg_end(seg, 274, margin=18) is False
    assert sweep.near_seg_end({"dir": "right", "until_x": 275}, 274, margin=18) is True


def test_transition_waits_count_the_time_spent_reading_the_minimap():
    # live read_xy is stable_char(2) (~0.27 s); up_wait 2.5 s used to last ~9 s of real time
    import time
    def slow_read():
        time.sleep(0.05)
        return (99, 96)
    t0 = time.monotonic()
    ok = sweep.route_transition("climb", 1, 1, KEYS, _KB(), read_xy=slow_read, up_wait=0.3, poll=0.01)
    assert ok is False
    assert time.monotonic() - t0 < 1.0            # ~2 x 0.3 s; counting only polls: ~2 x 1.5 s


def test_hop_jumps_while_walking_the_heading():
    # stuck recovery: middle_road2 dropped her on a low step (x 57, y 103) left of the middle
    # floor; walking right just pushed into the step for 100 s. A jump in the heading climbs it.
    kb = _KB()
    sweep.hop(1, KEYS, kb)
    ev = kb.events
    assert ev.index(("press", "R")) < ev.index(("press", "J"))
    assert ("release", "R") in ev[ev.index(("press", "J")):]


def test_climb_with_target_level_retries_after_a_jump_that_misses_the_ladder():
    # live: a jump that didn't grab (y 96 -> 88 -> 96) was reported as a climb
    kb = _KB()
    aligned = []
    seq = [(99, 96), (99, 88), (99, 96), (99, 96),                  # attempt 1: jump, land back
           (99, 96), (99, 80), (99, 65), (99, 51), (99, 51)]        # attempt 2: climbs
    ok = sweep.route_transition("climb", 1, 1, KEYS, kb, read_xy=_reader(seq), poll=0.1,
                                target_y=51, align=lambda: aligned.append(1))
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is True
    assert presses.count("J") == 2 and len(aligned) == 2


def test_climb_with_target_level_fails_when_she_never_reaches_it():
    kb = _KB()
    seq = [(99, 96), (99, 88), (99, 96), (99, 96)]
    ok = sweep.route_transition("climb", 1, 1, KEYS, kb, read_xy=_reader(seq), poll=0.1,
                                up_wait=0.5, target_y=51)
    assert ok is False
    assert [k for e, k in kb.events if e == "press"].count("J") == 3


def test_climb_run_mode_runs_in_and_jumps_at_the_takeoff_holding_the_arrow():
    # ladder_test.py (live): a running jump taken off at x=92 grabbed middle_road2's ladder most
    # reliably. Line up at takeoff - runup, hold the heading, jump when the dot reaches takeoff.
    kb = _KB()
    targets = []
    fast = _reader([(84, 96), (88, 96), (91, 96), (92, 96)])
    seq = [(92, 96), (96, 80), (99, 65), (99, 51), (99, 51)]
    ok = sweep.route_transition("climb", 1, 1, KEYS, kb, read_xy=_reader(seq), poll=0.1,
                                target_y=51, align_to=targets.append, fast_xy=fast,
                                run_takeoff=92, run_up=8, air_hold=0.3)
    ev = kb.events
    assert ok is True and targets == [84]
    assert ev.index(("press", "R")) < ev.index(("press", "J")) < ev.index(("press", "U"))
    assert ev.index(("release", "R")) > ev.index(("press", "J"))     # held through the air


def test_walk_right_after_an_attack_gets_one_second_look_first():
    # middle_road2 (user): she walked off mobs still in front of her -- right after the AoE her
    # skill effects hide them from the detector for a frame. One re-look before walking away.
    assert sweep.recheck_before_walk(sweep.WALK, sweep.GATHER, rechecked=False) is True
    assert sweep.recheck_before_walk(sweep.WALK, sweep.FIRE, rechecked=False) is True
    assert sweep.recheck_before_walk(sweep.WALK, sweep.FIRE, rechecked=True) is False  # 2nd: go
    assert sweep.recheck_before_walk(sweep.WALK, sweep.WALK, rechecked=False) is False
    assert sweep.recheck_before_walk(sweep.FIRE, sweep.FIRE, rechecked=False) is False


# --- resync by the NEAREST floor: level_tol 10 matched the upper floor (y 55) for a read of y 63
# on the right platform (y 62) and ping-ponged segments 4 <-> 6.

def test_resync_prefers_the_nearest_floor():
    route = [{"dir": "right", "until_x": 320, "y": 62}, {"dir": "left", "until_x": 45, "y": 55}]
    assert sweep.resync_segment(route, 1, 240, 63) == 0
    assert sweep.resync_segment(route, 0, 240, 63) == 0


def test_minimap_dot_filter_ignores_darker_map_art():
    """flat_sweep's minimap has a yellow recycling-bin graphic (V ~165) the same hue as her dot
    (V ~248); it passed as a second 'dot' and the tracker latched onto it."""
    import numpy as np
    import core
    mm = np.full((150, 400, 3), 40, np.uint8)
    mm[60:67, 205:212] = (12, 149, 165)          # bin graphic
    mm[103:110, 196:203] = (50, 246, 248)        # her dot
    blobs = core._char_blobs_from_mm(mm)
    assert [(x, y) for x, y, _a in blobs] == [(199, 106)]


def test_climb_with_fast_reads_needs_three_steady_reads():
    # transitions now poll fast minimap reads (~0.1 s apart): 2 equal reads can be a jump apex,
    # so the loop asks for 3. A pause at 60 on the way up must not count, 51 x3 does.
    kb = _KB()
    seq = [(99, 96), (99, 80), (99, 60), (99, 60), (99, 55), (99, 51), (99, 51), (99, 51)]
    n = [0]
    rd = _reader(seq)
    def read():
        n[0] += 1
        return rd()
    ok = sweep.route_transition("climb", 1, 1, KEYS, kb, read_xy=read, poll=0.05, target_y=51,
                                steady_reads=3)
    assert ok is True and n[0] >= len(seq)       # did not stop before three 51s


def test_climb_is_not_done_while_she_still_hangs_just_below_the_top():
    # live: 'climb ok' but she hung on the rope. The rope reads 52-57 under the top floor (51) and
    # the dot sits ~0.25 s per pixel, so quick equal reads at 55 looked like 'arrived + steady'.
    kb = _KB()
    seq = ([(99, 96), (99, 80), (99, 65)] + [(99, 55)] * 4 + [(99, 53)] * 4
           + [(99, 51)] * 12)
    ys = []
    rd = _reader(seq)
    def read():
        p = rd(); ys.append(p[1]); return p
    ok = sweep.route_transition("climb", 1, 1, KEYS, kb, read_xy=read, poll=0.05, target_y=51,
                                steady_reads=3, steady_secs=0.4, climb_land_tol=1)
    assert ok is True
    assert ys[-1] == 51 and ys.count(51) >= 8          # waited ~0.4 s ON the floor, not at 55/53


def test_push_prewalk_waits_out_the_attack_recovery():
    # the beat got ~5x faster (3a2080a): a push now comes ~70 ms after the AoE ends, while the
    # attack animation still eats movement, so the 0.1 s pre-walk did nothing (user). The walk is
    # stretched so >= pre_walk of it falls after `recover` s since the last attack ended.
    assert sweep.prewalk_secs(0.1, since_attack=0.05, recover=0.3) == pytest.approx(0.35)
    assert sweep.prewalk_secs(0.1, since_attack=1.0, recover=0.3) == pytest.approx(0.1)
    assert sweep.prewalk_secs(0.1, since_attack=None, recover=0.3) == pytest.approx(0.1)


def test_portal_press_retries_up_to_up_tries():
    kb = _KB()
    ok = sweep.route_transition("up", -1, -1, KEYS, kb, read_xy=_reader([(214, 106)]),
                                up_wait=0.3, poll=0.1, up_tries=3)
    assert ok is False
    assert [k for e, k in kb.events if e == "press"].count("U") == 3


# --- segment-end finishing: walk on to mobs past the portal (middle_road2 segment 1: the portal
# is at 296 but the floor runs to 311; the mobs left at the end sat 280-450 px ahead, beyond the
# 200 px AoE, so finishing went straight into the portal and skipped them).

def test_finish_walks_toward_mobs_ahead_beyond_aoe_when_the_floor_continues():
    act = sweep.finish_or_move([505 + 300], 505, 1, 200, rounds=0, max_rounds=3,
                               reach=450, can_walk=True)
    assert act == sweep.WALK


def test_finish_does_not_walk_past_the_floor_end():
    act = sweep.finish_or_move([505 + 300], 505, 1, 200, rounds=0, max_rounds=3,
                               reach=450, can_walk=False)
    assert act == sweep.ADVANCE


def test_finish_ignores_mobs_beyond_reach_and_still_fires_in_range():
    assert sweep.finish_or_move([505 + 600], 505, 1, 200, 0, 3, reach=450,
                                can_walk=True) == sweep.ADVANCE
    assert sweep.finish_or_move([505 + 150, 505 + 300], 505, 1, 200, 0, 3, reach=450,
                                can_walk=True) == sweep.FIRE


def test_push_key_is_held_for_dash_press():
    kb, slept = _KB(), []
    sweep.apply_action(sweep.GATHER, 1, KEYS, kb, sleep=slept.append, attack_hold=1.05,
                       pre_walk=0.1, dash_press=0.08)
    ev = kb.events
    zi = ev.index(("press", "z"))
    assert ev[zi + 1] == ("release", "z") and 0.08 in slept


def _timed_kb():
    kb, t = _KB(), [0.0]
    def sleep(d): t[0] += d
    def at(ev):                                   # time of the first matching event
        return kb.times[kb.events.index(ev)]
    kb.times = []
    orig_p, orig_r = kb.safe_press, kb.safe_release
    kb.safe_press = lambda k: (orig_p(k), kb.times.append(t[0]))
    kb.safe_release = lambda k: (orig_r(k), kb.times.append(t[0]))
    kb.safe_release_all = lambda: (kb.events.append(("release_all", None)), kb.times.append(t[0]))
    return kb, sleep, at


def test_push_waits_until_the_minimap_shows_her_walking():
    # user: the walk before the push was still swallowed (the attack animation runs longer than a
    # fixed guess). Walk until she has actually moved, then push; capped by pre_walk_max.
    kb, sleep, at = _timed_kb()
    moves = iter([False, False, False, True])
    sweep.apply_action(sweep.GATHER, 1, KEYS, kb, sleep=sleep, attack_hold=1.05,
                       pre_walk=0.1, moved=lambda: next(moves, True), pre_walk_max=0.8)
    walk = at(("press", "z")) - at(("press", "R"))
    assert walk == pytest.approx(0.1 + 3 * 0.03)              # min walk + 3 polls until moved


def test_push_prewalk_gives_up_waiting_at_pre_walk_max():
    kb, sleep, at = _timed_kb()
    sweep.apply_action(sweep.GATHER, 1, KEYS, kb, sleep=sleep, attack_hold=1.05,
                       pre_walk=0.1, moved=lambda: False, pre_walk_max=0.4)
    walk = at(("press", "z")) - at(("press", "R"))
    assert 0.4 - 1e-9 <= walk <= 0.4 + 0.03


def test_down_jump_retries_quickly_when_the_first_press_is_swallowed():
    # live: segment 4's drop took 4.2-4.4 s -- the first down+jump (right after the AoE) was
    # swallowed and the retry waited 2.5 s. drop_tries / a short up_wait retry sooner.
    kb = _KB()
    seq = [(232, 51)] * 12 + [(232, 60), (232, 80), (232, 96)] + [(232, 96)] * 12
    ok = sweep.route_transition("down_jump", 1, -1, dict(KEYS, down="D"), kb, read_xy=_reader(seq),
                                poll=0.1, up_wait=0.8, drop_tries=3, target_y=96)
    assert ok is True
    assert [k for e, k in kb.events if e == "press"].count("J") == 2


def test_drop_from_past_the_drop_point_walks_back_first():
    # live 14:23: finishing walked her to x=74 on middle_road2's middle floor; dropping there landed
    # on the low step (51, 111) instead of the bottom floor. Every good drop started at 84-88.
    seg = {"dir": "left", "until_x": 88, "then": "down_jump", "y": 96}
    assert sweep.drop_overshoot(seg, 74, tol=3) is True
    assert sweep.drop_overshoot(seg, 86, tol=3) is False          # at / near the drop point
    assert sweep.drop_overshoot(seg, 95, tol=3) is False          # not there yet
    assert sweep.drop_overshoot(dict(seg, then="up"), 74, tol=3) is False
    assert sweep.drop_overshoot(seg, -1, tol=3) is False          # no read


# --- stuck rescue that knows up from down (live 14:23: knocked off middle_road2's middle floor
# onto the low step (42-51, 111); segment 1 is the bottom floor (156) BELOW her, but the rescue
# always hopped -- up onto the middle floor, where she ran the bottom route).

def test_rescue_drops_first_when_the_segment_floor_is_below_her():
    assert sweep.rescue_move(seg_y=156, mmy=111, attempt=0) == "down_jump"
    assert sweep.rescue_move(seg_y=156, mmy=111, attempt=1) == "walk_back"
    assert sweep.rescue_move(seg_y=156, mmy=111, attempt=2) == "hop"
    assert sweep.rescue_move(seg_y=156, mmy=111, attempt=3) == "down_jump"      # cycles


def test_rescue_hops_when_the_segment_floor_is_level_or_above():
    assert sweep.rescue_move(seg_y=96, mmy=103, attempt=0) == "hop"
    assert sweep.rescue_move(seg_y=51, mmy=96, attempt=1) == "hop"
    assert sweep.rescue_move(seg_y=None, mmy=111, attempt=0) == "hop"


def test_after_a_failed_transition_check_the_floor():
    # she is not on the next segment's floor -> resync instead of blindly advancing
    assert sweep.off_expected_floor({"y": 156}, 111) is True
    assert sweep.off_expected_floor({"y": 156}, 153) is False
    assert sweep.off_expected_floor({"y": 156}, -1) is False       # no read: can't tell
    assert sweep.off_expected_floor({}, 111) is False


def test_down_jump_counts_any_lower_landing():
    # live: the bottom floor reads 148 on the raised block at its left end (and 143 on the car),
    # not 156 -- a drop landing there was "not confirmed" and retried
    kb = _KB()
    seq = [(80, 96), (80, 110), (70, 130), (60, 148)] + [(60, 148)] * 12
    ok = sweep.route_transition("down_jump", -1, 1, dict(KEYS, down="D"), kb, read_xy=_reader(seq),
                                poll=0.1, target_y=156, steady_secs=0.4)
    assert ok is True
    assert [k for e, k in kb.events if e == "press"].count("J") == 1


def test_walk_back_to_is_one_capped_walk():
    w = _World(74, speed=28.0)
    t = [0.0]
    def sleep(d): t[0] += d; w.sleep(d)
    assert sweep.walk_back_to(84, 1, KEYS, w, w.read, sleep, tol=1, timeout=2.0) is True
    assert 83 <= w.x <= 87 and t[0] < 1.0
    assert [k for e, k in w.events if e == "press"] == ["R"]          # one walk, no taps


def test_fell_below_the_segment_floor():
    # live: walked off middle_road2's middle floor (96) onto the start platform (148) mid-segment 5
    seg = {"dir": "left", "until_x": 84, "then": "down_jump", "y": 96}
    assert sweep.fell_below(seg, 148, tol=15) is True
    assert sweep.fell_below(seg, 104, tol=15) is False        # a slope / a small step
    assert sweep.fell_below(seg, 51, tol=15) is False         # ABOVE is not a fall
    assert sweep.fell_below(seg, -1, tol=15) is False
    assert sweep.fell_below({"dir": "left", "until_x": 84}, 148, tol=15) is False


def test_rescue_walks_the_other_way_after_two_failed_hops():
    # live 2026-10-21: on the ledge right of middle_road2's middle floor (y 104-108, floor 93) the hop
    # moved her along the ledge but never up -> third try walks back off its open side instead.
    # Stairs (left low steps) still get their two hops first.
    assert [sweep.rescue_move(seg_y=93, mmy=104, attempt=a) for a in range(4)] == \
        ["hop", "hop", "walk_back", "hop"]


def test_stuck_resync_to_a_twin_segment_does_not_help():
    route = [{"dir": "right", "until_x": 292, "y": 152}, {"dir": "left", "until_x": 89, "y": 93},
             {"dir": "right", "until_x": 98, "y": 93}, {"dir": "right", "until_x": 225, "y": 48},
             {"dir": "left", "until_x": 83, "y": 93}]
    assert sweep.resync_helps(route, 4, 1) is False     # seg 5 -> seg 2: same floor, same heading
    assert sweep.resync_helps(route, 1, 4) is False
    assert sweep.resync_helps(route, 1, 2) is True      # same floor but turns around
    assert sweep.resync_helps(route, 1, 0) is True      # another floor
    assert sweep.resync_helps(route, 1, None) is False


def test_resync_past_a_segments_end_takes_its_transition():
    # after walking off the right-end ledge she lands on the bottom floor at x ~300, past segment
    # 1's end (292) -> continue from segment 1: its end fires at once and the portal UP runs,
    # instead of "no segment" + rescue moves on the bottom floor.
    route = [{"dir": "right", "until_x": 292, "y": 152, "then": "up"},
             {"dir": "left", "until_x": 89, "y": 93},
             {"dir": "right", "until_x": 98, "y": 93, "then": "climb"},
             {"dir": "right", "until_x": 225, "y": 48, "then": "down_jump"},
             {"dir": "left", "until_x": 83, "y": 93, "then": "down_jump"}]
    for cur in (1, 4):
        assert sweep.resync_segment(route, cur, 300, 152, level_tol=15, fall_tol=12) == 0
    # a finished segment with NO transition is still skipped
    flat = [dict(route[0], then=None)] + route[1:]
    assert sweep.resync_segment(flat, 1, 300, 152, level_tol=15, fall_tol=12) is None


def test_back_up_is_capped_when_reads_go_blind():
    # live 2026-10-28: ladder run-up start (83) was behind her (segment 2 ended at 87-89 walking
    # left); the back-up held LEFT until a read said she was 6 px back -- with no usable reads it
    # walked up to the 4 s timeout (~56 px) off the middle floor's left end onto the stairs.
    w = _World(87)
    reads = [0]
    def read():
        reads[0] += 1
        return (round(w.x), 93) if reads[0] <= 1 else (-1, -1)      # blind after the first read
    sweep.approach_x(83, 1, KEYS, w, read, w.sleep, tol=1, back_max=1.0)
    assert w.x >= 87 - 14 * 1.0 - 1                  # walked back at most ~1 s


def test_back_up_never_passes_the_floor_edge_limit():
    w = _World(87)
    sweep.approach_x(83, 1, KEYS, w, w.read, w.sleep, tol=1, back=12, back_limit=80)
    assert min_x_seen(w) >= 79                       # stopped backing at the limit (80), not 71
    assert 82 <= round(w.x) <= 84 and _last_dir(w.events) == "R"


def min_x_seen(w):
    return w.min_x


def test_rescue_keeps_hopping_while_hops_climb_stairs():
    # live 2026-10-29: on the left stairs under the middle floor (y 108 -> 100 -> 93) the rescue
    # went hop, hop (climbed to y 100), walk_back (back down) -- forever. A rescue that finds her
    # HIGHER than the last one restarts the count, so a stair climb keeps hopping.
    n, prev, moves = 0, None, []
    for mmy in (108, 108, 100):                          # the three rescues of each live cycle
        n = sweep.rescue_count(prev, mmy, n)
        moves.append(sweep.rescue_move(93, mmy, n))
        n += 1; prev = mmy
    assert moves == ["hop", "hop", "hop"]


def test_rescue_still_walks_back_when_hops_do_not_lift_her():
    # the right-end ledge (y 104-108 under the 93 floor): hops only move her along it
    n, prev, moves = 0, None, []
    for mmy in (108, 104, 106):
        n = sweep.rescue_count(prev, mmy, n)
        moves.append(sweep.rescue_move(93, mmy, n))
        n += 1; prev = mmy
    assert moves == ["hop", "hop", "walk_back"]
