"""swim_to loop: presses arrow keys toward target and stops on arrival (no game)."""
import recovery
from pynput.keyboard import Key


def test_swim_to_presses_toward_target_and_stops(monkeypatch):
    # Scripted positions: start below-left of target (100,100), then arrive.
    seq = iter([(90, 130), (95, 115), (100, 100)])
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    pressed, released = [], []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: released.append(k))
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)

    # inject locate directly (bypass stable_char's multi-read) with a scripted sequence
    ok = recovery.swim_to(100, 100, tol=(3, 3), cap=5.0, locate=lambda: next(seq, (100, 100)))
    assert ok is True
    # First step: below target (y 130 > 100) -> JUMP to rise (NOT Up arrow); left of target
    # (x 90 < 100) -> right held.
    assert Key.right in pressed and recovery.JUMP in pressed
    assert Key.up not in pressed                  # water-world: never hold Up
    # On exit every arrow key is released
    for k in recovery._ARROW.values():
        assert k in released


def test_swim_to_x_axis_double_confirms_arrival(monkeypatch):
    # x-only reset (P1 -> rightmost column): a SINGLE in-tol read must not count as arrival --
    # that stopped her short of the rightmost column and she sank on the wrong platform (status
    # flip). settle=2 requires two consecutive in-tol reads before returning.
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: None)
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    calls = {"n": 0}

    def loc():
        calls["n"] += 1
        return (100, 50)                                   # always in tol of target x=100

    ok = recovery.swim_to(100, 50, tol=(3, 3), cap=5.0, axis="x", settle=2, locate=loc)
    assert ok is True
    assert calls["n"] >= 2                                  # double-confirmed, not single-read arrival


def test_swim_to_x_axis_confirm_resets_when_drifting(monkeypatch):
    # If she drifts back off target x between confirms, the streak resets -> she must re-align and
    # only arrives once genuinely settled in tol for `settle` consecutive reads.
    seq = iter([(100, 50), (90, 50), (100, 50), (100, 50)])   # in, drift off, in, in -> arrive
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    ok = recovery.swim_to(100, 50, tol=(3, 3), cap=5.0, axis="x", settle=2,
                          locate=lambda: next(seq, (100, 50)))
    assert ok is True
    assert Key.right in pressed                             # re-aligned right after drifting to x=90


def test_swim_to_start_near_rejects_first_read_phantom(monkeypatch):
    # P5->P4: her real dot is briefly absent (mid fall/transition) so the only blob is a fixed
    # phantom (54,347) ~180px away. Seeding start_near to her departure pos makes swim_read reject
    # it (> max-jump) -> no valid read -> she WAITS and times out, instead of locking onto it and
    # flying off-map. Uses the default swim_read (not an injected locate) to exercise the reject.
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (54, 347))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.3) or t["v"])
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    ok = recovery.swim_to(74, 128, tol=(3, 3), cap=2.0, start_near=(92, 166))
    assert ok is False                                 # never a plausible read -> timed out
    assert recovery.JUMP not in pressed                # did NOT chase the phantom (no jumps toward it)


def test_swim_to_times_out_when_never_arrives(monkeypatch):
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (0, 0))   # never reaches (100,100)
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: None)
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.5) or t["v"])
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    assert recovery.swim_to(100, 100, tol=(3, 3), cap=2.0) is False


def test_swim_to_jumps_when_ascent_stalls(monkeypatch):
    # Wants to go UP (y 130 > target 100) but never rises -> must JUMP to climb.
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (100, 130))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.2) or t["v"])
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    recovery.swim_to(100, 100, tol=(3, 3), cap=3.0, jump_burst=3)
    assert recovery.JUMP in pressed          # jump-swims up (no Up arrow)
    assert Key.up not in pressed


def test_swim_to_no_jump_when_descending(monkeypatch):
    # Going DOWN (y 80 < target 130) must NOT jump.
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (100, 80))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.2) or t["v"])
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    recovery.swim_to(100, 130, tol=(3, 3), cap=3.0)
    assert recovery.JUMP not in pressed
    assert Key.down not in pressed           # descend = just sink, never hold Down


def test_swim_to_requires_settle_not_momentary_touch(monkeypatch):
    # She hits the target y once (mid jump-arc), sinks back out, then finally seats. With
    # settle=2 the momentary touch must NOT count as arrival -> she re-rises (JUMP) and only
    # returns once she has rested in-band `settle` reads. Guards the "actually land" fix.
    seq = iter([(100, 100), (100, 120), (100, 100), (100, 100)])   # touch, sink, rest, rest
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    ok = recovery.swim_to(100, 100, tol=(3, 3), cap=5.0, settle=2,
                          locate=lambda: next(seq, (100, 100)))
    assert ok is True
    assert recovery.JUMP in pressed          # sank after the touch -> had to jump to re-rise


def _seq_reader(monkeypatch, vals):
    it = iter(vals)
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: next(it))
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)


def test_swim_read_agreeing_samples_return_midpoint(monkeypatch):
    _seq_reader(monkeypatch, [(100, 200), (104, 206)])   # within 12px -> average
    assert recovery.swim_read() == (102, 203)


def test_swim_read_disagreeing_samples_return_latest(monkeypatch):
    _seq_reader(monkeypatch, [(100, 200), (400, 500)])   # phantom jump -> trust freshest
    assert recovery.swim_read() == (400, 500)


def test_swim_read_skips_invalid_sample(monkeypatch):
    _seq_reader(monkeypatch, [(-1, -1), (150, 160)])     # first invalid -> return the valid one
    assert recovery.swim_read() == (150, 160)
    _seq_reader(monkeypatch, [(150, 160), (-1, -1)])     # second invalid -> return the first
    assert recovery.swim_read() == (150, 160)


def test_swim_read_rejects_far_phantom_when_near_given(monkeypatch):
    # The P2->P1 runaway: a spurious bottom-of-minimap blob (~300px from her real dot). With
    # `near` set, both far samples are dropped -> invalid -> swim_to waits instead of chasing it.
    _seq_reader(monkeypatch, [(54, 347), (42, 377)])
    assert recovery.swim_read(near=(100, 55)) == (-1, -1)


def test_swim_read_keeps_real_read_when_near_given(monkeypatch):
    # A genuine read close to the last position is kept (phantom reject must not block real motion).
    _seq_reader(monkeypatch, [(102, 58), (104, 60)])
    assert recovery.swim_read(near=(100, 55)) == (103, 59)


def _water_shoot_env(monkeypatch, read):
    calls = {"swim": 0}
    monkeypatch.setattr(recovery, "swim_to",
                        lambda *a, **k: (calls.__setitem__("swim", calls["swim"] + 1), True)[1])
    monkeypatch.setattr(recovery, "_face_right", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "get_character_color", lambda *a, **k: read)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: None)
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.3) or t["v"])
    return calls


def test_water_shoot_ignores_phantom_no_false_swimback(monkeypatch):
    # A phantom blob ~300px from the parked center must NOT fire a "drifted off -> swim back":
    # only the initial swim_to happens, none from the beat loop.
    calls = _water_shoot_env(monkeypatch, (400, 400))     # center (100,100) -> jump 300 > max
    recovery.water_shoot((100, 100), seconds=2.0, tol=(3, 3))
    assert calls["swim"] == 1                              # initial only; phantom triggered no swim-back


def test_water_shoot_swims_back_on_real_drift(monkeypatch):
    # A plausible read genuinely off center (30px, within max-jump) DOES swim her back.
    calls = _water_shoot_env(monkeypatch, (130, 100))     # 30px drift: plausible AND > tol+8
    recovery.water_shoot((100, 100), seconds=2.0, tol=(3, 3))
    assert calls["swim"] > 1                               # re-centered at least once during the beat


def test_sink_to_bottom_stops_at_bottom_y(monkeypatch):
    # y rises 150 -> 220; bottom_y=210 -> returns True once y crosses it, releasing keys.
    ys = iter([150, 170, 190, 210, 230])
    monkeypatch.setattr(recovery, "get_character_full", lambda: (176, next(ys, 230)))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.2) or t["v"])
    released = []
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: released.append("all"))
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    assert recovery.sink_to_bottom(210, cap=5.0) is True
    assert "all" in released                     # released keys before sinking


def test_hold_to_bottom_holds_continuously_and_arrives_by_y(monkeypatch):
    # Reset: HOLD right (pressed ONCE, no stutter) and conclude arrival purely by reaching the
    # bottom platform's y (settle consecutive in-band reads), not a sink heuristic.
    ys = iter([104, 150, 200, 213, 213, 213])            # walk/fall, then land at bottom y=213
    monkeypatch.setattr(recovery, "get_character_full", lambda: (150, next(ys, 213)))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.3) or t["v"])
    pressed, released = [], []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: released.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    ok = recovery.hold_to_bottom(213, Key.right, cap=5.0, near=35, settle=3)
    assert ok is True
    assert pressed.count(Key.right) == 1                 # held CONTINUOUSLY (no stutter re-press)
    assert Key.right in released                         # released on exit


def test_hold_to_bottom_ignores_far_phantom(monkeypatch):
    # A far phantom (y=347) is well below the bottom platform (213) -> outside the near band ->
    # must NOT be read as 'arrived'. Only the real bottom reads (213) count.
    ys = iter([347, 347, 347, 213, 213, 213])            # 3 phantoms then 3 real bottom reads
    calls = {"n": 0}

    def loc():
        calls["n"] += 1
        return (54, next(ys, 213))

    monkeypatch.setattr(recovery, "get_character_full", loc)
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.3) or t["v"])
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: None)
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    ok = recovery.hold_to_bottom(213, Key.right, cap=5.0, near=35, settle=3)
    assert ok is True
    assert calls["n"] == 6                               # phantoms NOT counted -> arrived only at read 6


def test_sink_to_bottom_settles_when_not_sinking(monkeypatch):
    # y stuck at 180 (< bottom 210) for several reads -> landed -> True
    monkeypatch.setattr(recovery, "get_character_full", lambda: (176, 180))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.2) or t["v"])
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    assert recovery.sink_to_bottom(210, cap=5.0, settle=3) is True
