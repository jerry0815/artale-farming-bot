"""Water-map farming loop -- swim between platform centers, fire in place, rotate when a
platform runs dry. Split out of core.py so each farm loop is its own module (dragon =>
farm_dragon, general sweep => farm_general). Shares core's engine: capture, focus,
lie-check, detection/anchors, movement primitives, buffs, and the STOP/STATUS globals.
"""
import core                              # the shared engine; every engine call goes through core.*
                                         # so tests can monkeypatch core.<name> and it takes effect.


def farming_loop_water(map_cfg, char=None, enemy_check=None, panic=None,
                       stand_secs=(6, 8), break_every=(8 * 60, 15 * 60),
                       rest_range=(30, 120), skill_interval=(240, 300),
                       deplete_threshold=1, max_seconds=None):
    """Water-map farming: swim between platform centers, fire in place, rotate when a
    platform runs dry. Reuses lie-check, YOLO dragon counting, breaks, skills, panic,
    STATUS/STOP. `map_cfg` is a maps/<name>.json path or a loaded config dict. `char` is an
    optional chars/<name>.json path, dict, or None; when set, its key overrides are merged
    onto map_cfg (character -> map -> code default precedence) before anything is read."""
    import random as _r
    import monsters
    if isinstance(map_cfg, str):
        map_cfg = core.watermap.load_map(map_cfg)
    if char is not None:                              # overlay this character's keys
        map_cfg = core.apply_character(map_cfg, core.load_char(char))
    core._LAST_WATER_ATTACK_KEY[0] = map_cfg.get("attack_key", "c")
    core.set_minimap(*core.watermap.minimap_crop(map_cfg))
    core.set_enemy_threshold(map_cfg.get("enemy_threshold", 0.75))   # red-dot sensitivity (per map)
    if map_cfg.get("map_box"):                          # per-map valid dot box (reject phantoms)
        core.set_map_box(*map_cfg["map_box"])
    centers = core.watermap.node_centers(map_cfg)
    farm_nodes = map_cfg.get("farm_nodes") or list(centers)
    if not farm_nodes:
        print("[water] no farm nodes in map config"); return
    tol = core.watermap.swim_tol(map_cfg)
    # Jump burst for RISING between platforms: hops fired per position read (scaled by the
    # gap). Higher = faster rise (the slow read no longer caps the rate). P5->P4 wants snappy.
    _jb = int(map_cfg.get("swim_jump_burst", 4))
    _jg = float(map_cfg.get("swim_jump_gap", 0.04))    # seconds between hops in a burst (lower=faster)
    # debug_log: write a fine-grained per-frame trace to logs/debug_<ts>.log (file only, NOT
    # the console/panel), collecting what's needed to debug a run. Implies swim/approach traces.
    _debug = bool(map_cfg.get("debug_log", False))
    if _debug:
        _p = core.open_debug_log()
        print(f"[water] debug trace -> {_p}" if _p else "[water] debug trace: could not open file")
    _log_swim = _debug or bool(map_cfg.get("log_swim", False))   # swim pos vs target (-> debug file)
    if not core.focus():
        print("[water] could not focus"); return
    core.STOP.clear(); core.STATUS["state"] = "farming"
    print(f"[water] {map_cfg.get('name', '?')}: {len(centers)} nodes, order {farm_nodes}")

    # Detection: build ONE detect_fn(frame, roi) -> [(score,x,y,w,h)] used by BOTH approach
    # and depletion. 'mob_yolo' = trained YOLO (recommended), 'fish' = template match,
    # 'time' = no detection (rotate every beat), 'yolo' = legacy dragon model.
    detector = map_cfg.get("detector", "yolo")
    poll_in_shoot = False
    one_count = None
    detect_fn = None
    _count_roi = tuple(map_cfg["count_roi"]) if map_cfg.get("count_roi") else None

    if detector == "mob_yolo":
        import mob_detect
        _mm = mob_detect.load_yolo(map_cfg.get("mob_model", "models/mob_yolo.pt"))
        _mconf = float(map_cfg.get("mob_conf", 0.6))
        _mimg = int(map_cfg.get("mob_imgsz", 640))
        _cconf = {}                                        # per-class conf floor (fishhouse=0, goby=1)
        if map_cfg.get("fishhouse_conf") is not None:
            _cconf[0] = float(map_cfg["fishhouse_conf"])   # lower -> keep it through attack VFX
        if map_cfg.get("goby_conf") is not None:
            _cconf[1] = float(map_cfg["goby_conf"])
        _cconf = _cconf or None
        _wc = bool(map_cfg.get("attack_keys"))             # return the class name for per-mob skill

        def detect_fn(frame, roi):
            return mob_detect.yolo_boxes(_mm, frame, roi, _mconf, _mimg,
                                         class_conf=_cconf, with_class=_wc)
        print(f"[water] detector: mob_yolo conf={_mconf} imgsz={_mimg}"
              + (f" class_conf={_cconf}" if _cconf else "")
              + (f" attack_keys={map_cfg.get('attack_keys')}" if _wc else ""))
    elif detector == "fish":
        import fish as _fish
        _templates, _mode = _fish.templates_for(map_cfg)
        _thr = map_cfg.get("fish_threshold") or _fish.default_threshold(_mode)
        _ds = map_cfg.get("match_downscale", 0.5)

        def detect_fn(frame, roi):
            return _fish.scan(frame, _templates, roi=roi, threshold=_thr, downscale=_ds)["dets"]
        print(f"[water] detector: {_mode} templates x{len(_templates)} thr={_thr} ds={_ds}")
    elif detector == "time":
        print("[water] detector: time-based rotation (rotate every beat)")
    else:                            # legacy dragon yolo
        model = monsters.load_dragon_model()
        if model is not None:
            _f0 = core.capture()
            if _f0 is not None:
                monsters.detect_dragons_yolo(_f0, model, monsters.DEFAULT_MOTION_ROI)
            poll_in_shoot = True

            def one_count(node):
                roi = monsters.MOTION_ROI_BY_NODE.get(node, monsters.DEFAULT_MOTION_ROI)
                f = core.capture()
                c = None if f is None else len(monsters.detect_dragons_yolo(f, model, roi))
                core.STATUS["count"] = c
                return c
        print(f"[water] detector: {'dragon-YOLO' if one_count else 'none -> time'}")

    if detect_fn is not None and one_count is None:   # depletion count from detect_fn
        def one_count(node):
            f = core.capture()
            c = None if f is None else len(detect_fn(f, _count_roi))
            core.STATUS["count"] = c
            return c

    # Farming mode: 'walk_shoot' = minimap-only end-to-end sweep firing (no screen anchor,
    # robust); 'approach' = walk to the detected nearest mob (needs a stable player anchor);
    # else fixed-fire at the node center.
    attack_key = map_cfg.get("attack_key", "c")
    farm_mode = map_cfg.get("farm_mode", "approach" if map_cfg.get("approach") else "park")
    _phalf = int(map_cfg.get("platform_half", 60))    # minimap half-width to sweep for walk_shoot
    if farm_mode == "walk_shoot":
        if detect_fn is None:
            raise RuntimeError("walk_shoot needs detector 'mob_yolo' or 'fish'")
        print(f"[water] walk_shoot ON: detector={detector} platform_half={_phalf}")
    approach = farm_mode == "approach"
    _make_anchor = None
    if approach:
        if detect_fn is None:
            raise RuntimeError("approach needs detector 'mob_yolo' or 'fish'")
        import player as _p
        _arange = int(map_cfg.get("attack_range", 110))
        _aband = int(map_cfg.get("same_platform_band", 70))
        _band_up = map_cfg.get("node_band_up", {})     # per-node cap on mobs ABOVE her (P2: drop P1)
        _band_down = map_cfg.get("node_band_down", {})  # per-node cap on mobs BELOW her
        _astep = float(map_cfg.get("approach_step", 0.14))
        _adeplete = int(map_cfg.get("deplete_reads", 4))
        _astall = int(map_cfg.get("stall_limit", 8))
        if map_cfg.get("anchor") == "yolo_player":        # class-2 box of the unified mob YOLO
            if detector != "mob_yolo":
                raise RuntimeError("anchor 'yolo_player' needs detector 'mob_yolo'")
            import mob_detect as _md
            _pfoot = int(map_cfg.get("yolo_foot_offset", 0))   # HP-bar box bottom -> feet (~123px down)
            _pconf = float(map_cfg.get("yolo_player_conf", _mconf))   # player recall > mob (HP bar is small)
            _pgrace = int(map_cfg.get("yolo_stale_grace", 0))  # 0 -> approach_shoot governs misses (rooted vs walk)

            # YOLO pick veto is its OWN key (default OFF): whenever the class-2 box is detected we
            # USE it -- she crosses a platform faster than player_anchor_max_jump per beat, so a
            # cap here just vetoes a real fast move (log: box @780 conf0.34 "jumped" 301px, yet she
            # WAS at 780) and drops us onto the phantom-prone nametag. player_anchor_max_jump stays
            # for the composite's nametag/title guard only. Set yolo_player_max_jump to re-enable.
            _pjump = map_cfg.get("yolo_player_max_jump")
            def _make_anchor():
                return _md.YoloPlayerAnchor(_mm, conf=_pconf, imgsz=_mimg, foot_offset=_pfoot,
                                            stale_grace=_pgrace, max_jump=_pjump)
            print(f"[water] anchor: yolo_player conf={_pconf} veto={_pjump or 'off (always use detection)'}"
                  f" (class 2 of {map_cfg.get('mob_model', 'models/mob_yolo.pt')})")
        elif map_cfg.get("anchor") == "nametag":          # KenYu-style name-tag anchor
            _tagw = _p.load_nametag(map_cfg["nametag_template"])
            _titlew = _p.load_nametag(map_cfg["title_template"]) if map_cfg.get("title_template") else None

            def _make_anchor():
                name = _p.NametagAnchor(_tagw, feet_offset=int(map_cfg.get("nametag_feet_offset", 6)),
                                        accept_thres=float(map_cfg.get("nametag_accept", 0.55)))
                if _titlew is None:
                    return name
                title = _p.NametagAnchor(_titlew, feet_offset=int(map_cfg.get("title_feet_offset", 40)),
                                         accept_thres=float(map_cfg.get("title_accept", 0.55)))
                return _p.CompositeAnchor([name, title],   # name tag first, 稱號 fallback
                                          max_jump=map_cfg.get("player_anchor_max_jump"))
            print(f"[water] anchor: nametag ({map_cfg['nametag_template']})"
                  + (f" + title fallback ({map_cfg['title_template']})" if _titlew is not None else ""))
        else:
            def _make_anchor():
                return _p.HPBarAnchor(cfg=map_cfg.get("player"))
            print("[water] anchor: hpbar (sticky)")
        # Nametag FALLBACK: wrap the primary anchor so a lost primary lock (e.g. the attack
        # VFX covering the HP bar that yolo_player keys on) falls back to the name tag/title,
        # which sit BELOW the character and stay visible through the skill burst.
        _fb = map_cfg.get("nametag_fallback")
        # A character overrides the fallback tag/title with its OWN (top-level nametag_template /
        # title_template, merged in by apply_character) -- so playing "Lulala" matches Lulala's
        # name, not the map's default "Qoolo". Falls back to the map's when the character sets none.
        _fb_tag_path = map_cfg.get("nametag_template") or (_fb or {}).get("nametag_template")
        _fb_title_path = map_cfg.get("title_template") or (_fb or {}).get("title_template")
        if _fb and _fb_tag_path:
            _primary_make = _make_anchor
            _fb_tagw = _p.load_nametag(_fb_tag_path)
            _fb_titlew = _p.load_nametag(_fb_title_path) if _fb_title_path else None

            def _make_anchor():
                anchors = [_primary_make(),
                           _p.NametagAnchor(_fb_tagw,
                                            feet_offset=int(_fb.get("nametag_feet_offset", 6)),
                                            accept_thres=float(_fb.get("nametag_accept", 0.55)))]
                if _fb_titlew is not None:
                    anchors.append(_p.NametagAnchor(_fb_titlew,
                                                    feet_offset=int(_fb.get("title_feet_offset", 40)),
                                                    accept_thres=float(_fb.get("title_accept", 0.55))))
                return _p.CompositeAnchor(anchors,          # primary first, nametag(+title) fallback
                                          max_jump=map_cfg.get("player_anchor_max_jump"))
            print(f"[water] anchor fallback: nametag ({_fb_tag_path})"
                  + (f" + title ({_fb_title_path})" if _fb_titlew is not None else ""))
        print(f"[water] approach ON: detector={detector} range={_arange} band={_aband}")

    # Buff groups: each key set fires on its OWN interval (see core.parse_buff_groups).
    buff_groups = core.buff_plan(map_cfg, skill_interval)       # [(keys, interval, potion)]
    # per-group next-cast time; each first fires ~5s in (staggered 1s so they don't collide)
    _next_buff = [core.time.time() + 5.0 + i for i in range(len(buff_groups))]

    _buff_settle = float(map_cfg.get("buff_settle_secs", 0.6))   # wait out the attack root first
    _buff_gap = float(map_cfg.get("buff_gap_secs", 0.7))         # gap AFTER each cast so the next
    #                                                             buff isn't swallowed by its animation

    def heal_skill():
        now = core.time.time()
        due = [i for i in range(len(buff_groups)) if now >= _next_buff[i]]
        if not due:
            return
        for i in due:
            keys, iv, potion = buff_groups[i]
            _next_buff[i] = now + iv
            print(f"[water] {'potion' if potion else 'buff'} -> press {keys} (next in {int(iv)}s)")
        # Potions are instant. SKILLS: the attack ROOTS her (its animation eats a key pressed too
        # soon), so cast_buffs drops all keys, lets the root clear, and gaps each cast.
        core.cast_buffs([(buff_groups[i][0], buff_groups[i][2]) for i in due], core.kb,
                        core.time.sleep, settle=_buff_settle, gap=_buff_gap)
        core.STATUS["buff_at"] = core.time.time()

    # rotation: 'sweep' = farm the list in order (bottom->top) then reset via reset_node;
    # 'cyclic' (default) = farm current until depleted, advance to the next (wrapping).
    _ylift = int(map_cfg.get("node_y_lift", 5))          # aim this many px above node center
    _lift_override = map_cfg.get("node_lift_override", {})  # per-node lift (pin nodes -> 0)
    _arrive_jumps = map_cfg.get("node_arrive_jumps", {})   # extra hops after arriving (seat on pin)
    rotation = map_cfg.get("rotation", "cyclic")
    reset_node = map_cfg.get("reset_node")
    # Farm each platform until it's actually DEPLETED (no beat cap). node_max_secs is a
    # SAFETY only -- if a platform never clears (e.g. an unreachable edge mob), rotate after
    # this many seconds so the loop can't hang. 0 = no cap (farm until clear, may hang).
    _node_max = float(map_cfg.get("node_max_secs", 90))
    t_start = core.time.time()
    core.STATUS["buff_at"] = None                             # fresh run (panel timers)
    # ACTIVE farming time (excludes pauses) so total EXP / time / avg stay consistent.
    active_tick, _freeze_active = core.make_active_timer()
    next_break = [core.time.time() + _r.uniform(*break_every)]

    # EXP tracker: per-10-min gain + running average (see make_exp_tracker).
    exp_tick = core.make_exp_tracker(log_exp=map_cfg.get("log_exp", True),
                                sample_secs=float(map_cfg.get("exp_sample_secs", 10)),
                                window_secs=float(map_cfg.get("exp_window_secs", 600)),
                                max_exp_per_sec=float(map_cfg.get("exp_max_per_sec", 20000)))

    def guard():
        """Per-tick housekeeping. Returns 'stop' (return now), 'pause'/'skip'
        (continue the outer loop), or 'ok'."""
        now = core.time.time()
        if core.STOP.is_set():
            core.kb.safe_release_all(); _freeze_active(now); core.STATUS["state"] = "idle"; print("[water] STOP"); return "stop"
        if max_seconds is not None and now - t_start > max_seconds:
            core.kb.safe_release_all(); _freeze_active(now); core.STATUS["state"] = "idle"; print("[water] max_seconds"); return "stop"
        if core.kb.pause:
            core.kb.safe_release_all(); core.lie_check_silence(); _freeze_active(now)
            core.STATUS["state"] = "paused"; core.STATUS["lie"] = False; core.time.sleep(0.1); return "pause"
        if not core.ensure_game_focused(label="water"):     # keys must land in the GAME, not another
            _freeze_active(now); core.time.sleep(0.3); return "skip"   # window -> refocus before farming
        core.STATUS["state"] = "farming"
        active_tick(now)                                   # advance ACTIVE farm time
        core.lie_check_fast_tick(); core.STATUS["lie"] = core.is_lie_check_active()   # FULL runs in the monitor thread
        exp_tick()
        if enemy_check and enemy_check():
            print("[water] another player -> ALARM + panic ('m' to silence)")
            core.enemy_alarm_on()
            if panic: panic()
            core.time.sleep(1); return "skip"
        return "ok"

    _breaks = map_cfg.get("breaks", True)              # False -> farm continuously (no rest)

    def take_break_if_due():
        if not _breaks or core.time.time() < next_break[0]:
            return
        print("[water] break -> swim to base and idle")
        core.swim_to(*centers[farm_nodes[0]], tol=tol, cap=12.0, jump_burst=_jb)
        end = core.time.time() + _r.uniform(*rest_range)
        while core.time.time() < end and not core.kb.pause and not core.STOP.is_set():
            core.lie_check_fast_tick(); core.time.sleep(0.5)
        next_break[0] = core.time.time() + _r.uniform(*break_every)

    # Stacked pair: P3 sits directly ABOVE P4 and reads the same minimap y, so swim_to can't
    # move between them. When we reach the UPPER node right after its lower partner, jump-up
    # blindly instead of swimming (the "arrive P4 -> target P3" flag).
    stacked_up = {up: lo for lo, up in map_cfg.get("stacked_up", [])}
    stacked_jump_secs = float(map_cfg.get("stacked_jump_secs", 0.9))
    # Nodes reached AFTER a sink (start_sink / reset): she's already at the right y, so align
    # x-only (no jump) instead of a full swim_to that fights vertically off the floor.
    _x_align = set(map_cfg.get("x_align_nodes", []))
    _no_fall = set(map_cfg.get("no_fall_nodes", []))   # skip the minimap fall check on these nodes
    #                                                    (e.g. P3, where a bottom-edge phantom keeps
    #                                                    false-triggering re-seats)
    _arrive_retries = int(map_cfg.get("arrive_retries", 1))   # extra swim attempts if she didn't seat

    def _seat(node, make_swim):
        """Run a swim and RETRY if it didn't seat (swim_to returns False only when she never
        confirmed arrival -- in tol AND resting). First try seeds the departure anchor; retries
        re-anchor from her actual position. True once seated, False if still not after the retries
        (caller re-locates instead of farming the WRONG platform she happened to land on)."""
        for i in range(_arrive_retries + 1):
            if make_swim(i == 0):
                return True
            if core.kb.pause or core.STOP.is_set():
                return False
            print(f"[water] {node}: did not seat (try {i + 1}/{_arrive_retries + 1})")
        return False

    # Shared-pass detection (opt-in via map "shared_detect"): ONE yolo_detect per beat gives BOTH
    # the mobs AND the player box, halving hot-loop inference vs the separate detect_fn + anchor
    # passes. Only when detector+anchor are the SAME mob YOLO; other detectors/anchors keep the
    # two-pass path (combined stays None). approach_shoot pushes the player box to the anchor.
    _combined = None
    if map_cfg.get("shared_detect") and detector == "mob_yolo" and map_cfg.get("anchor") == "yolo_player":
        _pmax = map_cfg.get("player_anchor_max_jump")

        def _combined(frame, near):
            return mob_detect.yolo_detect(_mm, frame, roi=None, conf=_mconf, imgsz=_mimg,
                                          class_conf=_cconf, with_class=_wc, player_conf=_pconf,
                                          near=near, max_jump=_pmax)
        print("[water] shared-pass detection ON (one YOLO inference/beat: mobs + player)")

    def farm_node(node, prev=None):
        cx, cy = centers[node]
        core.STATUS["node"] = node
        dep = centers.get(prev) if prev in centers else None   # departure anchor -> rejects the
        #                                          first-read phantom when her dot is briefly absent
        def _seat_on_platform(dep):
            """(Re)seat her on `node`: True if seated (blindly for a stacked jump), False if a swim
            couldn't confirm arrival. `dep` seeds the phantom anchor -- the departure on the first
            seat; None on a re-seat, where she reads fine off the platform she fell to."""
            if node in stacked_up and prev == stacked_up[node]:
                # The upper node isn't always DIRECTLY above -- P3 sits 18px right of P4 -- so a
                # straight-up jump falls back onto the lower platform. Drift toward the upper node's
                # x (for `stacked_drift_secs`) WHILE jumping, then jump straight up to seat on it.
                lo_x = centers[prev][0]
                hkey = core.Key.right if cx > lo_x + 2 else (core.Key.left if cx < lo_x - 2 else None)
                drift = float(map_cfg.get("stacked_drift_secs", 0.6))
                _hd = "R" if hkey == core.Key.right else ("L" if hkey == core.Key.left else "-")
                print(f"[water] --> {node}: jump-up from {prev} (stacked); drift {_hd} {drift}s (x {lo_x}->{cx})")
                t0j = core.time.time(); t_end = t0j + stacked_jump_secs
                while core.time.time() < t_end and not core.kb.pause:
                    if hkey and core.time.time() - t0j < drift:
                        core.kb.safe_press(hkey)            # drift toward the upper node's x...
                    elif hkey:
                        core.kb.safe_release(hkey)          # ...then straight up to land on it
                    core.kb.safe_press(core.JUMP); core.time.sleep(0.1); core.kb.safe_release(core.JUMP)
                core.kb.safe_release_all(); core.time.sleep(0.4) # let her fall onto the upper platform (seat)
                return True
            if node in _x_align:                       # already at the bottom y (just sank) -> x-only
                print(f"[water] --> farm {node}: align x to {cx} (no jump)")
                return _seat(node, lambda first: core.swim_to(cx, cy, tol=tol, cap=6.0, jump=False, axis="x",
                                                         verbose=_log_swim, label=node,
                                                         start_near=dep if first else None))
            lift = _lift_override.get(node, _ylift)        # pin nodes (P4) use 0 -- target below
            ntol = core.watermap.node_swim_tol(map_cfg, node, tol)   # tighten tol_y_up on stacked pins
            print(f"[water] --> farm {node} (center {cx},{cy}) lift={lift} tol={ntol}")  # the pin is unreachable
            if not _seat(node, lambda first: core.swim_to(cx, cy - lift, tol=ntol, cap=12.0, jump_burst=_jb,
                                                     jump_gap=_jg, verbose=_log_swim, label=node,
                                                     start_near=dep if first else None)):  # aim ABOVE
                return False
            for _ in range(_arrive_jumps.get(node, 0)):    # seat on a pin platform (P4): a few more hops
                if core.kb.pause:
                    break
                core.kb.safe_press(core.JUMP); core.time.sleep(0.12); core.kb.safe_release(core.JUMP); core.time.sleep(0.05)
            return True

        if not _seat_on_platform(dep):
            print(f"[water] {node}: could not seat -> re-locate")
            return False                               # don't farm the wrong platform she landed on
        heal_skill()                                   # cast buffs here: just arrived, NOT mid-attack
        node_anchor = _make_anchor() if _make_anchor else None   # fresh lock per platform, tracks across beats
        _node_t0 = core.time.time()
        while True:                                    # farm THIS platform until DEPLETED (no beat cap)
            if core.kb.pause or core.STOP.is_set():
                return False
            if _node_max and core.time.time() - _node_t0 > _node_max:
                print(f"[water] {node}: {_node_max:.0f}s safety cap -> rotate (couldn't clear)")
                break
            if farm_mode == "walk_shoot":
                ok = core.walk_shoot(centers[node][0], _r.uniform(*stand_secs), detect_fn,
                                attack_key=attack_key, half=_phalf, tol=tol,
                                deplete_reads=int(map_cfg.get("deplete_reads", 2)), label=node)
                heal_skill()
                if ok is False:
                    return False
                if ok is core.DEPLETED:
                    break
                continue
            if approach:
                _ncx = centers[node][0]                    # keep her on THIS platform (minimap)
                ok = core.approach_shoot(_r.uniform(*stand_secs), detect_fn, node_anchor,
                                    attack_range=_arange, band=_aband, step=_astep,
                                    band_up=_band_up.get(node), band_down=_band_down.get(node),
                                    attack_key=attack_key, deplete_reads=_adeplete,
                                    stall_limit=_astall, label=node,
                                    mm_bounds=(_ncx - _phalf, _ncx + _phalf),
                                    mm_y=(None if node in _no_fall else cy),
                                    fall_margin=int(map_cfg.get("fall_margin", 22)),
                                    ease_margin=int(map_cfg.get("approach_ease_margin", 45)),
                                    attack_keys=map_cfg.get("attack_keys"),
                                    priority_class=map_cfg.get("priority_class"),
                                    priority_range=map_cfg.get("priority_range"),
                                    priority_hold_hits=int(map_cfg.get("priority_hold_hits", 0)),
                                    priority_lock_grace=int(map_cfg.get("priority_lock_grace", 0)),
                                    fall_confirm=int(map_cfg.get("fall_confirm", 2)),
                                    continuous_attack=bool(map_cfg.get("continuous_attack", False)),
                                    attack_dwell=float(map_cfg.get("attack_dwell_secs", 0.25)),
                                    face_deadzone=int(map_cfg.get("face_deadzone", 15)),
                                    face_settle=float(map_cfg.get("face_settle_secs", 0.2)),
                                    fire_stall_limit=int(map_cfg.get("fire_stall_limit", 5)),
                                    lock_switch_px=int(map_cfg.get("lock_switch_px", 70)),
                                    collect_misses=bool(map_cfg.get("collect_miss_frames", False)),
                                    fail_dump_interval=float(map_cfg.get("yolo_fail_dump_interval", 3.0)),
                                    combined=_combined)
                heal_skill()
                if ok is False:
                    return False
                if ok is core.LOST:                           # anchor blind too long -> re-seat (don't
                    print(f"[water] {node}: player anchor lost -> re-seat")   # blind-attack to the cap)
                    _lost_at = core.stable_char()
                    if not _seat_on_platform(_lost_at if _lost_at[0] >= 0 else None):
                        return False
                    node_anchor = _make_anchor() if _make_anchor else None
                    _node_t0 = core.time.time()
                    continue
                if ok is core.FELL:                           # knocked/walked off mid-beat -> re-seat & farm on
                    print(f"[water] {node}: fell off platform -> re-seat")
                    # PHANTOM GUARD: seed the re-seat swim with her ACTUAL fallen position
                    # (stable_char = densest-cluster read, phantom-safe). Without a seed the first
                    # swim read has no `near` and can lock onto a fixed minimap phantom -> she swims
                    # to the top-right map edge (the old phantom-runaway) instead of climbing back.
                    _fell_at = core.stable_char()
                    if not _seat_on_platform(_fell_at if _fell_at[0] >= 0 else None):
                        return False
                    node_anchor = _make_anchor() if _make_anchor else None
                    _node_t0 = core.time.time()
                    continue
                if ok is core.DEPLETED:
                    break                                # platform clear -> advance
                continue
            ok = core.water_shoot((cx, cy), _r.uniform(*stand_secs),
                             count_fn=(lambda: one_count(node)) if poll_in_shoot else None,
                             threshold=deplete_threshold, tol=tol, attack_key=attack_key)
            heal_skill()
            if ok is False:
                return False
            if ok is core.DEPLETED:
                break
            if one_count is not None:
                n = one_count(node)
                if n is not None and n < deplete_threshold:
                    break
            elif detector == "time":
                break                                    # time mode: one beat then advance
        return True

    core._FM_ESCAPE[0] = bool(map_cfg.get("free_market_escape", False))   # auto-leave if enemy un-acked
    core._FM_AFTER[0] = float(map_cfg.get("free_market_after_secs", 20.5))
    core._FM_XY[0] = tuple(map_cfg.get("free_market_button_xy", (1452, 1092)))
    core._enemy_alarm_since[0] = None; core._fm_clicked[0] = False              # fresh run
    if core._FM_ESCAPE[0]:
        print(f"[free-market] auto-escape ON: click 自由市場 if the enemy alarm is un-acked "
              f"{core._FM_AFTER[0]:g}s (button frame {core._FM_XY[0]})")
    core.start_safety_monitor()                            # curse/monster runs off the action loop
    if rotation == "sweep":
        print(f"[water] sweep {farm_nodes} then reset via {reset_node or '(bottom)'}")
        if map_cfg.get("start_sink"):                     # farm_nodes[0] is the bottom -> drop
            _b0 = centers[farm_nodes[0]][1]               # straight down instead of swim-wandering
            print(f"[water] start -> sink to bottom (y={_b0}) before first sweep")
            core.sink_to_bottom(_b0, cap=15.0, near=int(map_cfg.get("sink_near", 35)))
        _resume_i = 0                                     # a break RECORDS the platform she was on so
        while True:                                       # unpause CONTINUES there, not always P6
            g = guard()
            if g == "stop":
                core.stop_safety_monitor(); return
            if g != "ok":
                continue
            broke = False
            prev = None
            for _i in range(_resume_i, len(farm_nodes)):  # bottom -> top, resuming where we stopped
                node = farm_nodes[_i]
                if guard() != "ok":
                    _resume_i = _i                        # remember her platform -> resume here on unpause
                    broke = True; break
                take_break_if_due()
                if not farm_node(node, prev=prev):       # prev enables the P4->P3 jump-up
                    _resume_i = _i
                    broke = True; break
                prev = node
            if broke:
                continue
            _resume_i = 0                                 # full sweep done -> reset to bottom, start fresh
            # reached the top -> reset to the bottom. NO tol-based alignment: she HOLDS toward the
            # drop (right) until she reaches the rightmost edge and falls, then sinks straight to the
            # bottom (re-holding off any ledge). Going all the way right can't stop her short of the
            # open drop column the way a tol'd swim_to could. The hold IS the alignment + the drop.
            if reset_node:
                core.STATUS["node"] = reset_node
            bottom_y = centers[farm_nodes[0]][1]                   # P6 y (land here before looping)
            _near = int(map_cfg.get("sink_near", 35))              # buffer: landing a bit high = arrived
            _dir = map_cfg.get("reset_nudge", "right")
            _key = {"right": core.Key.right, "left": core.Key.left}.get(_dir)
            print(f"[water] reset -> hold {_dir} to bottom (y within {_near} of {bottom_y})")
            if _key is not None:
                core.hold_to_bottom(bottom_y, _key, cap=15.0, near=_near)   # WALK to the drop, arrive by y
            else:                                                       # reset_nudge:none -> straight sink
                core.sink_to_bottom(bottom_y, cap=15.0, near=_near)
            # next sweep's farm_node(farm_nodes[0]) swims to it -> no separate re-center needed
    else:
        current = farm_nodes[0]
        while True:
            g = guard()
            if g == "stop":
                core.stop_safety_monitor(); return
            if g != "ok":
                continue
            take_break_if_due()
            if farm_node(current):
                current = core.watermap.next_farm(current, farm_nodes)
