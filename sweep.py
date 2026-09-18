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
