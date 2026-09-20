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

    attack_range = int(map_cfg.get("attack_range", 200))
    aoe_max = int(map_cfg.get("aoe_max", 6))
    heading = int(map_cfg.get("start_heading", 1))
    bounds = map_cfg.get("sweep_bounds")                   # [mm_x_lo, mm_x_hi] or None
    attack_key = map_cfg.get("attack_key", "c")
    gather = map_cfg.get("gather")
    keys = {"attack": attack_key, "dash": (gather or {}).get("key", "z"),
            "right": core.Key.right, "left": core.Key.left}

    # Normalize the gather block for sweep.decide (it derives dash = attack_range - dash_gap).
    gcfg = None
    if gather:
        gcfg = {"grp": int(gather.get("grp", 90)), "min": int(gather.get("min", 3)),
                "dash_gap": int(gather.get("dash_gap", 50))}
        if gather.get("dash") is not None:
            gcfg["dash"] = int(gather["dash"])
    gather_cd = float((gather or {}).get("cooldown", 1.5))
    next_gather = [0.0]

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

    def guard():
        """Per-beat housekeeping: STOP/time-cap/pause/focus/lie-check/enemy. Mirrors farm_water."""
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
        if enemy_check and enemy_check():
            print("[sweep] another player -> ALARM + panic")
            core.enemy_alarm_on()
            if panic:
                panic()
            core.time.sleep(1); return "skip"
        return "ok"

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
            mobs, pbox = mob_detect.yolo_detect(_mm, frame, conf=_mconf, imgsz=_mimg,
                                                class_conf=_cconf, near=last_px[0], max_jump=_pmax,
                                                player_conf=_pconf)
            if pbox is not None:
                px = pbox[1] + pbox[3] // 2; last_px[0] = px
            elif last_px[0] is not None:
                px = last_px[0]                            # ride a brief player-detection miss
            else:
                core.time.sleep(0.05); continue            # no player lock yet -> wait
            mob_xs = [m[1] + m[3] // 2 for m in mobs]       # center-x of each mob box
            core.STATUS["count"] = len(mob_xs)
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
