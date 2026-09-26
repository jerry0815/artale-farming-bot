"""Route edge_jump zone: past until_x (where the minimap dot pins) she keeps fighting mobs on her
level (AoE only -- no push, the edge is somewhere ahead) and walks on in short edge steps until
she walks off, instead of skipping every mob on one blind walk to the edge."""
import pytest

import core
import sweep

farm_general = pytest.importorskip("farm_general")


@pytest.fixture(autouse=True)
def _beat_reads_follow_stable_char(monkeypatch):
    """The loop reads the minimap from the beat's own frame (core.get_character_color(frame)); these
    tests fake positions through core.stable_char, so route the per-frame reads through it."""
    monkeypatch.setattr(core, "get_character_color", lambda *a, **k: core.stable_char(2))
    monkeypatch.setattr(core, "get_character_full", lambda *a, **k: core.stable_char(2))


def test_edge_zone_fights_then_steps_then_advances(monkeypatch):
    events = []
    kb = core.kb
    monkeypatch.setattr(kb, "pause", False, raising=False)
    monkeypatch.setattr(kb, "safe_press", lambda k: events.append(("press", k)))
    monkeypatch.setattr(kb, "safe_release", lambda k: None)
    monkeypatch.setattr(kb, "safe_release_all", lambda: None)
    for name, fn in {"focus": lambda: True, "set_minimap": lambda *a: None,
                     "ensure_game_focused": lambda **k: True, "lie_check_fast_tick": lambda: None,
                     "is_lie_check_active": lambda: False, "capture": lambda: object(),
                     "make_exp_tracker": lambda **k: (lambda: None),
                     "stable_char": lambda n=3: (196, 55)}.items():
        monkeypatch.setattr(core, name, fn)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())
    beats = iter([[(0.9, 520, 300, 40, 60)]])          # one mob in range on beat 1, then none
    monkeypatch.setattr(mob_detect, "yolo_detect",
                        lambda *a, **k: (next(beats, []), (0.9, 480, 290, 40, 70)))
    steps = []

    def fake_walk_to_edge(heading, keys, kb, read_xy, **k):
        steps.append(heading); events.append(("step", heading))
        if len(steps) == 2:
            core.STOP.set()
            return True                                  # walked off on the 2nd step
        return False
    monkeypatch.setattr(sweep, "walk_to_edge", fake_walk_to_edge)

    cfg = {"loop": "sweep", "anchor": "yolo_player", "attack_key": "x",
           "minimap": {"x": 0, "y": 0, "w": 10, "h": 10},
           "gather": {"key": "z", "cooldown": 1.0},
           "route": [{"dir": "right", "until_x": 195, "then": "edge_jump", "attack": True},
                     {"dir": "right", "until_x": 307, "then": None, "attack": True}]}
    core.STOP.clear()
    farm_general.farming_loop_general(cfg, max_seconds=5)
    assert len(steps) == 2
    first_step = events.index(("step", 1))
    assert ("press", "x") in events[:first_step]         # fought the in-range mob first
    assert ("press", "z") not in events                  # never pushed in the edge zone
    assert core.STATUS["node"] == "seg2"                 # walked off -> advanced


def test_failed_jump_stuck_on_bottom_resyncs_to_the_bottom_leg(monkeypatch, capsys):
    """Missed jump: route is on segment 5 (upper-right, until 307) but she is on the bottom floor,
    whose right end (~285) never reaches 307. Walking with no progress -> resync to segment 6."""
    clock = [1000.0]
    kb = core.kb
    monkeypatch.setattr(kb, "pause", False, raising=False)
    monkeypatch.setattr(kb, "safe_press", lambda k: None)
    monkeypatch.setattr(kb, "safe_release", lambda k: None)
    monkeypatch.setattr(kb, "safe_release_all", lambda: None)

    def stable_char(n=3):
        clock[0] += 0.25
        return (285, 106)                                  # pinned against the bottom-right wall

    for name, fn in {"focus": lambda: True, "set_minimap": lambda *a: None,
                     "ensure_game_focused": lambda **k: True, "lie_check_fast_tick": lambda: None,
                     "is_lie_check_active": lambda: False, "capture": lambda: object(),
                     "make_exp_tracker": lambda **k: (lambda: None), "stable_char": stable_char,
                     "make_active_timer": lambda: (lambda now: None, lambda now: None)}.items():
        monkeypatch.setattr(core, name, fn)
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    monkeypatch.setattr(core.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())

    def no_mobs(*a, **k):
        if core.STATUS.get("node") == "seg2":             # resynced -> done
            core.STOP.set()
        return ([], (0.9, 480, 290, 40, 70))
    monkeypatch.setattr(mob_detect, "yolo_detect", no_mobs)

    # flat_sweep's route rotated so the loop STARTS on segment 5 (route index 0 here = real seg 5)
    route = [{"dir": "right", "until_x": 307, "then": None, "attack": True, "y": 62},
             {"dir": "left", "until_x": 214, "then": "up", "attack": True, "y": 106},
             {"dir": "left", "until_x": 45, "then": None, "attack": False, "y": 55},
             {"dir": "right", "until_x": 275, "then": None, "attack": True, "y": 106},
             {"dir": "left", "until_x": 216, "then": "up", "attack": False, "y": 106},
             {"dir": "left", "until_x": 120, "then": None, "attack": False, "y": 55},
             {"dir": "right", "until_x": 195, "then": "edge_jump", "attack": True, "y": 55}]
    cfg = {"loop": "sweep", "anchor": "yolo_player", "attack_key": "x",
           "minimap": {"x": 0, "y": 0, "w": 10, "h": 10}, "route": route}
    core.STOP.clear(); core.STATUS["node"] = None
    farm_general.farming_loop_general(cfg, max_seconds=60)
    out = capsys.readouterr().out
    # real segment 5 stuck at the bottom wall -> real segment 6 (left to the bottom portal)
    assert "segment 1 stuck at x=285 y=106 -> resync to segment 2" in out
    assert core.STATUS["node"] == "seg2"


def test_segment_end_finishes_the_mob_in_range_before_moving_on(monkeypatch, capsys):
    events = []
    kb = core.kb
    monkeypatch.setattr(kb, "pause", False, raising=False)
    monkeypatch.setattr(kb, "safe_press", lambda k: events.append(("press", k)))
    monkeypatch.setattr(kb, "safe_release", lambda k: None)
    monkeypatch.setattr(kb, "safe_release_all", lambda: events.append(("release_all", None)))
    for name, fn in {"focus": lambda: True, "set_minimap": lambda *a: None,
                     "ensure_game_focused": lambda **k: True, "lie_check_fast_tick": lambda: None,
                     "is_lie_check_active": lambda: False, "capture": lambda: object(),
                     "make_exp_tracker": lambda **k: (lambda: None),
                     "stable_char": lambda n=3: (280, 106)}.items():   # already past until_x 275
        monkeypatch.setattr(core, name, fn)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())
    beats = iter([[(0.9, 530, 300, 40, 70)]])           # a mob in AoE range on beat 1, then none

    def detect(*a, **k):
        if core.STATUS.get("node") == "seg2":
            core.STOP.set()
        return (next(beats, []), (0.9, 480, 300, 40, 70))
    monkeypatch.setattr(mob_detect, "yolo_detect", detect)
    cfg = {"loop": "sweep", "anchor": "yolo_player", "attack_key": "x",
           "minimap": {"x": 0, "y": 0, "w": 10, "h": 10},
           "route": [{"dir": "right", "until_x": 275, "then": None, "attack": True, "y": 106},
                     {"dir": "left", "until_x": 215, "then": None, "attack": True, "y": 106}]}
    core.STOP.clear(); core.STATUS["node"] = None
    farm_general.farming_loop_general(cfg, max_seconds=5)
    out = capsys.readouterr().out
    assert "segment 1 end" in out and core.STATUS["node"] == "seg2"
    first_x = events.index(("press", "x"))
    assert first_x < len(events)                          # it AoE'd the mob at the segment end...
    assert "finishing" in out                             # ...before the transition


def test_push_aoe_is_fixed_but_aoe_only_holds_until_clear(monkeypatch):
    """AoE-until-clear is for AoE-only (FIRE); the AoE after a push stays the fixed 2 casts."""
    import sweep as sw
    calls = []
    real = sw.apply_action

    def spy(action, heading, keys, kb, **k):
        calls.append((action, k.get("keep_holding") is not None))
        return heading
    monkeypatch.setattr(sw, "apply_action", spy)
    kb = core.kb
    monkeypatch.setattr(kb, "pause", False, raising=False)
    monkeypatch.setattr(kb, "safe_press", lambda k: None)
    monkeypatch.setattr(kb, "safe_release", lambda k: None)
    monkeypatch.setattr(kb, "safe_release_all", lambda: None)
    for name, fn in {"focus": lambda: True, "set_minimap": lambda *a: None,
                     "ensure_game_focused": lambda **k: True, "lie_check_fast_tick": lambda: None,
                     "is_lie_check_active": lambda: False, "capture": lambda: object(),
                     "make_exp_tracker": lambda **k: (lambda: None),
                     "stable_char": lambda n=3: (100, 106)}.items():
        monkeypatch.setattr(core, name, fn)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())
    n = [0]

    def detect(*a, **k):
        n[0] += 1
        if n[0] > 2:
            core.STOP.set()
        return ([(0.9, 530, 300, 40, 70)], (0.9, 480, 300, 40, 70))   # a mob in range
    monkeypatch.setattr(mob_detect, "yolo_detect", detect)
    # beat 1: far from the end -> GATHER (push); beat 2: near the end -> FIRE (AoE-only)
    # (a realistic step: the phantom-read gate drops > 40 px jumps)
    pos = iter([(240, 106), (262, 106)])
    monkeypatch.setattr(core, "stable_char", lambda n=3: next(pos, (262, 106)))
    cfg = {"loop": "sweep", "anchor": "yolo_player", "attack_key": "x",
           "minimap": {"x": 0, "y": 0, "w": 10, "h": 10}, "gather": {"key": "z", "cooldown": 0},
           "route": [{"dir": "right", "until_x": 275, "then": None, "attack": True, "y": 106},
                     {"dir": "left", "until_x": 215, "then": None, "attack": True, "y": 106}]}
    core.STOP.clear()
    farm_general.farming_loop_general(cfg, max_seconds=5)
    assert (sw.GATHER, False) in calls                   # push + fixed AoE
    assert (sw.FIRE, True) in calls                      # AoE-only holds until clear


import pytest as _pt


@_pt.mark.parametrize("after_jump, expect", [
    ([(200, 58), (205, 62)], "landed"),                      # arc, then the right platform
    ([(200, 80), (205, 106)], "jump fell short"),            # dropped to the bottom floor
])
def test_after_the_jump_regular_beats_with_a_landing_check(monkeypatch, capsys, after_jump, expect):
    import sweep as sw
    kb = core.kb
    monkeypatch.setattr(kb, "pause", False, raising=False)
    monkeypatch.setattr(kb, "safe_press", lambda k: None)
    monkeypatch.setattr(kb, "safe_release", lambda k: None)
    monkeypatch.setattr(kb, "safe_release_all", lambda: None)
    clock = [1000.0]
    reads = iter([(196, 55)] + after_jump)
    def stable_char(n=3):
        clock[0] += 0.3
        return next(reads, after_jump[-1])
    for name, fn in {"focus": lambda: True, "set_minimap": lambda *a: None,
                     "ensure_game_focused": lambda **k: True, "lie_check_fast_tick": lambda: None,
                     "is_lie_check_active": lambda: False, "capture": lambda: object(),
                     "make_exp_tracker": lambda **k: (lambda: None), "stable_char": stable_char,
                     "make_active_timer": lambda: (lambda now: None, lambda now: None)}.items():
        monkeypatch.setattr(core, name, fn)
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    monkeypatch.setattr(core.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())
    n = [0]
    def detect(*a, **k):
        n[0] += 1
        if n[0] > 3:
            core.STOP.set()
        return ([], (0.9, 1000, 300, 40, 70))
    monkeypatch.setattr(mob_detect, "yolo_detect", detect)
    monkeypatch.setattr(sw, "walk_to_edge", lambda *a, **k: True)     # jumps on the first step
    route = [{"dir": "right", "until_x": 195, "then": "edge_jump", "attack": True, "y": 55},
             {"dir": "right", "until_x": 307, "then": None, "attack": True, "y": 62},
             {"dir": "right", "until_x": 275, "then": None, "attack": True, "y": 106}]
    cfg = {"loop": "sweep", "anchor": "yolo_player", "attack_key": "x",
           "minimap": {"x": 0, "y": 0, "w": 10, "h": 10}, "route": route}
    core.STOP.clear()
    farm_general.farming_loop_general(cfg, max_seconds=30)
    out = capsys.readouterr().out
    assert "slope bottom -> jump" in out and expect in out


def test_enemy_check_runs_at_most_once_per_second(monkeypatch):
    """~65 ms per call; it ran every beat. Now throttled to enemy_check_secs (1.0)."""
    clock = [1000.0]
    kb = core.kb
    monkeypatch.setattr(kb, "pause", False, raising=False)
    monkeypatch.setattr(kb, "safe_press", lambda k: None)
    monkeypatch.setattr(kb, "safe_release", lambda k: None)
    monkeypatch.setattr(kb, "safe_release_all", lambda: None)
    for name, fn in {"focus": lambda: True, "set_minimap": lambda *a: None,
                     "ensure_game_focused": lambda **k: True, "lie_check_fast_tick": lambda: None,
                     "is_lie_check_active": lambda: False, "capture": lambda: object(),
                     "make_exp_tracker": lambda **k: (lambda: None),
                     "stable_char": lambda n=3: (100, 106),
                     "start_safety_monitor": lambda *a, **k: None,
                     "make_active_timer": lambda: (lambda now: None, lambda now: None)}.items():
        monkeypatch.setattr(core, name, fn, raising=False)
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    monkeypatch.setattr(core.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())

    def detect(*a, **k):
        clock[0] += 0.1                                      # a beat takes ~0.1 s now
        return ([], (0.9, 480, 300, 40, 70))
    monkeypatch.setattr(mob_detect, "yolo_detect", detect)
    calls = []
    cfg = {"loop": "sweep", "anchor": "yolo_player", "attack_key": "x",
           "minimap": {"x": 0, "y": 0, "w": 10, "h": 10},
           "route": [{"dir": "right", "until_x": 275, "then": None, "attack": True, "y": 106}]}
    core.STOP.clear()
    farm_general.farming_loop_general(cfg, enemy_check=lambda: calls.append(1) or False,
                                      max_seconds=3)
    assert 2 <= len(calls) <= 4                                # ~3 s of beats -> ~3 checks, not ~20+


def test_a_single_mid_air_read_does_not_count_as_landed(monkeypatch, capsys):
    """Live 2026-09-26: 'landed (y=59) 0.57s after the jump' from one read taken while she passed the
    platform height mid-arc; the jump actually failed, she fell to the bottom, and the route
    stayed on the platform segment. Landing now needs two in-range reads in a row."""
    import sweep as sw
    kb = core.kb
    monkeypatch.setattr(kb, "pause", False, raising=False)
    for k in ("safe_press", "safe_release"):
        monkeypatch.setattr(kb, k, lambda key: None)
    monkeypatch.setattr(kb, "safe_release_all", lambda: None)
    clock = [1000.0]
    reads = iter([(196, 55), (200, 50), (203, 59), (206, 70), (208, 90), (210, 106)])
    def stable_char(n=3):
        clock[0] += 0.3
        return next(reads, (210, 106))
    for name, fn in {"focus": lambda: True, "set_minimap": lambda *a: None,
                     "ensure_game_focused": lambda **k: True, "lie_check_fast_tick": lambda: None,
                     "is_lie_check_active": lambda: False, "capture": lambda: object(),
                     "make_exp_tracker": lambda **k: (lambda: None), "stable_char": stable_char,
                     "start_safety_monitor": lambda *a, **k: None,
                     "make_active_timer": lambda: (lambda now: None, lambda now: None)}.items():
        monkeypatch.setattr(core, name, fn, raising=False)
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    monkeypatch.setattr(core.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())
    n = [0]
    def detect(*a, **k):
        n[0] += 1
        if n[0] > 3:
            core.STOP.set()
        return ([], (0.9, 1000, 300, 40, 70))
    monkeypatch.setattr(mob_detect, "yolo_detect", detect)
    monkeypatch.setattr(sw, "walk_to_edge", lambda *a, **k: True)
    route = [{"dir": "right", "until_x": 195, "then": "edge_jump", "attack": True, "y": 55},
             {"dir": "right", "until_x": 320, "then": None, "attack": True, "y": 62, "until_y": 102},
             {"dir": "left", "until_x": 215, "then": "up", "attack": True, "y": 106}]
    cfg = {"loop": "sweep", "anchor": "yolo_player", "attack_key": "x",
           "minimap": {"x": 0, "y": 0, "w": 10, "h": 10}, "route": route}
    core.STOP.clear()
    farm_general.farming_loop_general(cfg, max_seconds=30)
    out = capsys.readouterr().out
    # no false landing; the miss drops her on the bottom, which is where the platform leg ends
    # (until_y) -> straight on to the bottom leg (the user's "should be segment 5")
    assert "landed (" not in out and "jump fell short -- already at segment 2's end" in out, out
    assert "-> segment 3: left to x=215" in out, out


def _wait_mode_harness(monkeypatch, seen_until):
    """Loop with other_player 'wait': enemy_check reports a player until clock >= seen_until."""
    clock = [1000.0]
    ev = []
    kb = core.kb
    monkeypatch.setattr(kb, "pause", False, raising=False)
    monkeypatch.setattr(kb, "safe_press", lambda k: None)
    monkeypatch.setattr(kb, "safe_release", lambda k: None)
    monkeypatch.setattr(kb, "safe_release_all", lambda: ev.append("release_all"))
    for name, fn in {"focus": lambda: True, "set_minimap": lambda *a: None,
                     "ensure_game_focused": lambda **k: True, "lie_check_fast_tick": lambda: None,
                     "is_lie_check_active": lambda: False, "capture": lambda: object(),
                     "make_exp_tracker": lambda **k: (lambda: None),
                     "stable_char": lambda n=3: (100, 106),
                     "start_safety_monitor": lambda *a, **k: None,
                     "enemy_alarm_on": lambda: ev.append("ALARM"),
                     "click_free_market": lambda *a, **k: ev.append("free_market"),
                     "make_active_timer": lambda: (lambda now: None, lambda now: None)}.items():
        monkeypatch.setattr(core, name, fn, raising=False)
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    monkeypatch.setattr(core.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())
    def detect(*a, **k):
        clock[0] += 0.1
        ev.append("beat")
        return ([], (0.9, 480, 300, 40, 70))
    monkeypatch.setattr(mob_detect, "yolo_detect", detect)
    cfg = {"loop": "sweep", "anchor": "yolo_player", "attack_key": "x",
           "minimap": {"x": 0, "y": 0, "w": 10, "h": 10},
           "other_player": {"action": "wait", "wait_secs": 20},
           "route": [{"dir": "right", "until_x": 275, "then": None, "attack": True, "y": 106}]}
    core.STOP.clear()
    farm_general.farming_loop_general(cfg, enemy_check=lambda: clock[0] < seen_until,
                                      panic=lambda: ev.append("PANIC"), max_seconds=40)
    return ev


def test_other_player_leaves_within_the_wait_resumes_without_alarm(monkeypatch, capsys):
    ev = _wait_mode_harness(monkeypatch, seen_until=1008.0)       # gone after ~8 s
    out = capsys.readouterr().out
    assert "ALARM" not in ev and "PANIC" not in ev and "free_market" not in ev
    assert "stop and wait" in out and "other player left after" in out
    assert ev.count("beat") > 50                                   # farming resumed afterwards


def test_other_player_stays_20s_goes_to_free_market(monkeypatch, capsys):
    ev = _wait_mode_harness(monkeypatch, seen_until=10 ** 9)       # never leaves
    assert "ALARM" not in ev and "PANIC" not in ev
    assert ev.count("free_market") == 1
    assert ev.count("beat") <= 2                                   # no farming while waiting
