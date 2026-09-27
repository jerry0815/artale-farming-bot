"""Directional-sweep brain for looping a general platform and killing mobs on the route.

Pure module (stdlib only): decide() maps the current mob positions to one action. The
farming loop owns the actuators (walk/fire/dash keys), cooldown timers, and route
segments -- decide() only chooses, so it is testable with no live game (like navmap.plan).

Model: the platform is a 1D line in screen x. The player has a HEADING (+1 right / -1 left)
that only changes at a route waypoint, so she never reverses mid-segment. Decisions read
only mobs near/ahead within the heading -- far reads are stale (mobs move, partial view).

The optional push/gather skill DASHES forward and scoops mobs along the whole path into a
clump within attack range (path-scoop), so one AoE then hits them. It doubles as forward
travel, so it is the preferred locomotion when enabled and worthwhile; when it is not
configured for a loop (gather=None) decide() degrades to a plain walk-and-kill sweep.
"""
import time as _time


# Actions (strings, like navmap's STAND_SHOOT/WALK_SHOOT/ROTATE).
ADVANCE = "ADVANCE"   # nothing ahead -> move to the next route segment
FIRE = "FIRE"         # a mob is within attack_range -> AoE in place
WALK = "WALK"         # a mob is ahead but out of range -> close on it (heading)
GATHER = "GATHER"     # dash-scoop enough spread mobs into a clump, then AoE


def same_platform(mobs, pfeet, band_up=180, band_down=180):
    """decide() is x-only, so drop mobs on another LEVEL first. mobs: [(center_x, feet_y)];
    pfeet: the player's feet y (screen). Keeps a mob whose feet sit within band_up ABOVE or
    band_down BELOW her feet; returns their center-x list. Without this a mob on the floor above
    (in x-range but unreachable) pins her in FIRE forever."""
    return [x for x, fy in mobs if -band_up <= fy - pfeet <= band_down]


def dash_reach(attack_range, gather):
    """Forward distance the gather skill scoops: dash + group_range. dash is given
    explicitly, else derived as attack_range - dash_gap (so tuning attack_range keeps
    the gap). group_range defaults to attack_range (grabs anything within a dash)."""
    dash = gather.get("dash")
    if dash is None:
        dash = attack_range - gather.get("dash_gap", 50)
    return dash + gather.get("grp", attack_range)


def decide(mob_xs, px, heading, attack_range, gather=None, cd_ok=False, aoe_max=6):
    """Choose the next sweep action from current mob center-x positions.

    mob_xs: iterable of mob center-x (screen px). px: player center-x. heading: +1/-1.
    gather: None (skill off) or {"grp","min","dash"|"dash_gap"}. cd_ok: gather off cooldown.
    aoe_max: how many targets one AoE hits -- caps how many mobs gather waits for.

    Forward-only: a mob counts only if it is ahead (in the heading) or within attack_range
    behind (she can hit just-passed mobs without walking back). Kill-don't-skip: any mob
    ahead is engaged (FIRE in range, else WALK in); only genuinely empty ground -> ADVANCE.
    """
    fwd = [x for x in mob_xs if (x - px) * heading >= -attack_range]
    if not fwd:
        return ADVANCE
    n_here = sum(1 for x in fwd if abs(x - px) <= attack_range)
    if gather is not None and cd_ok:
        reach = dash_reach(attack_range, gather)
        n_span = sum(1 for x in fwd if 0 <= (x - px) * heading <= reach)
        gather_min = min(gather.get("min", 3), aoe_max)
        # Enough mobs sit within the scoop reach, but not enough are ALREADY in AoE range
        # -> dashing collapses the spread into one cast. If enough are already in range,
        # gathering buys nothing (fall through to FIRE).
        if n_span >= gather_min and n_here < gather_min:
            return GATHER
    if n_here >= 1:
        return FIRE
    return WALK


def apply_action(action, heading, keys, kb, sleep=lambda s: None,
                 burst=3, attack_dwell=0.2, face_gap=0.03, pull_settle=0.1, attack_hold=None,
                 face_tap=True, pre_walk=0.0, keep_holding=None, hold_max=6.0, hold_poll=0.1,
                 dash_press=None, moved=None, pre_walk_max=0.8, move_poll=0.03):
    """Translate a decide() action into key presses via the injected `kb` (dependency-injected
    so this stays pure/testable -- no core import). Returns the heading for the NEXT beat
    (flipped on ADVANCE so a route with no explicit segments ping-pongs the platform).

    keys: {"attack","dash","right","left"} -- the attack key (water loop's attack_key), the
    push/gather key ('z'), and the two facing keys (core.Key.right/left live). The loop owns
    the gather cooldown: it passes cd_ok into decide(), and resets its timer after a GATHER.
    face_tap=False skips the facing tap (she already faces the heading): at a platform edge that
    0.03 s tap is a step that can walk her off.
    pre_walk (s): GATHER walks in the heading this long first and pushes WHILE still moving (the
    arrow stays held through the push) -- a push from a standstill sometimes glitches (user).
    keep_holding: optional callable polled after the attack_hold minimum; while it returns True
    (a mob still in range -- the loop re-detects on a fresh frame) x stays held, up to hold_max s
    more. Releasing and re-deciding every hold left ~0.4 s dead gaps mid-fight (user).
    dash_press: how long the push key is held (default face_gap, 0.03 s -- too short a tap, user).
    moved: optional callable, True once the minimap shows her moving since the walk began; the
    pre-walk then lasts until moved() (>= pre_walk, <= pre_walk_max).
    """
    face = keys["right"] if heading > 0 else keys["left"]
    if action == ADVANCE:
        kb.safe_release_all()
        return -heading
    if action == WALK:                                   # continuous travel toward heading
        kb.safe_release(keys["left"] if heading > 0 else keys["right"])
        kb.safe_press(face)
        return heading
    if action == GATHER and pre_walk > 0:                # push from a walk, not a standstill
        kb.safe_release(keys["left"] if heading > 0 else keys["right"])
        kb.safe_press(face); sleep(pre_walk)
        if moved is not None:                            # ...and until she is REALLY walking: an
            waited = pre_walk                            # attack animation eats the walk for an
            while waited < pre_walk_max and not moved():  # unknown time after the AoE (user)
                sleep(move_poll); waited += move_poll
        kb.safe_press(keys["dash"]); sleep(dash_press or face_gap); kb.safe_release(keys["dash"])
        kb.safe_release(face)
        sleep(pull_settle)                               # let the pull group them before the AoE
    else:
        # FIRE or GATHER: stop walking, face the heading, act in place.
        kb.safe_release(keys["right"]); kb.safe_release(keys["left"])
        if face_tap:
            kb.safe_press(face); sleep(face_gap); kb.safe_release(face)
        if action == GATHER:
            kb.safe_press(keys["dash"]); sleep(dash_press or face_gap); kb.safe_release(keys["dash"])
            sleep(pull_settle)                           # let the pull group them before the AoE
    if attack_hold:                                      # one continuous AoE hold (casts repeat)
        kb.safe_press(keys["attack"]); sleep(attack_hold)
        if keep_holding is not None:                     # ...until nothing is left in range
            extra = 0.0
            while extra < hold_max and keep_holding():
                sleep(hold_poll); extra += hold_poll
        kb.safe_release(keys["attack"])
        return heading
    for _ in range(burst):
        kb.safe_press(keys["attack"]); sleep(attack_dwell); kb.safe_release(keys["attack"])
    return heading


# --- Route mode --------------------------------------------------------------------------
# A route is an ordered, cyclic list of segments recorded from a human run
# (build_sweep_route.py): {"dir": "right"|"left", "until_x": <minimap x>,
#   "then": "up"|"jump"|"edge_jump"|"climb"|"down_jump"|null, "attack": bool,
#   optional "until_y": end on reaching that floor}. Heading is FIXED for the whole segment; the segment ends when the minimap x
# reaches until_x, then its transition runs and the next segment starts.

def seg_heading(seg):
    return 1 if seg["dir"] == "right" else -1


def seg_done(seg, mmx, mmy=None, y_tol=6):
    """True once the minimap x has reached the segment's until_x in its direction -- or, if the
    segment has "until_y", as soon as she is on that floor (|mmy - until_y| <= y_tol). The last leg
    drops from the upper floor to the start floor; walking on to until_x after landing just wastes
    time because the next leg heads back the other way."""
    if seg.get("until_y") is not None and mmy is not None and mmy >= 0             and abs(mmy - seg["until_y"]) <= y_tol:
        return True
    return mmx >= seg["until_x"] if seg_heading(seg) > 0 else mmx <= seg["until_x"]


def near_seg_end(seg, mmx, margin=25):
    """True when she is within `margin` minimap px of the segment's end (until_x) in its heading.
    Route segment ends are where the platform edge / portal / jump spot is, so a forward push
    there would dash her past it -- the loop skips the push near here. A segment may carry "edge_x"
    when its platform edge is not its end point (e.g. it ends on landing, until_y), or "edge": false
    when its end is just a turnaround mid-floor (no no-push zone)."""
    if seg.get("edge") is False:                  # a plain turnaround: overshooting is harmless
        return False
    end = seg.get("edge_x", seg["until_x"])      # the platform edge, when it isn't the end point
    return (end - mmx) * seg_heading(seg) <= margin


def route_attack(mob_xs, px, heading, attack_range, reach, near_edge, behind_tol=30):
    """Route attack-leg rhythm: one push (dash-scoop) then AoE -- or just AoE near the edge.

    Returns GATHER (push + AoE), FIRE (AoE only: near_edge), or WALK (no mob ahead within push
    reach -> walk on / close in). Push and AoE both fire FORWARD and she keeps one direction per
    segment (user), so mobs behind her (beyond behind_tol px, her body width) are left alone:
    attacking forward for them hit air (live, 44/229 attacks). attack_range is kept for callers.
    mob_xs must already be same-platform filtered."""
    fwd = [(x - px) * heading for x in mob_xs]
    if any(-behind_tol <= d <= reach for d in fwd):
        return FIRE if near_edge else GATHER
    return WALK


def approach_x(target, heading, keys, kb, read_xy, sleep=lambda s: None, tol=2, back=6,
               tap=0.045, poll=0.03, timeout=4.0, brake=8):
    """Stand on minimap x `target` ARRIVING MOVING IN `heading` (the portal-press side). The press
    spot reads differently depending on the side she comes from; overshooting and correcting from
    the other side made UP miss (live). If she is past the target, walk back until `back` px before
    it, then approach in the heading: one continuous walk while more than `brake` px out (tapping
    the whole way stalled ~5 s per portal/ladder leg), short taps for the last few px. True when
    within tol."""
    fk = keys["right"] if heading > 0 else keys["left"]
    bk = keys["left"] if heading > 0 else keys["right"]

    def ahead():                                   # px still to go in the heading (<0: passed)
        x = read_xy()[0]
        return None if x is None or x < 0 else (target - x) * heading

    waited = 0.0
    for _ in range(3):
        d = ahead()
        if d is not None and d < -tol:             # passed it: back up past the approach point
            kb.safe_press(bk)
            while waited < timeout:
                d = ahead()
                if d is not None and d >= back:
                    break
                sleep(poll); waited += poll
            kb.safe_release(bk)
        for _ in range(40):
            d = ahead()
            if d is None:
                sleep(poll); continue
            if -tol <= d <= tol:
                return True
            if d < -tol:
                break                              # overshot on a tap: back up again
            if d > brake:                          # far: ONE continuous walk to the brake point
                kb.safe_press(fk)
                while waited < timeout:
                    d = ahead()
                    if d is not None and d <= brake:
                        break
                    sleep(poll); waited += poll
                kb.safe_release(fk)
                sleep(0.05)
                continue
            kb.safe_press(fk); sleep(min(0.3, tap + 0.02 * d)); kb.safe_release(fk)
            sleep(0.05)
    return False


def finish_or_move(mob_xs, px, heading, attack_range, rounds, max_rounds, behind_tol=30,
                   reach=None, can_walk=False):
    """At a segment end: FIRE (AoE, no push -- the end is an edge / portal / turn) while a mob on
    her level is in AoE range ahead (or overlapping her), else ADVANCE to the transition. Reaching
    until_x mid-fight used to move her on with the mobs she was hitting still alive; overshooting
    the point a bit is fine. Capped at max_rounds so a respawn / false box can't pin her there.
    can_walk + reach: a mob ahead beyond AoE range but within `reach` -> WALK toward it (the
    floor goes on past the end point, e.g. middle_road2's portal at 296 on a floor to 311: the mobs
    left there sat 280-450 px ahead and were skipped). The caller clears can_walk at the floor end.
    mob_xs must already be same-platform filtered."""
    if rounds >= max_rounds:
        return ADVANCE
    if any(-behind_tol <= (x - px) * heading <= attack_range for x in mob_xs):
        return FIRE
    if can_walk and reach and any(attack_range < (x - px) * heading <= reach for x in mob_xs):
        return WALK
    return ADVANCE


def push_ready_or_walk(act, cd_left):
    """A GATHER (push) decided while the push is still on cooldown (cd_left s > 0) becomes WALK:
    keep closing in until it is ready instead of standing idle or AoE-ing without the push. The
    next beat re-decides (near an edge that becomes FIRE, so the walk can't carry her off)."""
    return WALK if act == GATHER and cd_left > 0 else act


def _jump(keys, kb, sleep, land=1.0):
    """One jump, then fly `land` s (the caller holds the heading through the landing)."""
    kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
    sleep(land)


def walk_to_edge(heading, keys, kb, read_xy, sleep=lambda s: None, walk=0.4, poll=0.03,
                 jump_dy=5, commit_dy=2, commit_max=1.0, x_win=12, land=1.0, base=None,
                 level=None, interrupt=None, check_every=0.25, keep_moving=False):
    """Walk `heading` for up to `walk` s toward the edge_jump gap and jump at the SLOPE BOTTOM.

    The upper platform slopes down (minimap y 55 -> ~62) to the gap; recorded laps jump when the
    minimap reaches the bottom. Trigger: y >= level + jump_dy, where `level` is the PLATFORM's y
    (route segment "y") -- not wherever she happens to stand: re-basing on her own height after she
    stopped part-way down made the jump fire only after she had walked off the lower lip (too late,
    live). Already at/below the trigger on the first read -> jump at once. Once the descent starts
    (y >= level + commit_dy) the step is not cut short by `walk` (up to commit_max more s).
    No trigger: release, return False WITHOUT jumping so the loop can check for mobs. Reads more
    than x_win px from base x are phantoms and ignored. level=None -> first good read's y.
    read_xy must be FAST (single read).
    interrupt: optional callable checked every check_every s while still on the flat part; True (a
    mob came into range) -> stop and return False so the loop can fight. This makes the approach one
    continuous walk instead of short steps with a full stop between them. Never checked once the
    descent has started (the jump window is ~0.2 s; a detection must not delay it).
    keep_moving: after the jump leave the heading HELD (the caller's next action takes over) --
    releasing it on landing left her standing ~0.9 s through the next detection beat."""
    face = keys["right"] if heading > 0 else keys["left"]
    kb.safe_press(face)
    x0 = base[0] if base is not None else None
    y_lvl = level if level is not None else (base[1] if base is not None else None)
    waited, limit, committed = 0.0, walk, False
    next_check = check_every
    while waited < limit:
        x, y = read_xy()
        if x is not None and x >= 0 and y is not None and y >= 0:
            if x0 is None:
                x0 = x
            if y_lvl is None:
                y_lvl = y                         # standing on the platform
            if abs(x - x0) <= x_win:
                if y - y_lvl >= jump_dy:          # at the slope bottom -> jump now
                    _jump(keys, kb, sleep, land=land)
                    if not keep_moving:
                        kb.safe_release(face)
                    return True
                if not committed and y - y_lvl >= commit_dy:
                    committed, limit = True, waited + commit_max   # on the slope: finish it
        if interrupt is not None and not committed and waited >= next_check:
            next_check = waited + check_every
            if interrupt():                           # a mob in range: stop and fight
                break
        sleep(poll); waited += poll
    kb.safe_release(face)
    return False


def landing_state(elapsed, mmy, target_y, min_air=0.55, tol=4, fall_dy=15, max_air=2.0):
    """After an edge jump: 'fell' (minimap y >= target_y + fall_dy -- dropping past the platform),
    'landed' (>= min_air s after the jump and y within tol of the landing platform's y), 'timeout'
    (stop waiting after max_air s) or 'air'. min_air matters: she takes off near the landing height
    (slope bottom ~60 vs platform 62), so an early read would look landed. Recorded flights land
    after 0.70-0.77 s."""
    if mmy is not None and mmy >= 0 and mmy >= target_y + fall_dy:
        return "fell"
    if elapsed >= min_air and mmy is not None and mmy >= 0 and abs(mmy - target_y) <= tol:
        return "landed"
    if elapsed >= max_air:
        return "timeout"
    return "air"


def prewalk_secs(pre_walk, since_attack, recover=0.3):
    """How long to walk before a push so that >= pre_walk s of it is REAL walking: right after an
    attack the animation ignores movement for ~`recover` s. since_attack = s since the last attack
    key was released (None = no attack yet). With the fast beat (~70 ms) the push came during that
    window and the 0.1 s pre-walk was swallowed (user)."""
    if since_attack is None:
        return pre_walk
    return pre_walk + max(0.0, recover - since_attack)


def recheck_before_walk(act, last_act, rechecked):
    """True = don't walk yet, look again first: the beat right after an attack (FIRE / GATHER)
    decided WALK, and this is not already the second look. Right after the AoE her skill effects
    cover the mobs and the detector misses them for a frame, so she walked off mobs still alive in
    front of her (middle_road2, user). The second look walks if it still sees nothing in range."""
    return act == WALK and last_act in (FIRE, GATHER) and not rechecked


def walk_back_to(target, heading, keys, kb, read_xy, sleep=lambda s: None, tol=1, timeout=2.0,
                 poll=0.03):
    """ONE straight walk in `heading` until the minimap x is within `tol` of target (or past it),
    capped at `timeout` s. For a spot that needs no precise line-up (a drop point): approach_x's
    tap-overshoot-back-up cycles took ~13 s here (live). True when reached."""
    key = keys["right"] if heading > 0 else keys["left"]
    kb.safe_press(key)
    waited, ok = 0.0, False
    while waited < timeout:
        x = read_xy()[0]
        if x is not None and x >= 0 and (target - x) * heading <= tol:
            ok = True
            break
        sleep(poll); waited += poll
    kb.safe_release(key)
    return ok


def drop_overshoot(seg, mmx, tol=3):
    """True when a down_jump segment's end finds her more than `tol` minimap px PAST its drop point
    (until_x) -- finishing can walk her on toward mobs, and a drop from there may land somewhere
    else (middle_road2: from x=74 she landed on the low step at (51, 111), not the bottom floor;
    every good drop started at 84-88). The caller walks her back to until_x first."""
    if seg.get("then") != "down_jump" or mmx is None or mmx < 0:
        return False
    return (mmx - seg["until_x"]) * seg_heading(seg) > tol


def rescue_move(seg_y, mmy, attempt, below=10):
    """Stuck-rescue step when no route segment fits where she is. If the current segment's floor is
    BELOW her (> `below` minimap px), try to get down: drop through (down_jump), then walk back the
    other way (off a ledge), then hop; cycling with `attempt`. Otherwise hop (a step up / over a
    ledge). Live 14:23: knocked onto middle_road2's low step (y 111) with the bottom floor (156) as
    the target, the hop-only rescue took her UP onto the middle floor."""
    if seg_y is not None and mmy is not None and mmy >= 0 and seg_y - mmy > below:
        return ("down_jump", "walk_back", "hop")[attempt % 3]
    return "hop"


def fell_below(seg, mmy, tol=10):
    """True when her minimap y is more than `tol` BELOW the segment's floor: she fell off it
    mid-segment (walked / was knocked off an edge) -- the loop resyncs by her real floor at once
    instead of walking the old segment's heading into a wall until the stuck timer fires."""
    return seg.get("y") is not None and mmy is not None and mmy >= 0 and mmy - seg["y"] > tol


def off_expected_floor(seg, mmy, tol=10):
    """After a transition that was NOT confirmed: True when she is clearly not on the segment's
    floor (then resync by her real floor instead of blindly advancing)."""
    return seg.get("y") is not None and mmy is not None and mmy >= 0 and abs(mmy - seg["y"]) > tol


def hop(heading, keys, kb, sleep=lambda s: None, hold=0.5):
    """Stuck recovery: walk the heading and jump (climbs a step / ledge she keeps walking into)."""
    face = keys["right"] if heading > 0 else keys["left"]
    kb.safe_release_all()
    kb.safe_press(face); sleep(0.1)
    kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
    sleep(hold)
    kb.safe_release(face)


def route_transition(then, cur_heading, next_heading, keys, kb, read_xy, sleep=lambda s: None,
                     up_wait=2.5, poll=0.1, y_change=8, nudge=0.12, jump_gap=0.3, align=None,
                     edge_wait=4.0, jump_dy=5, x_win=12, land=1.0, level=None, climb_wait=5.0,
                     target_y=None, level_tol=6, climb_tries=3, run_takeoff=None, run_up=8,
                     air_hold=0.3, align_to=None, fast_xy=None, run_timeout=3.0, steady_reads=2,
                     steady_secs=0.0, climb_land_tol=6, climb_finish=0.0, up_tries=2,
                     drop_tries=2):
    """Run a segment's end-transition. Returns True when it (probably) worked.

    "up":   stop and press up (portal/ladder). Confirmed when the minimap y moves by >= y_change
            within up_wait; if not, nudge back a step (she may have overshot the portal) and
            retry ONCE, then report failure (the loop just continues the next segment).
    "jump": hold the next segment's direction and double-jump (the gap onto the next platform).
    "edge_jump": keep walking the next direction down to the slope bottom (walk_to_edge: minimap
            y >= level + jump_dy), jump once and hold the direction `land` s to cross the gap. For
            where the minimap dot is pinned by scrolling, so x can't find the edge. Never reached
            within edge_wait -> jump anyway and report failure. Pass a FAST read_xy.
    "climb": (align on the ladder x) jump, hold up until she stands on the level above (minimap y
            up by >= y_change, then steady); no grab within up_wait -> re-align and retry ONCE.
            climb_wait caps the climb once it started. keys needs "up" and "jump". With target_y
            (the next segment's floor y) success means standing on THAT level: a plain jump
            that doesn't grab the ladder used to count as a climb (live). A miss (she lands back
            on the start level) re-aligns and retries, up to climb_tries attempts. run_takeoff =
            RUNNING jump instead (ladder_test.py): align_to(run_takeoff - heading*run_up), hold
            the heading, jump when fast_xy() reaches run_takeoff, arrow held air_hold s more.
    "down_jump": hold down + jump (drop through the floor), confirmed when she lands on a lower
            level (y down by >= y_change, then steady). keys needs "down".
    None:   nothing to do.
    cur_heading: the segment just finished (portal-overshoot nudge goes against it);
    next_heading: the segment about to start (the jump faces it).
    keys: {"up","jump","right","left"}; read_xy() -> (minimap x, y).
    align: optional callback run before EACH up press to stand exactly on the portal x (the loop
    passes core.walk_to_x); without it the retry does a blind one-step nudge back."""
    kb.safe_release_all()                         # WALK holds the arrow: stop at the end point
    if not then:
        return True
    if then == "jump":
        face = keys["right"] if next_heading > 0 else keys["left"]
        kb.safe_press(face)
        kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
        sleep(jump_gap)
        kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
        sleep(jump_gap)
        kb.safe_release(face)
        return True
    if then == "edge_jump":
        if walk_to_edge(next_heading, keys, kb, read_xy, sleep, walk=edge_wait, poll=poll,
                        jump_dy=jump_dy, x_win=x_win, land=land, level=level):
            return True
        face = keys["right"] if next_heading > 0 else keys["left"]   # never reached: jump anyway
        kb.safe_press(face)
        _jump(keys, kb, sleep, land=land)
        kb.safe_release(face)
        return False
    spent = [0.0]                                 # real time spent inside read_xy: stable_char(2) takes
                                                  # ~0.27 s, so counting only the poll sleeps
                                                  # made up_wait 2.5 s last ~9 s

    def _timed_y():                               # (-1,-1) = no minimap read -> not a level change
        t = _time.monotonic()
        y = read_xy()[1]
        spent[0] += _time.monotonic() - t
        return y if (y is not None and y >= 0) else None
    if then == "up":
        _y = _timed_y
        y0 = _y()
        for attempt in range(up_tries):
            if align is not None:
                align()                           # line up exactly on the portal x first
            elif attempt == 1:                    # no aligner: overshot? step back and retry once
                back = keys["left"] if cur_heading > 0 else keys["right"]
                kb.safe_press(back); sleep(nudge); kb.safe_release(back)
            kb.safe_press(keys["up"]); sleep(0.25); kb.safe_release(keys["up"])
            waited = 0.0; spent[0] = 0.0
            while waited + spent[0] < up_wait:
                y = _y()
                if y0 is None:
                    y0 = y                            # no baseline yet: take the first good read
                elif y is not None and abs(y - y0) >= y_change:
                    return True
                sleep(poll); waited += poll
        return False
    if then in ("climb", "down_jump"):
        _y = _timed_y
        sign = -1 if then == "climb" else 1       # climbing: y falls; dropping: y rises

        def arrived(y):
            # climb with a target: exactly on it -- the rope reads 52-57 just below the top floor
            # (51), so near-enough is still ON the rope. A drop: any LOWER landing -- floors are not
            # flat (middle_road2's bottom reads 148 on its raised left block, 143 on the car, 156
            # elsewhere); the caller checks which floor she is on afterwards.
            if then == "climb" and target_y is not None:
                return abs(y - target_y) <= climb_land_tol
            return (y - y0) * sign >= y_change
        for attempt in range(climb_tries if then == "climb" else drop_tries):
            if then == "climb" and run_takeoff is not None:
                # running jump: line up run_up px before the take-off, run in holding the heading,
                # jump at run_takeoff with the arrow still held for air_hold s, then hold up
                face = keys["right"] if cur_heading > 0 else keys["left"]
                if align_to is not None:
                    align_to(run_takeoff - cur_heading * run_up)
                y0 = _y()
                kb.safe_press(face)
                t_run = 0.0
                while t_run < run_timeout:
                    x = (fast_xy or read_xy)()[0]
                    if x is not None and x >= 0 and (x - run_takeoff) * cur_heading >= 0:
                        break
                    sleep(0.01); t_run += 0.01
                kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
                kb.safe_press(keys["up"])
                sleep(air_hold); kb.safe_release(face)
            elif then == "climb":                 # standing jump onto the ladder, then hold up
                if align is not None:
                    align()                       # stand on the ladder x first
                y0 = _y()
                kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
                kb.safe_press(keys["up"])
            else:                                 # hold down + jump = drop through the floor
                y0 = _y()
                kb.safe_press(keys["down"]); sleep(0.1)
                kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
                sleep(0.1); kb.safe_release(keys["down"])
            waited, moved, recent = 0.0, False, []; spent[0] = 0.0
            band = [None, 0.0]                    # [y the dot has stayed within 1 px of, since]
            while waited + spent[0] < (climb_wait if moved else up_wait):
                y = _y()
                if y0 is None:
                    y0 = y
                elif y is not None:
                    moved = moved or abs(y - y0) >= 3
                    # steady = the last steady_reads reads within 1 px (fast reads come ~0.1 s
                    # apart: 3 of them keep a jump's slow apex from passing as a landing)
                    recent = (recent + [y])[-steady_reads:]
                    steady = len(recent) == steady_reads and max(recent) - min(recent) <= 1
                    # ...and held for steady_secs: on the rope the dot sits ~0.2-0.3 s on each
                    # pixel, so a few quick equal reads happen mid-climb (live: 'climb ok' while
                    # still hanging on middle_road2's rope)
                    t_now = waited + spent[0]
                    if band[0] is None or abs(y - band[0]) > 1:
                        band[0], band[1] = y, t_now
                    steady = steady and t_now - band[1] >= steady_secs
                    if steady and arrived(y):
                        if then == "climb":
                            sleep(climb_finish)   # keep UP a moment: step off the rope top
                        kb.safe_release(keys["up"])
                        return True               # on the new level and not moving any more
                    if steady and moved and abs(y - y0) <= 2:
                        break                     # jumped but missed the ladder: landed back
                sleep(poll); waited += poll
            kb.safe_release(keys["up"])
            if moved and y is not None and abs(y - y0) > 2 and not arrived(y):
                return False                      # stuck mid-way (on the ladder?): don't retry
        return False
    raise ValueError(f"unknown route transition {then!r}")


# --- Route recovery ------------------------------------------------------------------------
# A failed jump/portal leaves her on a different level than the route expects; the segment's
# until_x may then be unreachable (flat_sweep: bottom floor ends ~285, segment 5 wants 307), so she
# walks into a wall forever. Progress detects that; resync_segment picks where to continue.

class Progress:
    """Stuck detector: WALKING time with no minimap-x gain in the heading. Only time spent walking
    counts (mark_walking() after a walk action), so fighting in place is never 'stuck'."""

    def __init__(self, stuck_secs=4.0, min_gain=2):
        self.stuck_secs, self.min_gain = stuck_secs, min_gain
        self.reset()

    def reset(self):
        self.best, self.walk, self.t, self.walking = None, 0.0, None, False

    def mark_walking(self):
        self.walking = True

    def update(self, now, mmx, heading):
        """Call once per beat with the current minimap x. True = stuck (walked stuck_secs with no
        gain of min_gain px toward the heading)."""
        if self.walking and self.t is not None:
            self.walk += now - self.t
        self.t, self.walking = now, False
        if mmx is not None and mmx >= 0:
            p = mmx * heading
            if self.best is None or p >= self.best + self.min_gain:
                self.best, self.walk = p, 0.0
        return self.walk >= self.stuck_secs


def resync_segment(route, cur_i, mmx, mmy, level_tol=10, same_floor=2):
    """Index of the segment to continue from when she is off-route: the next one in route order
    (after cur_i, cyclic) on HER floor whose until_x is still ahead of her. Her floor = the segment
    level(s) NEAREST her minimap y (within level_tol; levels within same_floor px of the nearest
    count as the same floor) -- a plain +-10 window matched the upper floor (y 55) for a read of
    y 63 on the right platform (y 62) and ping-ponged segments. None if nothing matches; segments
    without "y" never match."""
    ys = [abs(s["y"] - mmy) for s in route if s.get("y") is not None]
    if not ys or min(ys) > level_tol:
        return None
    best = min(ys)
    n = len(route)
    for k in range(1, n + 1):
        i = (cur_i + k) % n
        seg = route[i]
        if seg.get("y") is None or abs(seg["y"] - mmy) > best + same_floor:
            continue
        if not seg_done(seg, mmx):
            return i
    return None


class MinimapGate:
    """Drops phantom minimap reads: a read more than max_jump px (x) from the last accepted one
    within `stale` s is a phantom (she can't move that far that fast; stray yellow blobs on the
    minimap can). After `stale` s without an accepted read, any read is accepted (self-heals if a
    phantom got in first). reset() after a transition (a portal legitimately teleports her)."""

    def __init__(self, max_jump=40, stale=2.0):
        self.max_jump, self.stale = max_jump, stale
        self.reset()

    def reset(self):
        self.last, self.t = None, None

    def feed(self, x, y, now):
        if x is None or x < 0:
            return -1, -1
        if self.last is not None and now - self.t < self.stale and abs(x - self.last[0]) > self.max_jump:
            return -1, -1
        self.last, self.t = (x, y), now
        return x, y
