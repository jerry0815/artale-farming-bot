"""General-map farming loop -- sweep a platform in one heading, killing scattered mobs with the
AoE and (optionally) a push/gather skill, then loop the route. Split out so each farm loop is
its own module (dragon => farm_dragon, water => farm_water). The brain is sweep.decide and the
actuator is sweep.apply_action; this module is just the live scaffolding around them, reusing
core's engine (capture, focus, lie-check, buffs, panic, STOP/STATUS) exactly like the others.

Config mirrors the water loop's keys (detector=mob_yolo, mob_*, anchor conf, attack_key,
buff_groups/buff_keys, enemy_threshold, map_box) plus the sweep's own:
  attack_range   AoE half-reach used by the sweep, px (default 200)
  aoe_max        max targets one AoE hits -- the satisfice cap (default 6)
  start_heading  +1 (face/move right) / -1 (left) (default 1)
  sweep_bounds   [mm_x_lo, mm_x_hi] minimap x of the platform ends -> turn around at an edge
  gather         push/group skill: {"key":"z","grp":90,"min":3,"dash_gap":50,"cooldown":1.5};
                 omit the whole block to run a plain walk-and-kill sweep with no push skill.
"""
import sweep
import core


def _player_pos(anchor, frame, pbox, last):
    """Player screen (x, feet_y) for this beat. With an HP-bar anchor (default, character-free),
    use its sticky red-bar locate; else (yolo_player mode) the model's class-2 box: center x and
    box bottom as feet. On a miss ride `last` (None -> caller waits for a lock).
    Returns (pos, updated_last)."""
    if anchor is not None:
        pos = anchor.locate(frame)
    else:
        pos = (pbox[1] + pbox[3] // 2, pbox[2] + pbox[4]) if pbox is not None else None
    if pos is not None:
        return pos, pos
    return last, last


def _reanchor(anchor, recent):
    """Re-seed the HP-bar tracker after a transition. A portal teleport pans the camera and the
    bar jumps far on screen; the sticky tracker (near_tol) lost her, re-acquired a red blob nearest
    screen center and followed it for ~3 s (live). Seed it at her usual screen spot (median of
    recent good positions) so find_player locks onto the bar there; no history -> forget the lock
    (fresh re-acquire). No-op in yolo_player mode (anchor None)."""
    if anchor is None:
        return
    if recent:
        xs = sorted(p[0] for p in recent); ys = sorted(p[1] for p in recent)
        anchor.last = (xs[len(xs) // 2], ys[len(ys) // 2])
    else:
        anchor.last = None


def _against_heading(prev, cur, heading, min_mm=8, min_px=60, home_x=None, away=150):
    """prev/cur = (screen x of the HP-bar lock, minimap x), the ends of a short window of beats.
    True when the minimap advanced >= min_mm in the heading but the lock's screen x moved >= min_px
    AGAINST it. The camera keeps her at ~the
    same screen x while she walks (or she moves WITH the heading when the camera is clamped), so a
    lock drifting backwards is scenery sliding past -- the tracker latched onto a STOP-sign sliver
    after a portal (live: mm 155->174 while "her" x went 907->838->800->726).
    home_x (her usual screen x on this floor): the camera catching up after a push dash also slides
    her backwards on screen (1147 -> 1026) but TOWARD home; a scenery lock slides AWAY from it, so
    with home_x it must end > `away` px from home and farther than it started."""
    (px0, mm0), (px1, mm1) = prev, cur
    if not ((mm1 - mm0) * heading >= min_mm and (px1 - px0) * heading <= -min_px):
        return False
    if home_x is None:
        return True
    return abs(px1 - home_x) > away and abs(px1 - home_x) > abs(px0 - home_x)


def _floor_key(mmy):
    """Minimap y rounded to the floor it belongs to (10 px buckets) -- her usual screen spot differs
    per floor (the camera clamps vertically): feet ~710 upper, ~798 bottom on flat_sweep."""
    return int(round(mmy / 10.0)) * 10


def _dump_attack(frame, mobs, kept, pos, act, seg_no, mmx, attack_range, reach, heading,
                 outdir="logs/sweep_dbg"):
    """debug_log: save the frame this attack decision was made on, annotated -- every mob box
    (green = counted: on her level; grey = dropped by the same-platform band) with its conf, her
    HP-bar position (red cross), the AoE range (yellow) and push reach (cyan) she decided with.
    Best-effort, never raises (rides the hot loop). Returns the path or None."""
    try:
        cv2 = core.cv2
        img = frame.copy()
        px, pfeet = pos
        for m in mobs:
            c, x, y, w, h = m[:5]
            col = (0, 220, 0) if (x + w // 2, y + h) in kept else (140, 140, 140)
            cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
            cv2.putText(img, f"{c:.2f}", (x, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
        cv2.drawMarker(img, (int(px), int(pfeet)), (0, 0, 255), cv2.MARKER_CROSS, 30, 3)
        for dx, col in ((-attack_range, (0, 220, 220)), (attack_range, (0, 220, 220)),
                        (reach * heading, (220, 220, 0))):
            X = int(px + dx)
            cv2.line(img, (X, int(pfeet) - 150), (X, int(pfeet) + 20), col, 2)
        cv2.putText(img, f"seg{seg_no} x={mmx} {act} counted={len(kept)}", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        core.os.makedirs(outdir, exist_ok=True)
        ts = core.time.strftime("%H%M%S", core.time.localtime()) + f"_{int((core.time.time() % 1) * 1000):03d}"
        path = core.os.path.join(outdir, f"{ts}_seg{seg_no}_{act}.jpg")
        cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return path
    except Exception:
        return None


def farming_loop_general(map_cfg, char=None, enemy_check=None, panic=None,
                         skill_interval=(240, 300), max_seconds=None):
    import random as _r  # noqa: F401  (kept for parity with the other loops' break jitter)
    if isinstance(map_cfg, str):
        map_cfg = core.watermap.load_map(map_cfg)
    if char is not None:                                   # overlay this character's keys
        map_cfg = core.apply_character(map_cfg, core.load_char(char))
    core.set_minimap(*core.watermap.minimap_crop(map_cfg))
    core.set_enemy_threshold(map_cfg.get("enemy_threshold", 0.75))
    if map_cfg.get("map_box"):
        core.set_map_box(*map_cfg["map_box"])
    # debug_log: per-beat decision trace -> logs/debug_<ts>.log, plus every route attack's frame
    # annotated -> logs/sweep_dbg/ (capped) -- to see WHAT she attacked when she hits air.
    _debug = bool(map_cfg.get("debug_log", False))
    _dump_left = [int(map_cfg.get("debug_max_frames", 400))]
    if _debug:
        _p = core.open_debug_log()
        print(f"[sweep] debug trace -> {_p} + attack frames -> logs/sweep_dbg/" if _p
              else "[sweep] debug trace: could not open file")
    if not core.focus():
        print("[sweep] could not focus"); return
    core.STOP.clear(); core.STATUS["state"] = "farming"

    # ONE unified mob YOLO gives both the mobs AND the player box per beat (shared pass).
    if map_cfg.get("detector", "mob_yolo") != "mob_yolo":
        raise RuntimeError("general sweep loop needs detector 'mob_yolo'")
    import mob_detect
    _mm = mob_detect.load_yolo(map_cfg.get("mob_model", "models/mob_yolo.pt"))
    _mconf = float(map_cfg.get("mob_conf", 0.6))
    _mimg = int(map_cfg.get("mob_imgsz", 640))
    _cconf = {}
    if map_cfg.get("fishhouse_conf") is not None:
        _cconf[0] = float(map_cfg["fishhouse_conf"])
    if map_cfg.get("goby_conf") is not None:
        _cconf[1] = float(map_cfg["goby_conf"])
    _cconf = _cconf or None
    _pconf = float(map_cfg.get("yolo_player_conf", _mconf))
    _pmax = map_cfg.get("player_anchor_max_jump")

    # Player anchor: default 'hpbar' = the generic red-HP-bar anchor (character/map-free, no
    # template, no training). 'yolo_player' = the model's class-2 box (needs a player-class model).
    _anchor = None
    if map_cfg.get("anchor", "hpbar") == "hpbar":
        import player as _pl
        _anchor = _pl.HPBarAnchor(cfg=map_cfg.get("player"))
        print("[sweep] anchor: hpbar (sticky, character-free)")
    else:
        print("[sweep] anchor: yolo_player (model class 2)")

    attack_range = int(map_cfg.get("attack_range", 200))
    _band = int(map_cfg.get("same_platform_band", 180))   # px: mob feet vs her feet (same level)
    band_up = int(map_cfg.get("band_up", _band))           # how far ABOVE her feet still counts
    band_down = int(map_cfg.get("band_down", _band))       # how far BELOW her feet still counts
    aoe_max = int(map_cfg.get("aoe_max", 6))
    heading = int(map_cfg.get("start_heading", 1))
    bounds = map_cfg.get("sweep_bounds")                   # [mm_x_lo, mm_x_hi] or None
    attack_key = map_cfg.get("attack_key", "c")
    gather = map_cfg.get("gather")
    keys = {"attack": attack_key, "dash": (gather or {}).get("key", "z"),
            "right": core.Key.right, "left": core.Key.left,
            "up": core.Key.up, "jump": core.JUMP}

    # Route mode: a recorded, cyclic list of segments (build_sweep_route.py). Heading is fixed per
    # segment; a segment ends when the minimap x reaches its until_x, then its transition ("up"
    # portal / "jump") runs. Without a route the loop ping-pongs one platform (sweep_bounds).
    # Start the character at the route's first segment start (where the demo run began).
    route = map_cfg.get("route") or None
    seg_i = [0]
    if route:
        heading = sweep.seg_heading(route[0])
        print(f"[sweep] route mode: {len(route)} segments, starting at segment 1 "
              f"({route[0]['dir']} to x={route[0]['until_x']})")

    # Normalize the gather block for sweep.decide (it derives dash = attack_range - dash_gap).
    gcfg = None
    if gather:
        gcfg = {"grp": int(gather.get("grp", 90)), "min": int(gather.get("min", 3)),
                "dash_gap": int(gather.get("dash_gap", 50))}
        if gather.get("dash") is not None:
            gcfg["dash"] = int(gather["dash"])
    gather_cd = float((gather or {}).get("cooldown", 1.5))
    next_gather = [0.0]
    # Route attack rhythm knobs: AoE presses per push, and how close to a segment end (minimap px)
    # she stops pushing so the dash can't carry her past the edge / portal / jump spot.
    aoe_hold = float(map_cfg.get("aoe_hold_secs", 1.05))      # hold x this long (continuous AoE)
    push_settle = float(map_cfg.get("push_settle_secs", 0.6))  # push animation before the AoE
    push_margin = int(map_cfg.get("push_edge_margin", 25))
    portal_tol = int(map_cfg.get("portal_tol", 2))             # minimap px to stand on the portal
    portal_lead = int(map_cfg.get("portal_lead", 20))          # start the portal line-up this early
    reach = sweep.dash_reach(attack_range, gcfg) if gcfg else attack_range

    # Buffs -- same scheme as farm_water (each group on its own interval).
    buff_groups = core.parse_buff_groups(map_cfg, skill_interval)
    _next_buff = [core.time.time() + 5.0 + i for i in range(len(buff_groups))]
    _buff_settle = float(map_cfg.get("buff_settle_secs", 0.6))
    _buff_gap = float(map_cfg.get("buff_gap_secs", 0.7))

    def heal_skill():
        now = core.time.time()
        due = [i for i in range(len(buff_groups)) if now >= _next_buff[i]]
        if not due:
            return
        core.kb.safe_release_all(); core.time.sleep(_buff_settle)   # let the attack root clear first
        for i in due:
            k_list, iv = buff_groups[i]; _next_buff[i] = now + iv
            print(f"[sweep] buff -> {k_list} (next {int(iv)}s)")
            for k in k_list:
                core.kb.safe_press(k); core.time.sleep(0.3); core.kb.safe_release(k)
                core.time.sleep(_buff_gap)
        core.STATUS["buff_at"] = core.time.time()

    t_start = core.time.time()
    active_tick, _freeze_active = core.make_active_timer()
    core.STATUS["buff_at"] = None
    last_px = [None]
    # EXP tracker: per-10-min gain + running average (same knobs as farm_water).
    exp_tick = core.make_exp_tracker(log_exp=map_cfg.get("log_exp", True),
                                     sample_secs=float(map_cfg.get("exp_sample_secs", 10)),
                                     window_secs=float(map_cfg.get("exp_window_secs", 600)),
                                     max_exp_per_sec=float(map_cfg.get("exp_max_per_sec", 20000)))

    def guard():
        """Per-beat housekeeping: STOP/time-cap/pause/focus/lie-check/EXP/enemy. Mirrors farm_water."""
        now = core.time.time()
        if core.STOP.is_set():
            core.kb.safe_release_all(); _freeze_active(now); core.STATUS["state"] = "idle"
            print("[sweep] STOP"); return "stop"
        if max_seconds is not None and now - t_start > max_seconds:
            core.kb.safe_release_all(); _freeze_active(now); core.STATUS["state"] = "idle"
            print("[sweep] max_seconds"); return "stop"
        if core.kb.pause:
            core.kb.safe_release_all(); core.lie_check_silence(); _freeze_active(now)
            core.STATUS["state"] = "paused"; core.STATUS["lie"] = False; core.time.sleep(0.1); return "pause"
        if not core.ensure_game_focused(label="sweep"):
            _freeze_active(now); core.time.sleep(0.3); return "skip"
        core.STATUS["state"] = "farming"; active_tick(now)
        core.lie_check_fast_tick(); core.STATUS["lie"] = core.is_lie_check_active()
        exp_tick()
        if enemy_check and enemy_check():
            print("[sweep] another player -> ALARM + panic")
            core.enemy_alarm_on()
            if panic:
                panic()
            core.time.sleep(1); return "skip"
        return "ok"

    edge_wait = float(map_cfg.get("edge_wait_secs", 6.0))  # max stepping time hunting the edge
    edge_step_secs = float(map_cfg.get("edge_step_secs", 0.4))
    zone_walk = [0.0]
    import collections as _col
    home_n = int(map_cfg.get("anchor_home_beats", 30))
    recent_pos = _col.defaultdict(lambda: _col.deque(maxlen=home_n))  # floor -> her usual spot
    portal_settle = float(map_cfg.get("portal_settle_secs", 1.5))   # camera pan after a portal
    lock_win = _col.deque(maxlen=4)  # (screen x, minimap x) of the last beats, for _against_heading
    # Recovery: phantom minimap reads are gated out; walking stuck_secs with no progress (a failed
    # jump/portal left her on a level where this segment's until_x is unreachable) -> resync.
    gate = sweep.MinimapGate(max_jump=int(map_cfg.get("mm_max_jump", 40)))
    progress = sweep.Progress(stuck_secs=float(map_cfg.get("stuck_secs", 4.0)))

    def _reanchor_here():
        """Re-seed the HP-bar lock at her usual screen spot for the floor she is on NOW."""
        x, y = core.stable_char(2)
        _reanchor(_anchor, list(recent_pos[_floor_key(y)]) if y >= 0 else [])
        lock_win.clear()

    def _resync(mmx, mmy, why):
        """Continue from the next route segment on the level she is actually on. Returns the new
        heading, or None if no segment matches (keep going; the stuck clock restarts)."""
        progress.reset()
        j = sweep.resync_segment(route, seg_i[0], mmx, mmy)
        if j is None:
            print(f"[sweep] {why} at x={mmx} y={mmy} -- no route segment on this level")
            return None
        print(f"[sweep] {why} at x={mmx} y={mmy} -> resync to segment {j + 1}")
        core.kb.safe_release_all()
        seg_i[0] = j; zone_walk[0] = 0.0
        _reanchor_here()
        core.STATUS["node"] = f"seg{j + 1}"
        return sweep.seg_heading(route[j])

    def _advance(ok, mmx):
        """Move to the next route segment (after its transition ran). Returns the new heading."""
        if not ok:
            print(f"[sweep] segment {seg_i[0] + 1}: '{route[seg_i[0]].get('then')}' at x={mmx} "
                  f"not confirmed -- continuing")
        seg_i[0] = (seg_i[0] + 1) % len(route)
        nxt = route[seg_i[0]]
        zone_walk[0] = 0.0
        gate.reset(); progress.reset()                 # a transition may teleport her (portal)
        _reanchor_here()                               # the camera moved: re-seed the HP-bar lock
        core.STATUS["node"] = f"seg{seg_i[0] + 1}"
        print(f"[sweep] -> segment {seg_i[0] + 1}: {nxt['dir']} to x={nxt['until_x']}"
              f"{' (travel)' if not nxt.get('attack', True) else ''}")
        return sweep.seg_heading(nxt)

    def _portal_align(seg, mmx, mmy):
        """Line up on the portal x arriving in the segment's heading (the recorded press side);
        fast single minimap reads, tracked near her last position."""
        last = [(mmx, mmy)]

        def read():
            x, y = core.get_character_color(near=last[0])
            if x >= 0:
                last[0] = (x, y)
            return x, y
        return sweep.approach_x(seg["until_x"], sweep.seg_heading(seg), keys, core.kb, read,
                                sleep=core.time.sleep, tol=portal_tol)

    def edge_step(heading, mmx, mmy):
        """One short step toward the edge_jump edge; on the walk-off it air-jumps and the route
        advances. Fast single reads tracked near her (a fall gives ~0.2 s to react)."""
        fell = sweep.walk_to_edge(heading, keys, core.kb,
                                  read_xy=lambda: core.get_character_color(near=(mmx, mmy)),
                                  sleep=core.time.sleep, walk=edge_step_secs, poll=0.03,
                                  base=(mmx, mmy), level=route[seg_i[0]].get("y"))
        zone_walk[0] += edge_step_secs
        if fell:
            print(f"[sweep] segment {seg_i[0] + 1}: slope bottom -> jump")
            return _advance(True, mmx)
        return heading

    print(f"[sweep] start: range={attack_range} aoe_max={aoe_max} heading={heading} "
          f"gather={('%s@%.1fs' % (keys['dash'], gather_cd)) if gcfg else 'off'} bounds={bounds}")
    try:
        while True:
            g = guard()
            if g == "stop":
                return
            if g in ("pause", "skip"):
                continue
            heal_skill()
            frame = core.capture()
            if frame is None:
                core.time.sleep(0.05); continue
            if route:
                seg = route[seg_i[0]]
                now = core.time.time()
                mmx, _mmy = gate.feed(*core.stable_char(2), now)
                if progress.update(now, mmx, heading):              # walking, going nowhere
                    h = _resync(mmx, _mmy, f"segment {seg_i[0] + 1} stuck")
                    if h is not None:
                        heading = h
                    continue
                # Portal legs hand over to the line-up portal_lead px early: walking reads come
                # ~0.3 s apart, so she overshot the spot and had to come back from the wrong side.
                done = mmx >= 0 and (sweep.seg_done(seg, mmx) or (
                    seg.get("then") == "up" and sweep.near_seg_end(seg, mmx, portal_lead)))
                # edge_jump segments: until_x is only where the minimap dot PINS (the minimap scrolls
                # on), not the edge -- so past it she keeps fighting and walks on in short steps
                # (edge_step) until she actually walks off, instead of one blind walk to the edge.
                edge_zone = done and seg.get("then") == "edge_jump"
                if edge_zone and seg.get("y") is not None and _mmy - seg["y"] >= 15:
                    # already BELOW the jump level: she dropped off without the jump firing
                    h = _resync(mmx, _mmy, f"segment {seg_i[0] + 1} fell before the jump")
                    if h is not None:
                        heading = h
                        continue
                if done and not edge_zone:                            # segment end -> transition
                    nxt = route[(seg_i[0] + 1) % len(route)]
                    print(f"[sweep] segment {seg_i[0] + 1} end at x={mmx} y={_mmy}")
                    ok = sweep.route_transition(seg.get("then"), sweep.seg_heading(seg),
                                                sweep.seg_heading(nxt), keys, core.kb,
                                                read_xy=lambda: core.stable_char(2),
                                                sleep=core.time.sleep,
                                                align=lambda: _portal_align(seg, mmx, _mmy))
                    if seg.get("then") == "up":
                        core.time.sleep(portal_settle)     # let the camera finish panning first
                    heading = _advance(ok, mmx)
                    continue
                if edge_zone and zone_walk[0] >= edge_wait:           # never found the edge
                    print(f"[sweep] segment {seg_i[0] + 1}: no walk-off after {zone_walk[0]:.1f}s "
                          f"of stepping -- jumping here")
                    ok = sweep.route_transition("edge_jump", heading, heading, keys, core.kb,
                                                read_xy=lambda: core.get_character_color(
                                                    near=(mmx, _mmy)),
                                                poll=0.03, sleep=core.time.sleep, edge_wait=0.5,
                                                level=seg.get("y"))
                    heading = _advance(ok, mmx)
                    continue
                if not seg.get("attack", True):                       # travel leg: just walk
                    if edge_zone:
                        heading = edge_step(heading, mmx, _mmy)
                        continue
                    sweep.apply_action(sweep.WALK, heading, keys, core.kb, sleep=core.time.sleep)
                    progress.mark_walking()
                    core.time.sleep(float(map_cfg.get("beat_gap", 0.05)))
                    continue
            mobs, pbox = mob_detect.yolo_detect(_mm, frame, conf=_mconf, imgsz=_mimg,
                                                class_conf=_cconf, near=last_px[0], max_jump=_pmax,
                                                player_conf=_pconf)
            if _anchor is not None and route and _mmy >= 0:
                # her usual screen spot: x pooled over all floors (the camera centers her), feet y
                # for THIS floor (the camera clamps vertically) -- find_player's home prior.
                xs = sorted(p[0] for d in recent_pos.values() for p in d)
                fy = sorted(p[1] for p in recent_pos[_floor_key(_mmy)])
                _anchor.home = ((xs[len(xs) // 2], fy[len(fy) // 2])
                                if len(xs) >= 5 and len(fy) >= 5 else None)
            pos, last_px[0] = _player_pos(_anchor, frame, pbox, last_px[0])
            if pos is None:
                if route and edge_zone:                    # no lock near the edge: careful step
                    heading = edge_step(heading, mmx, _mmy); continue
                if route:                                  # no player lock: keep walking the segment
                    sweep.apply_action(sweep.WALK, heading, keys, core.kb, sleep=core.time.sleep)
                    progress.mark_walking()
                core.time.sleep(0.05); continue            # no player lock yet -> wait
            px, pfeet = pos
            if route and mmx >= 0:
                lock_win.append((px, mmx))
                # her usual screen x is the same on every floor (the camera centers her
                # horizontally), so pool x across floors; y (camera clamp) is per floor.
                xs = sorted(p[0] for d in recent_pos.values() for p in d)
                home_x = xs[len(xs) // 2] if len(xs) >= 5 else None
                if len(lock_win) >= 2 and _against_heading(lock_win[0], lock_win[-1], heading,
                                                           home_x=home_x):
                    # over the last few beats she advanced but "she" slid backwards: scenery
                    print(f"[sweep] HP-bar lock drifting against the heading (x={px}) -> re-anchor")
                    _reanchor(_anchor, list(recent_pos[_floor_key(_mmy)]))
                    lock_win.clear()
                    continue
                recent_pos[_floor_key(_mmy)].append(pos)
            # decide() is x-only: keep only mobs on HER level (feet within the band), else a mob on
            # the floor above/below that she can't hit pins her in FIRE forever.
            mob_xs = sweep.same_platform([(m[1] + m[3] // 2, m[2] + m[4]) for m in mobs], pfeet,
                                         band_up=band_up, band_down=band_down)
            core.STATUS["count"] = len(mob_xs)
            if route:
                # Route attack leg, fixed rhythm: one push (z) then hold x (AoE); no push within
                # push_edge_margin of the segment end (edge/portal/jump spot); no mob -> walk on.
                # In an edge_jump zone the edge is somewhere ahead, unknown: never push (the dash could
                # carry her off it), and walk only in edge_step steps.
                near_edge = edge_zone or (mmx >= 0 and sweep.near_seg_end(
                    seg, mmx, int(seg.get("push_margin", push_margin))))   # per-segment override
                act = sweep.route_attack(mob_xs, px, heading, attack_range, reach, near_edge)
                if _debug:
                    core.dbg(f"[sweep] seg{seg_i[0] + 1} mm=({mmx},{_mmy}) p=({px},{pfeet}) "
                             f"mobs={[(m[1] + m[3] // 2, m[2] + m[4], round(m[0], 2)) for m in mobs]} "
                             f"kept={mob_xs} -> {act}{' edge' if near_edge else ''}")
                    if act != sweep.WALK and _dump_left[0] > 0:
                        _dump_left[0] -= 1
                        kept = [(m[1] + m[3] // 2, m[2] + m[4]) for m in mobs
                                if (m[1] + m[3] // 2) in mob_xs]
                        _dump_attack(frame, mobs, kept, (px, pfeet), act, seg_i[0] + 1, mmx,
                                     attack_range, reach, heading)
                if edge_zone and act == sweep.WALK:
                    heading = edge_step(heading, mmx, _mmy)
                    continue
                if act == sweep.GATHER:
                    if gcfg is None:
                        act = sweep.FIRE                      # no push skill configured
                    else:                                     # push on cooldown -> walk until ready
                        act = sweep.push_ready_or_walk(act, next_gather[0] - core.time.time())
                        if act == sweep.GATHER:
                            next_gather[0] = core.time.time() + gather_cd
                # edge zone: she already faces the heading (walked in), and the facing tap is a
                # step that walks her off the lip mid-AoE (seen live) -> no tap there.
                sweep.apply_action(act, heading, keys, core.kb, sleep=core.time.sleep,
                                   attack_hold=aoe_hold, pull_settle=push_settle,
                                   face_tap=not edge_zone)
                if act == sweep.WALK:
                    progress.mark_walking()
                core.time.sleep(float(map_cfg.get("beat_gap", 0.05)))
                continue
            now = core.time.time()
            cd_ok = gcfg is not None and now >= next_gather[0]
            action = sweep.decide(mob_xs, px, heading, attack_range,
                                  gather=gcfg, cd_ok=cd_ok, aoe_max=aoe_max)
            if action == sweep.GATHER:
                next_gather[0] = now + gather_cd
            # Minimap-edge safety: never WALK off the platform -- turn around at a configured end.
            if action == sweep.WALK and bounds is not None:
                mmx, _mmy = core.stable_char(2)
                if mmx >= 0 and ((heading > 0 and mmx >= bounds[1]) or
                                 (heading < 0 and mmx <= bounds[0])):
                    action = sweep.ADVANCE
            heading = sweep.apply_action(action, heading, keys, core.kb, sleep=core.time.sleep)
            core.time.sleep(float(map_cfg.get("beat_gap", 0.05)))
    finally:
        core.kb.safe_release_all()
