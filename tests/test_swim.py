"""swim_to loop: presses arrow keys toward target and stops on arrival (no game)."""
import recovery
from pynput.keyboard import Key


def test_swim_to_presses_toward_target_and_stops(monkeypatch):
    # Scripted positions: start below-left of target (100,100), then arrive.
    seq = iter([(90, 130), (95, 115), (100, 100)])
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "enemy_fast_tick", lambda *a, **k: False)
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


def test_swim_to_times_out_when_never_arrives(monkeypatch):
    monkeypatch.setattr(recovery, "get_character_full", lambda: (0, 0))   # never reaches (100,100)
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "enemy_fast_tick", lambda *a, **k: False)
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: None)
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.5) or t["v"])
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    assert recovery.swim_to(100, 100, tol=(3, 3), cap=2.0) is False


def test_swim_to_jumps_when_ascent_stalls(monkeypatch):
    # Wants to go UP (y 130 > target 100) but never rises -> must JUMP to climb.
    monkeypatch.setattr(recovery, "get_character_full", lambda: (100, 130))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "enemy_fast_tick", lambda *a, **k: False)
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
    monkeypatch.setattr(recovery, "get_character_full", lambda: (100, 80))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "enemy_fast_tick", lambda *a, **k: False)
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
    monkeypatch.setattr(recovery, "enemy_fast_tick", lambda *a, **k: False)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    ok = recovery.swim_to(100, 100, tol=(3, 3), cap=5.0, settle=2,
                          locate=lambda: next(seq, (100, 100)))
    assert ok is True
    assert recovery.JUMP in pressed          # sank after the touch -> had to jump to re-rise


def test_sink_to_bottom_stops_at_bottom_y(monkeypatch):
    # y rises 150 -> 220; bottom_y=210 -> returns True once y crosses it, releasing keys.
    ys = iter([150, 170, 190, 210, 230])
    monkeypatch.setattr(recovery, "get_character_full", lambda: (176, next(ys, 230)))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "enemy_fast_tick", lambda *a, **k: False)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.2) or t["v"])
    released = []
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: released.append("all"))
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    assert recovery.sink_to_bottom(210, cap=5.0) is True
    assert "all" in released                     # released keys before sinking


def test_sink_to_bottom_settles_when_not_sinking(monkeypatch):
    # y stuck at 180 (< bottom 210) for several reads -> landed -> True
    monkeypatch.setattr(recovery, "get_character_full", lambda: (176, 180))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "enemy_fast_tick", lambda *a, **k: False)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.2) or t["v"])
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    assert recovery.sink_to_bottom(210, cap=5.0, settle=3) is True
