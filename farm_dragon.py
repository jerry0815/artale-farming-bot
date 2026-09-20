"""Dragon-nest farming loop -- navigate the node-graph map (navmap), farm the top/bottom
platforms, rotate on depletion, recover after falls. Split out of core.py so each farm loop
is its own module (water => farm_water, general sweep => farm_general). Shares core's engine:
capture, focus, lie-check, detection/anchors, climbing + recovery primitives, buffs, STOP/STATUS.
"""
import core                              # the shared engine; every engine call goes through core.*
                                         # so tests can monkeypatch core.<name> and it takes effect.


def farming_loop_nav(exp_check=None, enemy_check=None, panic=None,
                     stand_secs=(6, 8), break_every=(8 * 60, 15 * 60),
                     rest_range=(30, 120), skill_interval=(240, 300),
                     deplete_threshold=1, max_seconds=None, breaks=True, cfg=None):
    """Node-graph farming loop with a STAND/WALK state machine. Each iteration runs
    ONE state move on the current platform (STAND_SHOOT or WALK_SHOOT), then counts
    dragons at the resulting standstill; a low count (< deplete_threshold) rotates to
    the next platform via travel(), otherwise the states alternate. Recovery and
    rotation both go through navmap.travel(). Preserves EXP/red-dot safety, 'n' pause,
    jittered breaks, and jittered skill/heal cadence. max_seconds bounds it."""
    import random as _r
    import monsters, navmap
    if cfg:                                    # per-map overrides (dragon_nest.json): tune rest cadence
        break_every = tuple(cfg.get("break_every", break_every))
        rest_range = tuple(cfg.get("rest_range", rest_range))
        breaks = cfg.get("breaks", breaks)
    if not core.focus():
        print("[nav] could not focus"); return
    print(f"[nav] state machine (stand/walk) + motion-based dragon counting"
          + (f" | breaks {int(rest_range[0])}-{int(rest_range[1])}s every "
             f"{int(break_every[0] / 60)}-{int(break_every[1] / 60)}min" if breaks else " | breaks OFF"))
    t_start = core.time.time()
    next_break = core.time.time() + _r.uniform(*break_every)
    next_skill = [core.time.time()]                    # cast skills from the first stint (as in split)
    current = "TOP_FARM"
    farm_state = navmap.STAND_SHOOT
    core.STOP.clear()
    core.STATUS["state"] = "farming"

    # Reactive deplete-check: with a YOLO model loaded we poll a fast single-frame
    # count WHILE shooting and rotate the instant the platform runs dry. Without a
    # model we fall back to the old motion count at the end of each move.
    model = monsters.load_dragon_model()
    reactive = model is not None
    if reactive:                                  # warm CUDA once so the first in-loop check is fast
        _f0 = core.capture()
        if _f0 is not None:
            monsters.detect_dragons_yolo(_f0, model, monsters.DEFAULT_MOTION_ROI)
    print(f"[nav] reactive deplete-check: {'YOLO (single-frame)' if reactive else 'off -> motion fallback'}")

    exp_tick = core.make_exp_tracker(label="nav")     # per-10-min EXP gain + running average
    core.STATUS["buff_at"] = None                      # fresh run (panel timers)
    active_tick, active_freeze = core.make_active_timer()  # ACTIVE farm time (excludes pauses)

    def one_count(nd):
        roi = monsters.MOTION_ROI_BY_NODE.get(nd, monsters.DEFAULT_MOTION_ROI)
        f = core.capture()
        c = None if f is None else len(monsters.detect_dragons_yolo(f, model, roi))
        core.STATUS["count"] = c
        return c

    def heal_skill():
        # Throttled to skill_interval (240-300s): the timed BUFFS A and J only. 'H' is
        # NOT here -- it is cast before every STAND_SHOOT (see cast_h), per user.
        if core.time.time() < next_skill[0]:
            return
        next_skill[0] = core.time.time() + _r.uniform(*skill_interval)
        core.kb.safe_press('a'); core.time.sleep(0.4); core.kb.safe_release('a')
        core.kb.safe_press('j'); core.time.sleep(0.4); core.kb.safe_release('j')
        core.STATUS["buff_at"] = core.time.time()               # panel buff timer

    def cast_h():
        # 'H' (heal/potion) before every STAND_SHOOT -- not throttled, per user request.
        core.kb.safe_press('h'); core.time.sleep(0.08); core.kb.safe_release('h')

    def go(dst):
        return navmap.travel(dst, locate_fn=core._nav_locate, execute_fn=core.execute_edge)

    core.start_safety_monitor()                            # curse/monster runs off the action loop
    while True:
        now = core.time.time()
        if core.STOP.is_set():
            core.kb.safe_release_all(); active_freeze(now); core.STATUS["state"] = "idle"
            core.stop_safety_monitor(); print("[nav] STOP -> stop"); return
        if max_seconds is not None and now - t_start > max_seconds:
            core.kb.safe_release_all(); active_freeze(now); core.STATUS["state"] = "idle"
            core.stop_safety_monitor(); print("[nav] max_seconds -> stop"); return
        if core.kb.pause:
            core.kb.safe_release_all(); core.lie_check_silence(); active_freeze(now)
            core.STATUS["state"] = "paused"; core.STATUS["lie"] = False; core.time.sleep(0.1); continue
        if not core.ensure_game_focused(label="nav"):       # keys must land in the GAME, not another
            active_freeze(now); core.time.sleep(0.3); continue   # window -> refocus before farming
        core.STATUS["state"] = "farming"
        active_tick(now)                              # advance ACTIVE farm time
        core.lie_check_fast_tick()                         # FULL curse/monster now runs in the monitor thread
        core.STATUS["lie"] = core.is_lie_check_active()
        exp_tick()                                    # per-10-min EXP logging (no-op between samples)

        # locate; recover onto a farm node if off-map
        node = core._nav_locate()
        core.STATUS["node"] = node
        if node is None:
            # Not in any mapped node band. Two cases: (a) a transient bad read (buff
            # glow, mid-fall) -> no clean position -> wait; or (b) a REACHABLE platform
            # we haven't added a node for -> a stable read. Never spin here: hand any
            # stable position to recover_to_farming (it has its own on-top / rope-top /
            # in-envelope / bail logic) and escape to safety only if it truly can't
            # climb out. This kills the whole "unmapped spot -> loop stops" bug class.
            x, y = core.stable_char(3)
            if x < 0:
                core.time.sleep(0.2); continue                 # no clean read -> tolerate, wait
            print(f"[nav] unmapped position ({x},{y}) -> recover to top")
            if core.recover_to_farming():
                current = "TOP_FARM"
            elif panic:
                print("[nav] unmapped & unrecoverable -> panic")
                panic(); core.time.sleep(1)
            else:
                core.time.sleep(1)                             # no panic hook -> avoid tight retry
            continue
        if node not in navmap.FARM_NODES:
            print(f"[nav] on {node} -> travel to {current}")
            if not go(current) and panic:
                panic(); core.time.sleep(1)
            continue

        # safety
        if enemy_check and enemy_check():
            print("[nav] another player -> ALARM + panic ('m' to silence)")
            core.enemy_alarm_on()
            if panic: panic()
            core.time.sleep(1); continue
        if exp_check and exp_check():
            print("[nav] EXP too low -> panic")
            if panic: panic()
            core.time.sleep(1); continue

        # scheduled break: drop to REST, rest, climb back
        if breaks and core.time.time() >= next_break:
            print("[nav] break -> rest on fallen platform")
            if core.drop_to_fallen():
                core._idle_on_fallen(_r.uniform(*rest_range))
            core.recover_to_farming()
            next_break = core.time.time() + _r.uniform(*break_every)
            current = "TOP_FARM"
            continue

        # --- one state move of the farming state machine ---
        current = node
        if farm_state == navmap.WALK_SHOOT:
            ok = core._walk_shoot_sweep(node)
        else:
            cast_h()                                     # 'H' before every STAND_SHOOT
            ok = core._stand_shoot(node, _r.uniform(*stand_secs),
                              count_fn=(lambda: one_count(node)) if reactive else None,
                              threshold=deplete_threshold)
        if ok is False:
            farm_state = navmap.STAND_SHOOT          # fell -> re-locate/recover, reset state
            continue
        heal_skill()

        # decide: rotate when the platform is depleted, else alternate stand<->walk.
        if ok is core.DEPLETED:                            # STAND's in-shoot poll already saw it dry
            depleted = True
        elif reactive:
            # STAND stayed populated for the whole beat; give WALK one cheap end-of-sweep check.
            if farm_state == navmap.WALK_SHOOT:
                n = one_count(node)
                depleted = n is not None and n < deplete_threshold
            else:
                depleted = False
        else:
            # no model: motion count needs static frames -> release keys, take the median count
            core.kb.safe_release_all()
            n = monsters.count_dragons_best(core.capture, node)
            core.STATUS["count"] = n
            print(f"[nav] {node} {farm_state} dragons~{n}")
            depleted = n < deplete_threshold

        if depleted:
            target = navmap.next_farm_target(node, 0, threshold=deplete_threshold)
            if target:
                print(f"[nav] {node} depleted -> travel to {target}")
                if go(target):
                    current = target
            farm_state = navmap.STAND_SHOOT          # start fresh on the new platform
        else:
            farm_state = navmap.WALK_SHOOT if farm_state == navmap.STAND_SHOOT else navmap.STAND_SHOOT
