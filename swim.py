"""Extracted from core.py (behavior unchanged). Functions call the engine as core.<name>;
core.py re-exports them so callers/tests keep using core.<name> and monkeypatch still binds."""
import core


def _apply_swim_keys(want):
    """Hold exactly the arrow keys in `want`; release the rest."""
    for name, key in core._ARROW.items():
        if name in want:
            core.kb.safe_press(key)
        else:
            core.kb.safe_release(key)


def swim_to(target_x, target_y, tol=(3, 3), cap=8.0, locate=None, jump=True,
            jump_burst=4, jump_gap=0.04, axis="xy", settle=2, verbose=False, label="",
            start_near=None):
    """Swim toward (target_x, target_y) until within `tol` on both axes or `cap` seconds.
    Returns True on arrival. 'n' (kb.pause) aborts. `locate` (default get_character_full)
    is injectable for tests.

    LANDING: arrival requires `settle` CONSECUTIVE in-band reads (not one), and no jump is
    fired while confirming -- so she must be RESTING on the platform, not just passing
    through the target y mid-jump-arc. If she isn't seated she sinks back out of the band,
    the streak resets, and she jumps again. This is what makes her actually LAND (critical
    for the stacked P4 pin, where a momentary y-touch used to count as 'arrived').

    Water-world movement: you RISE by JUMPING repeatedly (holding Up does nothing), so when
    the target is above we spam JUMP and NEVER press Up. Left/right use arrow keys; descending
    just sinks. The position read (stable_char) is slow (~0.28s), so ONE jump per loop caps
    the rate at ~3/s; instead we fire a BURST of hops per read, scaled by the gap: far below
    -> up to `jump_burst` quick hops; near the target -> a single gentle hop (no overshoot).
    `jump_gap` = seconds between hops in a burst.

    `axis="x"` reaches the target X only (ignores Y, no jump) -- used by the reset to reach
    the rightmost open column without fighting the descent.

    Uses the fast two-sample swim_read by default (travel only needs a direction and the loop
    re-reads every iteration); landing robustness comes from `settle` CONSECUTIVE in-band reads,
    not from any single read, so a scattered phantom at a bad spot (e.g. P6's far-right edge)
    can't false-land -- it just costs one self-correcting iteration."""
    locate = locate or core.swim_read
    t0 = core.time.time()
    settle_hits = 0
    # Seed the phantom anchor with her DEPARTURE position: the very first read otherwise has no
    # `near`, so if her real dot is momentarily absent (mid fall/transition) the read falls back to
    # the largest blob -- which can be a fixed minimap phantom -- and then near LOCKS onto it. With
    # the departure seed, that first phantom is rejected as an implausible jump and she waits for a
    # real read instead of chasing it off-map.
    last = start_near if (start_near is not None and start_near[0] >= 0) else (-1, -1)
    _pfx = f"[swim {label}]" if label else "[swim]"

    def _arrived(x, y):
        if verbose:
            core.dbg(f"{_pfx} ARRIVED at ({x},{y}) tgt ({target_x},{target_y}) "
                f"gap dx={x - target_x} dy={y - target_y}")
    try:
        while core.time.time() - t0 < cap:
            if core.kb.pause:
                return False
            core.lie_check_fast_tick()
            # Anchor the read to her last known position so a phantom blob can't hijack direction.
            # Only the default swim_read takes `near`; injected locates (tests) stay zero-arg.
            x, y = core.swim_read(near=last) if locate is core.swim_read else locate()
            if x is None or x < 0:
                core.time.sleep(0.04); continue
            # Arrival needs her AT the target AND RESTING: if y is still rising she is falling
            # THROUGH the band (mid jump-arc / sinking), not seated -- that must not conclude arrival.
            sinking = last[1] >= 0 and y > last[1] + 0.5
            last = (x, y)
            want = core.watermap.swim_keys((x, y), (target_x, target_y), tol)
            if verbose:
                core.dbg(f"{_pfx} at ({x},{y}) tgt ({target_x},{target_y}) dx={x - target_x} "
                    f"dy={y - target_y} want={sorted(want)} sinking={sinking} hits={settle_hits}")
            if axis == "x":                               # horizontal only; arrived when x holds AND resting
                horiz = want & {"left", "right"}
                if not horiz:                             # x within tol -> confirm it HOLDS and she's not
                    settle_hits = settle_hits + 1 if not sinking else 0   # sinking through -> not landed;
                    if settle_hits >= settle:            # a single in-tol read (overshoot bounce) or a
                        _arrived(x, y); return True      # fall through the band must not stop her short
                    core._apply_swim_keys(set())              # of the column. Release arrows, re-read to
                    core.time.sleep(0.05); continue           # double-confirm before declaring arrival.
                settle_hits = 0                           # drifted off target x -> reset the confirm streak
                core._apply_swim_keys(horiz)
                core.time.sleep(0.05); continue
            if not want:                                  # in the target band -> confirm SEATED (resting)
                settle_hits = settle_hits + 1 if not sinking else 0   # in tol but still falling through
                if settle_hits >= settle:                             # -> not landed; needs to come to rest
                    _arrived(x, y); return True           # in tol AND not sinking `settle` reads -> landed
                core._apply_swim_keys(set())                   # release arrows; DON'T jump -> let her settle
                core.time.sleep(0.08); continue
            settle_hits = 0                               # drifted out of band -> not landed yet
            # Water-world vertical: rise = jump (no Up), descend = just sink (no Down).
            # Only horizontal keys are ever held.
            core._apply_swim_keys(want - {"up", "down"})
            if jump and "up" in want:
                # BURST of hops per read, scaled by the vertical gap: far below -> jump_burst
                # quick hops; near the target -> 1 gentle hop (don't overshoot up).
                dy = y - target_y
                span = 30.0
                frac = max(0.0, min(1.0, (dy - tol[1]) / span))
                hops = 1 + int(round(frac * (jump_burst - 1)))
                for _ in range(hops):
                    if core.kb.pause:
                        break
                    core.kb.safe_press(core.JUMP); core.time.sleep(0.02); core.kb.safe_release(core.JUMP)
                    core.time.sleep(jump_gap)
                continue                                   # burst paced this iter -> re-read now
            core.time.sleep(0.05)
        if verbose:
            core.dbg(f"{_pfx} CAP {cap:.0f}s at {last} tgt ({target_x},{target_y}) "
                f"gap dx={last[0] - target_x} dy={last[1] - target_y} (never seated in tol)")
        return False
    finally:
        for key in core._ARROW.values():
            core.kb.safe_release(key)
        core.kb.safe_release(core.JUMP)


def sink_to_bottom(bottom_y, cap=15.0, locate=None, settle=4, near=35):
    """Straight descent (no direction held): release ALL keys and let her SINK. Returns True once
    she LANDED after sinking -- was sinking, then stopped for `settle` reads AND is within `near`
    px of `bottom_y`. Used for start_sink (she's already over the drop). The RESET uses
    hold_to_bottom instead (it must WALK to the rightmost drop first). 'n' aborts."""
    locate = locate or core.get_character_full
    core.kb.safe_release_all()
    t0 = core.time.time()
    prev_y = None
    still = 0
    sank = False
    while core.time.time() - t0 < cap:
        if core.kb.pause:
            return False
        core.lie_check_fast_tick()              # unified safety scan: lie-check + another-player
        x, y = locate()
        if y is not None and y >= 0:
            if prev_y is not None:
                if y > prev_y + 0.5:                      # still descending
                    sank = True
                    still = 0
                else:
                    still += 1
                    if sank and still >= settle and y >= bottom_y - near:
                        return True                       # sank, then landed near the bottom
            prev_y = y
        core.time.sleep(0.1)
    return True


def hold_to_bottom(bottom_y, key, cap=15.0, near=35, settle=3):
    """Reset descent by WALKING: HOLD `key` continuously (toward the rightmost drop) the whole
    way -- she walks off the edge and falls, and holding never lets her stop short of the drop
    column the way a tol'd align could. No release on minor y wobble (that made her stutter:
    walk-stop-walk-stop). ARRIVED requires BOTH: she's at the target y (within `near` of
    `bottom_y`) AND she is NOT still sinking (y didn't rise since the last read) -- so a fast fall
    passing THROUGH the band can't conclude arrival; she must have come to REST on the bottom
    platform, for `settle` consecutive reads. The `near` band also rejects a far minimap phantom
    (e.g. y~347, well below the bottom platform). Releases `key` on exit. 'n' aborts."""
    core.kb.safe_release_all()
    core.kb.safe_press(key)
    t0 = core.time.time()
    hits = 0
    prev_y = None
    try:
        while core.time.time() - t0 < cap:
            if core.kb.pause:
                return False
            core.lie_check_fast_tick()
            x, y = core.get_character_full()
            valid = y is not None and y >= 0
            in_band = valid and abs(y - bottom_y) <= near
            resting = valid and prev_y is not None and y <= prev_y + 0.5   # not sinking (y not rising)
            hits = hits + 1 if (in_band and resting) else 0
            if valid:
                prev_y = y
            if core.dbg_on():
                core.dbg(f"[reset-hold] at ({x},{y}) tgt_bottom={bottom_y} in_band={in_band} "
                    f"resting={resting} hits={hits}")
            if hits >= settle:
                return True                               # at the bottom platform's y AND resting
            core.time.sleep(0.1)
        if core.dbg_on():
            core.dbg(f"[reset-hold] CAP {cap:.0f}s -- never settled at bottom (last y read above)")
        return True
    finally:
        core.kb.safe_release(key)


def water_shoot(center, seconds, count_fn=None, threshold=1, tol=(3, 3),
                check_every=0.5, debounce=2, attack_key='c'):
    """Park at a platform center and hold the attack key for a beat; if knocked off, swim
    back. With `count_fn` (fast single-frame YOLO count), returns DEPLETED as soon as
    `debounce` consecutive reads fall below `threshold`. Returns True otherwise / False
    if paused out."""
    cx, cy = center
    core.swim_to(cx, cy, tol=tol, cap=6.0)
    core._face_right(0.04)
    t0, nextc, low = core.time.time(), core.time.time() + check_every, 0
    core.kb.safe_press(attack_key)                             # HOLD attack (continuous)
    try:
        while core.time.time() - t0 < seconds:
            if core.kb.pause:
                return False
            if not core.focus_ok_or_bail("water"):    # focus lost -> release the held attack, refocus,
                return False                      # bail so the caller re-parks and re-holds attack
            core.lie_check_fast_tick()
            # Phantom-safe: anchor the read to the parked center and reject a blob that leaps far
            # from it (buff-glow speck). A bare read let one phantom fire a false "drifted off" ->
            # release attack + swim back, interrupting the beat. None -> keep firing (no false move).
            r = core._plausible_read((cx, cy), max_jump=core._SWIM_MAX_JUMP)
            if r is not None and (abs(r[0] - cx) > tol[0] + 8 or abs(r[1] - cy) > tol[1] + 8):
                core.kb.safe_release(attack_key)               # drifted off -> swim back, resume
                core.swim_to(cx, cy, tol=tol, cap=4.0)
                core._face_right(0.04); core.kb.safe_press(attack_key)
            if count_fn is not None and core.time.time() >= nextc:
                nextc = core.time.time() + check_every
                low, rotate = core._reactive_deplete(low, count_fn(), threshold, debounce)
                if rotate:
                    return core.DEPLETED
            core.time.sleep(0.1)
        return True
    finally:
        core.kb.safe_release(attack_key)
