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


def _static_off_centre(prev, cur, heading, centre_x, min_mm=10, max_px=15, off=250):
    """prev/cur = (screen x of the HP-bar lock, minimap x). True when the minimap advanced >= min_mm
    in the heading but the lock's screen x barely moved (< max_px) while sitting > `off` px from the
    screen centre. The camera centres her while it follows her, and when it is clamped at a map
    edge she moves across the screen -- so a still, off-centre lock is something else: live on
    middle_road2 it sat on a STOP-sign mob's HP bar at (505, 831) for 18 s (minimap 120 -> 226, her
    own bar hidden under the AoE effect) and she pushed into empty space 12 times."""
    (px0, mm0), (px1, mm1) = prev, cur
    return ((mm1 - mm0) * heading >= min_mm and abs(px1 - px0) < max_px
            and abs(px1 - centre_x) > off)


def _floor_key(mmy):
    """Minimap y bucketed to its floor (5 px buckets) -- her usual screen spot differs per floor
    (the camera clamps vertically): feet ~710 upper (y 55), ~798 right platform (y 62) and bottom
    (y 99-106) on flat_sweep. 10 px buckets merged 55 and 62."""
    return int(mmy // 5) * 5


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
            "up": core.Key.up, "down": core.Key.down, "jump": core.JUMP}

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
    push_prewalk = float(map_cfg.get("push_prewalk_secs", 0.0))  # walk this long, push while moving
    attack_recover = float(map_cfg.get("attack_recover_secs", 0.3))  # post-attack: moves ignored
    push_press = float(map_cfg.get("push_press_secs", 0.08))   # hold the push key this long
    push_prewalk_max = float(map_cfg.get("push_prewalk_max_secs", 0.8))  # cap on the pre-walk
    attack_end = [None]                                        # when the last attack key came up
    portal_tol = int(map_cfg.get("portal_tol", 2))             # minimap px to stand on the portal
    portal_lead = int(map_cfg.get("portal_lead", 20))          # start the portal line-up this early
    climb_tol = int(map_cfg.get("climb_tol", portal_tol))      # ladder line-up tolerance (px)
    portal_brake = int(map_cfg.get("portal_brake", 8))         # line-up: walk until this close, then tap
    reach = sweep.dash_reach(attack_range, gcfg) if gcfg else attack_range
    # engage "kite" (archer: shoots while walking): route attack legs walk on tapping the shot and
    # only stand to shoot when a mob ahead is inside kite_safe_dist screen px. Default "push" is the
    # push + AoE rhythm. Segment-end finishing is the same for both (stand + shoot within range).
    kite = map_cfg.get("engage", "push") == "kite"
    kite_safe = int(map_cfg.get("kite_safe_dist", 150))
    shot_press = float(map_cfg.get("shot_press_secs", 0.05))
    stand_shoot = float(map_cfg.get("stand_shoot_secs", 0.3))

    # Buffs -- same scheme as farm_water (each group on its own interval).
    buff_groups = core.buff_plan(map_cfg, skill_interval)       # [(keys, interval, potion)]
    _next_buff = [core.time.time() + 5.0 + i for i in range(len(buff_groups))]
    _buff_settle = float(map_cfg.get("buff_settle_secs", 0.6))
    _buff_gap = float(map_cfg.get("buff_gap_secs", 0.7))

    def heal_skill():
        now = core.time.time()
        due = [i for i in range(len(buff_groups)) if now >= _next_buff[i]]
        if not due:
            return
        for i in due:
            k_list, iv, potion = buff_groups[i]; _next_buff[i] = now + iv
            print(f"[sweep] {'potion' if potion else 'buff'} -> {k_list} (next {int(iv)}s)")
        # potions: instant, no stop; skills: stop, let the attack root clear, gap between casts
        core.cast_buffs([(buff_groups[i][0], buff_groups[i][2]) for i in due], core.kb,
                        core.time.sleep, settle=_buff_settle, gap=_buff_gap)
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

    # Another-player check costs ~65 ms (its own capture + template match): once per enemy_every s,
    # not every beat.
    enemy_every = float(map_cfg.get("enemy_check_secs", 1.0))
    _last_enemy = [0.0]
    # Another player on the map -> the panel's mode (core._ENEMY_MODE, read every beat so it can
    # change mid-run; never an alarm): "stop" = release keys and wait, resume by herself if they
    # leave, click Free Market (and stop) if still there after wait_secs; "notify" / "off" = keep
    # farming (the fast safety scan sends the Discord note). other_player.wait_secs overrides 20.
    wait_secs = float((map_cfg.get("other_player") or {}).get("wait_secs", 20))
    enemy_since = [None]
    if map_cfg.get("free_market_button_xy"):
        core._FM_XY[0] = tuple(map_cfg["free_market_button_xy"])

    def _other_player(now):
        """wait mode: 'wait' (keep waiting), 'escape' (clicked Free Market -> stop) or None."""
        if now - _last_enemy[0] < enemy_every:        # ~65 ms per check: once per enemy_every s
            return "wait" if enemy_since[0] is not None else None   # waiting: don't farm
        _last_enemy[0] = now
        if not enemy_check():
            if enemy_since[0] is not None:
                print(f"[sweep] other player left after {now - enemy_since[0]:.0f}s -> resume")
                enemy_since[0] = None
            return None
        if enemy_since[0] is None:
            enemy_since[0] = now
            core.kb.safe_release_all()
            print(f"[sweep] another player on the map -> stop and wait "
                  f"(Free Market if still here after {wait_secs:g}s)")
            return "wait"
        if now - enemy_since[0] >= wait_secs:
            print(f"[sweep] other player still here after {now - enemy_since[0]:.0f}s -> Free Market")
            core.click_free_market()
            return "escape"
        return "wait"

    def _mm_read(frame):
        """Minimap (x, y) from the beat's OWN frame (~0.3 ms) -- stable_char(2) took ~267 ms (4
        separate grabs + sleeps) every beat, the bulk of the ~0.5 s stall between actions. Tracks
        the blob nearest the last accepted read; MinimapGate still drops phantom jumps. Falls back
        to the colour+template read of the same frame."""
        x, y = core.get_character_color(frame, near=gate.last)
        if x < 0:
            x, y = core.get_character_full(frame)
        return x, y

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
        if enemy_check and core._ENEMY_MODE[0] == "stop":
            op = _other_player(now)
            if op == "escape":
                core.kb.safe_release_all(); _freeze_active(now); core.STATUS["state"] = "idle"
                return "stop"
            if op == "wait":
                _freeze_active(now); core.STATUS["state"] = "waiting (other player)"
                core.time.sleep(0.2); return "skip"
        elif enemy_since[0] is not None:              # switched away from "stop" mid-wait
            enemy_since[0] = None
        return "ok"

    edge_wait = float(map_cfg.get("edge_wait_secs", 6.0))  # max stepping time hunting the edge
    edge_step_secs = float(map_cfg.get("edge_step_secs", 3.0))  # one continuous walk (stops for mobs)
    air = [None]                    # (jump time, landing platform y) while airborne after a jump
    land_hits = [0]                 # consecutive in-range reads (one mid-arc read is not a landing)
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
    rescue_stuck = float(map_cfg.get("rescue_stuck_secs", 1.0))  # stuck window right after a rescue

    def _reanchor_here(y_hint=None):
        """Re-seed the HP-bar lock at her usual screen spot for the floor she is on NOW. y_hint
        (the next segment's floor y) skips the ~0.27 s stable_char read after a transition."""
        y = y_hint if y_hint is not None else core.stable_char(2)[1]
        _reanchor(_anchor, list(recent_pos[_floor_key(y)]) if y >= 0 else [])
        lock_win.clear()

    rescue_n = [0]                                  # consecutive stuck-rescue moves (rescue_move)
    below_n = [0]                                   # consecutive reads below the segment's floor

    def _resync(mmx, mmy, why):
        """Continue from the next route segment on the level she is actually on. Returns the new
        heading, or None if no segment matches (keep going; the stuck clock restarts)."""
        progress.reset()
        j = sweep.resync_segment(route, seg_i[0], mmx, mmy, level_tol=floor_tol)
        if j is None or j == seg_i[0]:
            # no other segment fits: she is on an odd spot (middle_road2: the low step left of the
            # middle floor, y 103-111). Floor needed BELOW her -> drop / walk off the ledge first;
            # otherwise (a step up or a wall) -> hop over it. Cycles on repeated stucks.
            h = sweep.seg_heading(route[seg_i[0]])
            move = sweep.rescue_move(route[seg_i[0]].get("y"), mmy, rescue_n[0])
            rescue_n[0] += 1
            print(f"[sweep] {why} at x={mmx} y={mmy} -- "
                  f"{'no route segment on this level' if j is None else 'same segment'}: {move}")
            core.dbg(f"[sweep] {why} at x={mmx} y={mmy} -> {move}")
            if move == "down_jump":
                core.kb.safe_release_all()
                core.kb.safe_press(keys["down"]); core.time.sleep(0.1)
                core.kb.safe_press(keys["jump"]); core.time.sleep(0.05)
                core.kb.safe_release(keys["jump"]); core.time.sleep(0.1)
                core.kb.safe_release(keys["down"]); core.time.sleep(0.8)
            elif move == "walk_back":
                back = keys["left"] if h > 0 else keys["right"]
                core.kb.safe_release_all()
                core.kb.safe_press(back); core.time.sleep(1.0); core.kb.safe_release(back)
                core.time.sleep(0.5)
            else:
                sweep.hop(h, keys, core.kb, sleep=core.time.sleep)
            progress.rescue(rescue_stuck)   # stairs: the next hop comes after ~1 s, not 4 s
            return h
        rescue_n[0] = 0
        print(f"[sweep] {why} at x={mmx} y={mmy} -> resync to segment {j + 1}")
        core.dbg(f"[sweep] {why} at x={mmx} y={mmy} -> resync to segment {j + 1}")
        core.kb.safe_release_all()
        seg_i[0] = j; zone_walk[0] = 0.0; finish_n[0] = 0
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
        zone_walk[0] = 0.0; finish_n[0] = 0
        gate.reset(); progress.reset()                 # a transition may teleport her (portal)
        last_act[0] = None                             # no walk-off re-look at a segment start
        h = sweep.seg_heading(nxt)
        # start walking the new heading NOW: the first beat (minimap read + capture + YOLO) takes
        # ~1 s, and she stood still through it at every segment start (user). The beat takes over.
        # Not when the new leg is a portal/ladder line-up she is already close to (middle_road2:
        # segment 2 ends 9 px from the ladder) -- a blind ~1 s walk carried her past the ladder.
        if not (nxt.get("then") in ("up", "climb") and mmx is not None and mmx >= 0
                and sweep.near_seg_end(nxt, mmx, portal_lead)):
            sweep.apply_action(sweep.WALK, h, keys, core.kb, sleep=core.time.sleep)
            progress.mark_walking()
        _reanchor_here(nxt.get("y"))                   # the camera moved: re-seed the HP-bar lock
        core.STATUS["node"] = f"seg{seg_i[0] + 1}"
        print(f"[sweep] -> segment {seg_i[0] + 1}: {nxt['dir']} to x={nxt['until_x']}"
              f"{' (travel)' if not nxt.get('attack', True) else ''}")
        return h

    aoe_hold_max = float(map_cfg.get("aoe_hold_max_secs", 6.0))  # cap on one AoE-until-clear hold

    def _still_in_range(heading, t0):
        """keep_holding for the AoE: re-detect on a fresh frame while x is held; True while a mob
        on her level is still in AoE range ahead (or overlapping her). False on pause/STOP, no frame
        or no player lock, or after aoe_hold_max s of wall time (a false box can't pin her)."""
        if core.STOP.is_set() or core.kb.pause or core.time.time() - t0 > aoe_hold_max:
            return False
        fr = core.capture()
        if fr is None:
            return False
        ms, pb = mob_detect.yolo_detect(_mm, fr, conf=_mconf, imgsz=_mimg, class_conf=_cconf,
                                        near=last_px[0], max_jump=_pmax, player_conf=_pconf)
        pos, last_px[0] = _player_pos(_anchor, fr, pb, last_px[0])
        if pos is None:
            return False
        xs = sweep.same_platform([(m[1] + m[3] // 2, m[2] + m[4]) for m in ms], pos[1],
                                 band_up=band_up, band_down=band_down)
        return any(-30 <= (x - pos[0]) * heading <= attack_range for x in xs)

    finish_max = int(map_cfg.get("finish_rounds", 3))   # AoE rounds allowed past a segment end
    finish_reach = int(map_cfg.get("finish_reach", 450))  # finishing walks to mobs this far (px)
    finish_step = float(map_cfg.get("finish_step_secs", 0.12))  # one finishing walk step (s)
    finish_edge_margin = int(map_cfg.get("finish_edge_margin", 6))  # stop walking this far (mm px)
                                                        # before a segment's floor_end
    last_act, rechecked = [None], [False]               # walk-off re-look (recheck_before_walk)
    walk_recheck = float(map_cfg.get("walk_recheck_secs", 0.15))
    finish_n = [0]

    climb_fails = [0]
    climb_rounds = int(map_cfg.get("climb_rounds", 3))    # line-up+climb rounds before moving on
    climb_retry = float(map_cfg.get("climb_retry_secs", 1.0))  # no take-off this long -> re-jump
    portal_retry = float(map_cfg.get("portal_retry_secs", 1.0))  # no teleport this long -> press again
    drop_retry = float(map_cfg.get("drop_retry_secs", 0.8))      # not falling this long -> press again
    drop_tol = int(map_cfg.get("drop_overshoot_px", 3))          # past the drop point -> walk back
    floor_tol = int(map_cfg.get("floor_tol", 10))   # minimap px: "on that floor" (floors aren't flat)

    def _transition(seg, mmx, mmy):
        """Run the segment's end transition (portal / jump / ladder climb / drop-through / none)
        and advance. Returns heading."""
        nxt = route[(seg_i[0] + 1) % len(route)]
        print(f"[sweep] segment {seg_i[0] + 1} end at x={mmx} y={mmy}")
        # stop NOW: a WALK holds the arrow until released, and the waits below (attack recovery)
        # kept her walking -- off middle_road2's middle floor onto the start platform (live)
        core.kb.safe_release_all()
        if seg.get("then") and attack_end[0] is not None:
            # the fast beat starts the transition ~70 ms after the last attack, while its animation
            # still swallows input: the portal UP was ignored and waited out up_wait (3-4.6 s
            # portals, live) -- let the animation finish first
            core.time.sleep(sweep.prewalk_secs(0.0, core.time.time() - attack_end[0],
                                               attack_recover))
        if sweep.drop_overshoot(seg, mmx, drop_tol):
            # finishing carried her past the drop point: walk back to it (a drop from farther on
            # can land on another ledge)
            core.dbg(f"[sweep] drop point {seg['until_x']} overshot (x={mmx}) -> walk back first")
            sweep.walk_back_to(seg["until_x"], -sweep.seg_heading(seg), keys, core.kb,
                               _fast_reader(mmx, mmy), sleep=core.time.sleep, tol=1)
        t0 = core.time.time()
        ok = sweep.route_transition(seg.get("then"), sweep.seg_heading(seg),
                                    sweep.seg_heading(nxt), keys, core.kb,
                                    # fast tracked reads (~17 ms) instead of stable_char(2)
                                    # (~267 ms each): landing/grab seen ~0.3-0.5 s sooner
                                    read_xy=_fast_reader(mmx, mmy), poll=0.05, steady_reads=3,
                                    steady_secs=0.4, climb_land_tol=1, climb_finish=0.3,
                                    sleep=core.time.sleep,
                                    align=lambda: _portal_align(seg, mmx, mmy),
                                    target_y=nxt.get("y"),   # climb/drop: must LAND on that floor
                                    # climb: no take-off within climb_retry_secs (hit before the
                                    # jump) -> line up and jump again (user: retry in ~1 s)
                                    # no take-off / teleport / fall this long -> press again
                                    # (a press right after an AoE is swallowed by its animation)
                                    up_wait={"climb": climb_retry, "up": portal_retry,
                                             "down_jump": drop_retry}.get(seg.get("then"), 2.5),
                                    up_tries=3, drop_tries=3,
                                    # running ladder jump (segment "run_takeoff", ladder_test.py)
                                    run_takeoff=seg.get("run_takeoff"),
                                    run_up=int(seg.get("run_up", 8)),
                                    air_hold=float(seg.get("air_hold", 0.3)),
                                    align_to=lambda x: _portal_align(seg, mmx, mmy, target=x),
                                    fast_xy=_fast_reader(mmx, mmy))
        t1 = core.time.time()
        if seg.get("then") == "up":
            core.time.sleep(portal_settle)                 # let the camera finish panning first
        if not ok and seg.get("then") == "climb":
            # knocked OFF the ladder's floor (a mob hit dropped her to the floor below): retrying
            # the line-up there is pointless -- continue from the route segment for that floor
            y_now = core.stable_char(2)[1]
            if y_now >= 0 and seg.get("y") is not None and abs(y_now - seg["y"]) > 10:
                climb_fails[0] = 0
                h = _resync(core.stable_char(2)[0], y_now, f"segment {seg_i[0] + 1} climb knocked off")
                if h is not None:
                    return h
        if not ok and seg.get("then") == "climb" and climb_fails[0] < climb_rounds - 1:
            # never got onto the ladder: stay on this leg and line up again -- advancing ran the
            # top-floor leg on the middle floor (live). climb_rounds caps it (then move on/resync).
            climb_fails[0] += 1
            print(f"[sweep] segment {seg_i[0] + 1}: climb failed ({climb_fails[0]}) -- retrying")
            core.dbg(f"[sweep] climb failed ({climb_fails[0]}) at x={mmx} -> retry the leg")
            progress.reset()
            return sweep.seg_heading(seg)
        climb_fails[0] = 0
        h = _advance(ok, mmx)
        if seg.get("then"):
            # is she on the new segment's floor? (live: a swallowed drop + a mob hit knocked her
            # onto the low step; a drop fell through two floors) -- if not, continue from her real
            # floor, or rescue toward this segment's floor. After ANY transition: a drop now
            # counts on any lower landing. One fast read (~17 ms; stable_char is ~267 ms).
            x_now, y_now = core.get_character_full()
            if sweep.off_expected_floor(route[seg_i[0]], y_now, tol=floor_tol):
                h2 = _resync(x_now, y_now, f"segment {seg_i[0] + 1} not on its floor")
                if h2 is not None:
                    h = h2
        core.dbg(f"[sweep] transition '{seg.get('then')}' ok={ok}: {t1 - t0:.2f}s, settle+reanchor "
                 f"{core.time.time() - t1:.2f}s")
        return h

    def _fast_reader(mmx, mmy):
        """Fast single minimap reads (~17 ms), tracked near her last position."""
        last = [(mmx, mmy)]

        def read():
            x, y = core.get_character_color(near=last[0])
            if x >= 0:
                last[0] = (x, y)
            return x, y
        return read

    def _moved_since(mmx, mmy, heading):
        """moved() for the push pre-walk: True once a fast minimap read is >= 1 px past mmx in the
        heading (she is really walking). No usable start read -> always True (plain pre_walk)."""
        if mmx is None or mmx < 0:
            return lambda: True
        rd, t0 = _fast_reader(mmx, mmy), core.time.time()

        def moved():
            x = rd()[0]
            ok = x >= 0 and (x - mmx) * heading >= 1
            if ok:
                core.dbg(f"[sweep] push pre-walk: moving after {core.time.time() - t0:.2f}s "
                         f"(mm {mmx}->{x})")
            return ok
        return moved

    def _portal_align(seg, mmx, mmy, target=None):
        """Line up on the portal x (or `target`, e.g. a running jump's run-up start) arriving in
        the segment's heading (the recorded press side)."""
        exact = seg.get("then") == "climb" and target is None
        return sweep.approach_x(seg["until_x"] if target is None else target,
                                sweep.seg_heading(seg), keys, core.kb, _fast_reader(mmx, mmy),
                                sleep=core.time.sleep,
                                tol=climb_tol if exact else portal_tol,
                                # the ladder grab needs a precise spot: tap the whole line-up
                                # (the walk-then-tap line-up landed off the ladder, user)
                                brake=10 ** 6 if exact else portal_brake)

    def edge_step(heading, mmx, mmy):
        """Walk on toward the edge_jump edge in ONE continuous walk (up to edge_step_secs) that only
        stops when a mob comes into range; at the slope bottom it jumps and the route advances. Fast
        single minimap reads tracked near her (the jump window is ~0.2 s)."""
        t_walk = core.time.time()
        fell = sweep.walk_to_edge(heading, keys, core.kb,
                                  read_xy=lambda: core.get_character_color(near=(mmx, mmy)),
                                  sleep=core.time.sleep, walk=edge_step_secs, poll=0.03,
                                  base=(mmx, mmy), level=route[seg_i[0]].get("y"),
                                  interrupt=lambda: _still_in_range(heading, core.time.time()),
                                  check_every=0.25, land=0.0, keep_moving=True)
        zone_walk[0] += core.time.time() - t_walk
        if fell:
            print(f"[sweep] segment {seg_i[0] + 1}: slope bottom -> jump")
            nxt = route[(seg_i[0] + 1) % len(route)]
            if nxt.get("y") is not None:                   # regular beats + a landing check
                air[0] = (core.time.time(), nxt["y"]); land_hits[0] = 0
            return _advance(True, mmx)
        return heading

    print(f"[sweep] start: range={attack_range} aoe_max={aoe_max} heading={heading} "
          f"gather={('%s@%.1fs' % (keys['dash'], gather_cd)) if gcfg else 'off'} bounds={bounds}"
          + (f" engage=kite safe={kite_safe}px" if kite else ""))
    core.start_safety_monitor()        # curse/monster popups (guard() only runs the fast captcha check)
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
                mmx, _mmy = gate.feed(*_mm_read(frame), now)
                if air[0] is not None:                              # just jumped: landed yet?
                    t_j, ty = air[0]
                    st = sweep.landing_state(now - t_j, _mmy, ty)
                    land_hits[0] = land_hits[0] + 1 if st == "landed" else 0
                    if st == "fell" and sweep.seg_done(seg, mmx, _mmy):
                        # the miss put her where this segment ends anyway (flat_sweep: the right-
                        # platform leg ends on landing on the bottom) -> normal transition below
                        print(f"[sweep] jump fell short -- already at segment {seg_i[0] + 1}'s end "
                              f"(x={mmx} y={_mmy})")
                        air[0] = None
                        st = "done"
                    if st == "landed" and land_hits[0] < 2:
                        # one read at the platform height can be her passing it mid-arc (live:
                        # "landed (y=59) 0.57s" and the jump had failed) -- confirm on the next beat
                        sweep.apply_action(sweep.WALK, heading, keys, core.kb,
                                           sleep=core.time.sleep)
                        continue
                    if st == "landed":
                        print(f"[sweep] landed (y={_mmy}) {now - t_j:.2f}s after the jump")
                        air[0] = None
                    elif st == "timeout" or now - t_j > 3.0:
                        air[0] = None
                    elif st == "fell" and sweep.resync_segment(route, seg_i[0], mmx, _mmy) is not None:
                        air[0] = None                  # missed it and landed on a known floor
                        h = _resync(mmx, _mmy, f"segment {seg_i[0] + 1} jump fell short")
                        if h is not None:
                            heading = h
                        continue
                    elif st == "done":
                        pass
                    else:                              # in the air / still dropping: keep going
                        sweep.apply_action(sweep.WALK, heading, keys, core.kb,
                                           sleep=core.time.sleep)
                        progress.mark_walking()
                        continue
                # fell off her segment's floor mid-segment (2 reads in a row, not one bad read) ->
                # continue from the floor she is on (live: walked off the middle floor onto the
                # start platform, then kept walking segment 5 left into the wall)
                below_n[0] = below_n[0] + 1 if (air[0] is None and sweep.fell_below(
                    seg, _mmy, floor_tol)) else 0
                if below_n[0] >= 2:
                    below_n[0] = 0
                    h = _resync(mmx, _mmy, f"segment {seg_i[0] + 1} fell off its floor")
                    if h is not None:
                        heading = h
                    continue
                if progress.update(now, mmx, heading):              # walking, going nowhere
                    h = _resync(mmx, _mmy, f"segment {seg_i[0] + 1} stuck")
                    if h is not None:
                        heading = h
                    continue
                # Portal/ladder legs hand over to the line-up portal_lead px early: walking reads come
                # ~0.3 s apart, so she overshot the spot and had to come back from the wrong side.
                done = mmx >= 0 and (sweep.seg_done(seg, mmx, _mmy) or (
                    seg.get("then") in ("up", "climb")
                    and sweep.near_seg_end(seg, mmx, portal_lead)))
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
                # Segment end: on attack legs first FINISH the mobs in AoE range (reaching the
                # point mid-fight moved her on with them alive); an until_y landing ends at once.
                landed = (seg.get("until_y") is not None and _mmy >= 0
                          and abs(_mmy - seg["until_y"]) <= 6)
                finishing = (done and not edge_zone and seg.get("attack", True) and not landed
                             and finish_n[0] < finish_max)
                if done and not edge_zone and not finishing:          # segment end -> transition
                    heading = _transition(seg, mmx, _mmy)
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
                if route and finishing:                    # can't see the fight: move on
                    heading = _transition(seg, mmx, _mmy); continue
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
                drift = len(lock_win) >= 2 and _against_heading(lock_win[0], lock_win[-1], heading,
                                                                home_x=home_x)
                _shape = getattr(frame, "shape", None)       # (tests pass a dummy frame)
                still = (len(lock_win) >= 2 and _shape is not None
                         and _static_off_centre(lock_win[0], lock_win[-1], heading, _shape[1] / 2))
                if drift or still:
                    # over the last few beats she advanced but "she" slid backwards (scenery) or
                    # sat still far off-centre (another bar, e.g. a mob's)
                    why = "drifting against the heading" if drift else "stuck off-centre"
                    print(f"[sweep] HP-bar lock {why} (x={px}) -> re-anchor"
                          + (" -- is her HP bar shown in game? (it locked onto a mob's bar)"
                             if still else ""))
                    core.dbg(f"[sweep] HP-bar lock {why} (x={px}, mm {lock_win[0][1]}->{mmx}) "
                             f"-> re-anchor")
                    # a still lock also polluted her usual spot: re-seed from the screen centre
                    _reanchor(_anchor, [] if still else list(recent_pos[_floor_key(_mmy)]))
                    if still:
                        recent_pos[_floor_key(_mmy)].clear()
                    lock_win.clear()
                    continue
                recent_pos[_floor_key(_mmy)].append(pos)
            # decide() is x-only: keep only mobs on HER level (feet within the band), else a mob on
            # the floor above/below that she can't hit pins her in FIRE forever.
            mob_xs = sweep.same_platform([(m[1] + m[3] // 2, m[2] + m[4]) for m in mobs], pfeet,
                                         band_up=band_up, band_down=band_down)
            core.STATUS["count"] = len(mob_xs)
            if route and finishing:
                # segment "floor_end": the floor goes on past the end point (a portal mid-floor):
                # walk on to mobs out there, up to floor_end - finish_edge_margin, then come back
                fe = seg.get("floor_end")
                can_walk = (fe is not None and mmx >= 0
                            and (fe - mmx) * heading > finish_edge_margin)
                act = sweep.finish_or_move(mob_xs, px, heading, attack_range, finish_n[0], finish_max,
                                           reach=finish_reach, can_walk=can_walk)
                if act == sweep.ADVANCE and last_act[0] == sweep.FIRE and not rechecked[0]:
                    rechecked[0] = True               # AoE effects hide mobs: look once more
                    core.dbg("[sweep] finishing: nothing seen right after the AoE -> one more look")
                    core.kb.safe_release_all()        # never keep walking while just looking
                    core.time.sleep(walk_recheck)
                    continue
                rechecked[0] = False
                if _debug:
                    core.dbg(f"[sweep] seg{seg_i[0] + 1} finish mm=({mmx},{_mmy}) p=({px},{pfeet}) "
                             f"kept={mob_xs} -> {act} (round {finish_n[0]}, walk ok={can_walk})")
                if act == sweep.ADVANCE:
                    heading = _transition(seg, mmx, _mmy)
                    continue
                if act == sweep.WALK:                 # close in on mobs past the end point
                    # SHORT steps, not a held walk: past the end point the floor edge is near and
                    # a held arrow kept walking through the next beat's waits (walked off, live)
                    last_act[0] = sweep.WALK
                    face = keys["right"] if heading > 0 else keys["left"]
                    core.kb.safe_press(face); core.time.sleep(finish_step)
                    core.kb.safe_release(face)
                    progress.mark_walking()
                    core.time.sleep(float(map_cfg.get("beat_gap", 0.05)))
                    continue
                if finish_n[0] == 0:
                    print(f"[sweep] segment {seg_i[0] + 1} end reached mid-fight -- finishing "
                          f"the mobs in range first")
                finish_n[0] += 1
                _t0, _h = core.time.time(), heading
                sweep.apply_action(sweep.FIRE, heading, keys, core.kb, sleep=core.time.sleep,
                                   attack_hold=aoe_hold, face_tap=False,
                                   keep_holding=lambda: _still_in_range(_h, _t0), hold_max=60)
                attack_end[0] = core.time.time()
                last_act[0] = sweep.FIRE
                continue
            if route and kite:
                act = sweep.kite_action(mob_xs, px, heading, kite_safe)
                if _debug:
                    ahead = [(x - px) * heading for x in mob_xs if (x - px) * heading >= -30]
                    core.dbg(f"[sweep] seg{seg_i[0] + 1} kite mm=({mmx},{_mmy}) p=({px},{pfeet}) "
                             f"nearest={min(ahead) if ahead else None} kept={mob_xs} -> {act}")
                last_act[0] = act
                sweep.apply_kite(act, heading, keys, core.kb, sleep=core.time.sleep,
                                 shot_press=shot_press, stand_secs=stand_shoot)
                if act == sweep.WALK_SHOOT:
                    progress.mark_walking()
                attack_end[0] = core.time.time()
                core.time.sleep(float(map_cfg.get("beat_gap", 0.05)))
                continue
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
                if sweep.recheck_before_walk(act, last_act[0], rechecked[0]):
                    rechecked[0] = True                   # one fresh look before walking off
                    core.dbg("[sweep] WALK right after an attack -> one more look first")
                    core.time.sleep(walk_recheck)          # let the AoE effects fade a little
                    continue
                rechecked[0] = False
                last_act[0] = act
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
                _t0, _h = core.time.time(), heading
                sweep.apply_action(act, heading, keys, core.kb, sleep=core.time.sleep,
                                   attack_hold=aoe_hold, pull_settle=push_settle,
                                   face_tap=not edge_zone, dash_press=push_press,
                                   # walk >= push_prewalk AND until the minimap shows her moving
                                   # forward: the AoE animation eats the walk for an unknown
                                   # time (a fixed 0.3 s guess still got swallowed, user)
                                   pre_walk=push_prewalk, moved=_moved_since(mmx, _mmy, heading),
                                   pre_walk_max=push_prewalk_max,
                                   # AoE-until-clear only for AoE-only (FIRE); after a push the
                                   # AoE stays the fixed 2 casts (user)
                                   keep_holding=(lambda: _still_in_range(_h, _t0))
                                   if act == sweep.FIRE else None, hold_max=60)
                if act == sweep.WALK:
                    progress.mark_walking()
                else:
                    attack_end[0] = core.time.time()
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
        core.stop_safety_monitor()
        core.kb.safe_release_all()
