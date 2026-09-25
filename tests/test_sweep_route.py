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


ROUTE = [{"dir": "right", "until_x": 275, "y": 106}, {"dir": "left", "until_x": 216, "y": 106},
         {"dir": "left", "until_x": 120, "y": 55}, {"dir": "right", "until_x": 195, "y": 55},
         {"dir": "right", "until_x": 307, "y": 62}, {"dir": "left", "until_x": 214, "y": 106},
         {"dir": "left", "until_x": 45, "y": 99}]


def test_resync_failed_jump_bottom_right_goes_to_the_bottom_portal_leg():
    # stuck in seg 5 (index 4) at the bottom-right wall -> seg 6 (left to the portal), normal lap
    assert sweep.resync_segment(ROUTE, 4, 285, 106) == 5


def test_resync_skips_segments_already_past_their_end():
    # on the bottom at x=200: seg 6 (left to 214) is already done -> seg 7 (left to 45)
    assert sweep.resync_segment(ROUTE, 4, 200, 106) == 6


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
