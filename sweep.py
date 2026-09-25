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
                 face_tap=True, pre_walk=0.0):
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
        kb.safe_press(keys["dash"]); sleep(face_gap); kb.safe_release(keys["dash"])
        kb.safe_release(face)
        sleep(pull_settle)                               # let the pull group them before the AoE
    else:
        # FIRE or GATHER: stop walking, face the heading, act in place.
        kb.safe_release(keys["right"]); kb.safe_release(keys["left"])
        if face_tap:
            kb.safe_press(face); sleep(face_gap); kb.safe_release(face)
        if action == GATHER:
            kb.safe_press(keys["dash"]); sleep(face_gap); kb.safe_release(keys["dash"])
            sleep(pull_settle)                           # let the pull group them before the AoE
    if attack_hold:                                      # one continuous AoE hold (casts repeat)
        kb.safe_press(keys["attack"]); sleep(attack_hold); kb.safe_release(keys["attack"])
        return heading
    for _ in range(burst):
        kb.safe_press(keys["attack"]); sleep(attack_dwell); kb.safe_release(keys["attack"])
    return heading


# --- Route mode --------------------------------------------------------------------------
# A route is an ordered, cyclic list of segments recorded from a human run
# (build_sweep_route.py): {"dir": "right"|"left", "until_x": <minimap x>, "then": "up"|"jump"|null,
# "attack": bool, optional "until_y": end on reaching that floor}. Heading is FIXED for the whole segment; the segment ends when the minimap x
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
    there would dash her past it -- the loop skips the push near here."""
    return (seg["until_x"] - mmx) * seg_heading(seg) <= margin


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
               tap=0.045, poll=0.03, timeout=4.0):
    """Stand on minimap x `target` ARRIVING MOVING IN `heading` (the portal-press side). The press
    spot reads differently depending on the side she comes from; overshooting and correcting from
    the other side made UP miss (live). If she is past the target, walk back until `back` px before
    it, then approach in the heading with short taps (longer when far). True when within tol."""
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
            kb.safe_press(fk); sleep(min(0.3, tap + 0.02 * d)); kb.safe_release(fk)
            sleep(0.05)
    return False


def finish_or_move(mob_xs, px, heading, attack_range, rounds, max_rounds, behind_tol=30):
    """At a segment end: FIRE (AoE, no push -- the end is an edge / portal / turn) while a mob on
    her level is in AoE range ahead (or overlapping her), else ADVANCE to the transition. Reaching
    until_x mid-fight used to move her on with the mobs she was hitting still alive; overshooting
    the point a bit is fine. Capped at max_rounds so a respawn / false box can't pin her there.
    mob_xs must already be same-platform filtered."""
    if rounds >= max_rounds:
        return ADVANCE
    if any(-behind_tol <= (x - px) * heading <= attack_range for x in mob_xs):
        return FIRE
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
                 level=None):
    """Walk `heading` for up to `walk` s toward the edge_jump gap and jump at the SLOPE BOTTOM.

    The upper platform slopes down (minimap y 55 -> ~62) to the gap; recorded laps jump when the
    minimap reaches the bottom. Trigger: y >= level + jump_dy, where `level` is the PLATFORM's y
    (route segment "y") -- not wherever she happens to stand: re-basing on her own height after she
    stopped part-way down made the jump fire only after she had walked off the lower lip (too late,
    live). Already at/below the trigger on the first read -> jump at once. Once the descent starts
    (y >= level + commit_dy) the step is not cut short by `walk` (up to commit_max more s).
    No trigger: release, return False WITHOUT jumping so the loop can check for mobs. Reads more
    than x_win px from base x are phantoms and ignored. level=None -> first good read's y.
    read_xy must be FAST (single read)."""
    face = keys["right"] if heading > 0 else keys["left"]
    kb.safe_press(face)
    x0 = base[0] if base is not None else None
    y_lvl = level if level is not None else (base[1] if base is not None else None)
    waited, limit, committed = 0.0, walk, False
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
                    kb.safe_release(face)
                    return True
                if not committed and y - y_lvl >= commit_dy:
                    committed, limit = True, waited + commit_max   # on the slope: finish it
        sleep(poll); waited += poll
    kb.safe_release(face)
    return False


def route_transition(then, cur_heading, next_heading, keys, kb, read_xy, sleep=lambda s: None,
                     up_wait=2.5, poll=0.1, y_change=8, nudge=0.12, jump_gap=0.3, align=None,
                     edge_wait=4.0, jump_dy=5, x_win=12, land=1.0, level=None):
    """Run a segment's end-transition. Returns True when it (probably) worked.

    "up":   stop and press up (portal/ladder). Confirmed when the minimap y moves by >= y_change
            within up_wait; if not, nudge back a step (she may have overshot the portal) and
            retry ONCE, then report failure (the loop just continues the next segment).
    "jump": hold the next segment's direction and double-jump (the gap onto the next platform).
    "edge_jump": keep walking the next direction down to the slope bottom (walk_to_edge: minimap
            y >= level + jump_dy), jump once and hold the direction `land` s to cross the gap. For
            where the minimap dot is pinned by scrolling, so x can't find the edge. Never reached
            within edge_wait -> jump anyway and report failure. Pass a FAST read_xy.
    None:   nothing to do.
    cur_heading: the segment just finished (portal-overshoot nudge goes against it);
    next_heading: the segment about to start (the jump faces it).
    keys: {"up","jump","right","left"}; read_xy() -> (minimap x, y).
    align: optional callback run before EACH up press to stand exactly on the portal x (the loop
    passes core.walk_to_x); without it the retry does a blind one-step nudge back."""
    if not then:
        return True
    kb.safe_release_all()
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
    if then == "up":
        def _y():                                 # (-1,-1) = no minimap read -> not a level change
            y = read_xy()[1]
            return y if (y is not None and y >= 0) else None
        y0 = _y()
        for attempt in range(2):
            if align is not None:
                align()                           # line up exactly on the portal x first
            elif attempt == 1:                    # no aligner: overshot? step back and retry once
                back = keys["left"] if cur_heading > 0 else keys["right"]
                kb.safe_press(back); sleep(nudge); kb.safe_release(back)
            kb.safe_press(keys["up"]); sleep(0.25); kb.safe_release(keys["up"])
            waited = 0.0
            while waited < up_wait:
                y = _y()
                if y0 is None:
                    y0 = y                            # no baseline yet: take the first good read
                elif y is not None and abs(y - y0) >= y_change:
                    return True
                sleep(poll); waited += poll
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


def resync_segment(route, cur_i, mmx, mmy, level_tol=10):
    """Index of the segment to continue from when she is off-route: the next one in route order
    (after cur_i, cyclic) on HER level (seg "y" within level_tol of mmy) whose until_x is still
    ahead of her. None if no segment matches (segments without "y" never match)."""
    n = len(route)
    for k in range(1, n + 1):
        i = (cur_i + k) % n
        seg = route[i]
        if seg.get("y") is None or abs(seg["y"] - mmy) > level_tol:
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
