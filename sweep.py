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
                 burst=3, attack_dwell=0.2, face_gap=0.03, pull_settle=0.1, attack_hold=None):
    """Translate a decide() action into key presses via the injected `kb` (dependency-injected
    so this stays pure/testable -- no core import). Returns the heading for the NEXT beat
    (flipped on ADVANCE so a route with no explicit segments ping-pongs the platform).

    keys: {"attack","dash","right","left"} -- the attack key (water loop's attack_key), the
    push/gather key ('z'), and the two facing keys (core.Key.right/left live). The loop owns
    the gather cooldown: it passes cd_ok into decide(), and resets its timer after a GATHER.
    """
    face = keys["right"] if heading > 0 else keys["left"]
    if action == ADVANCE:
        kb.safe_release_all()
        return -heading
    if action == WALK:                                   # continuous travel toward heading
        kb.safe_release(keys["left"] if heading > 0 else keys["right"])
        kb.safe_press(face)
        return heading
    # FIRE or GATHER: stop walking, face the heading, act in place.
    kb.safe_release(keys["right"]); kb.safe_release(keys["left"])
    kb.safe_press(face); sleep(face_gap); kb.safe_release(face)
    if action == GATHER:
        kb.safe_press(keys["dash"]); sleep(face_gap); kb.safe_release(keys["dash"])
        sleep(pull_settle)                               # let the pull group them before the AoE
    if attack_hold:                                      # one continuous AoE hold (casts repeat)
        kb.safe_press(keys["attack"]); sleep(attack_hold); kb.safe_release(keys["attack"])
        return heading
    for _ in range(burst):
        kb.safe_press(keys["attack"]); sleep(attack_dwell); kb.safe_release(keys["attack"])
    return heading


# --- Route mode --------------------------------------------------------------------------
# A route is an ordered, cyclic list of segments recorded from a human run
# (build_sweep_route.py): {"dir": "right"|"left", "until_x": <minimap x>, "then": "up"|"jump"|null,
# "attack": bool}. Heading is FIXED for the whole segment; the segment ends when the minimap x
# reaches until_x, then its transition runs and the next segment starts.

def seg_heading(seg):
    return 1 if seg["dir"] == "right" else -1


def seg_done(seg, mmx):
    """True once the minimap x has reached the segment's until_x in its direction."""
    return mmx >= seg["until_x"] if seg_heading(seg) > 0 else mmx <= seg["until_x"]


def near_seg_end(seg, mmx, margin=25):
    """True when she is within `margin` minimap px of the segment's end (until_x) in its heading.
    Route segment ends are where the platform edge / portal / jump spot is, so a forward push
    there would dash her past it -- the loop skips the push near here."""
    return (seg["until_x"] - mmx) * seg_heading(seg) <= margin


def route_attack(mob_xs, px, heading, attack_range, reach, near_edge):
    """Route attack-leg rhythm: one push (dash-scoop) then AoE -- or just AoE near the edge.

    Returns GATHER (push + AoE burst), FIRE (AoE burst only: near_edge), or WALK (no mob on her
    level, or the nearest is ahead beyond push reach -> close in). Only mobs ahead, or just behind
    within attack_range (the AoE still hits them), count; she never turns back mid-segment.
    mob_xs must already be same-platform filtered."""
    fwd = [(x - px) * heading for x in mob_xs]
    if not any(-attack_range <= d <= reach for d in fwd):
        return WALK
    return FIRE if near_edge else GATHER


def walk_to_edge(heading, keys, kb, read_xy, sleep=lambda s: None, walk=0.4, poll=0.03,
                 fall_dy=3, x_win=12, land=0.6, base=None):
    """Walk `heading` for up to `walk` s watching for the walk-off (minimap y drops >= fall_dy).
    On the drop: one air-jump, hold the heading `land` s to cross the gap, return True. No drop:
    release and return False WITHOUT jumping, so the loop can stop and check for mobs (short steps
    keep the pinned-minimap stretch before an edge_jump an attack zone). Reads more than x_win px
    from the baseline are phantoms and ignored. base=(x, y) standing position if the caller already
has one (else the first good read). read_xy must be FAST (single read)."""
    face = keys["right"] if heading > 0 else keys["left"]
    kb.safe_press(face)
    x0, y0 = base if base is not None else (None, None)
    waited = 0.0
    while waited < walk:
        x, y = read_xy()
        if x is not None and x >= 0 and y is not None and y >= 0:
            if x0 is None:
                x0, y0 = x, y                     # standing on the platform
            elif abs(x - x0) <= x_win and y - y0 >= fall_dy:
                kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
                sleep(land)
                kb.safe_release(face)
                return True
        sleep(poll); waited += poll
    kb.safe_release(face)
    return False


def route_transition(then, cur_heading, next_heading, keys, kb, read_xy, sleep=lambda s: None,
                     up_wait=2.5, poll=0.1, y_change=8, nudge=0.12, jump_gap=0.3, align=None,
                     edge_wait=4.0, fall_dy=3, x_win=12, land=0.6):
    """Run a segment's end-transition. Returns True when it (probably) worked.

    "up":   stop and press up (portal/ladder). Confirmed when the minimap y moves by >= y_change
            within up_wait; if not, nudge back a step (she may have overshot the portal) and
            retry ONCE, then report failure (the loop just continues the next segment).
    "jump": hold the next segment's direction and double-jump (the gap onto the next platform).
    "edge_jump": keep walking the next direction until she walks OFF the edge (minimap y drops by
            >= fall_dy), then jump once in the air and hold the direction `land` s to cross the gap.
            For where the minimap dot is pinned by scrolling, so x can't find the edge. Reads more
            than x_win px from her start x are phantoms and ignored. No walk-off within edge_wait
            -> jump anyway and report failure. Pass a FAST read_xy (single read) -- a fall gives
            ~0.2 s to react.
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
                        fall_dy=fall_dy, x_win=x_win, land=land):
            return True
        face = keys["right"] if next_heading > 0 else keys["left"]   # never walked off: jump anyway
        kb.safe_press(face)
        kb.safe_press(keys["jump"]); sleep(0.05); kb.safe_release(keys["jump"])
        sleep(land)
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
