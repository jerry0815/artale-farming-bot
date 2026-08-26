"""swim_to loop: presses arrow keys toward target and stops on arrival (no game)."""
import recovery
from pynput.keyboard import Key


def test_swim_to_presses_toward_target_and_stops(monkeypatch):
    # Scripted positions: start below-left of target (100,100), then arrive.
    seq = iter([(90, 130), (95, 115), (100, 100)])
    monkeypatch.setattr(recovery, "get_character_full", lambda: next(seq, (100, 100)))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    pressed, released = [], []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: released.append(k))
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)

    ok = recovery.swim_to(100, 100, tol=(3, 3), cap=5.0)
    assert ok is True
    # First step: below target (y 130 > 100) and left (x 90 < 100) -> up + right held
    assert Key.up in pressed and Key.right in pressed
    # On exit every arrow key is released
    for k in recovery._ARROW.values():
        assert k in released


def test_swim_to_times_out_when_never_arrives(monkeypatch):
    monkeypatch.setattr(recovery, "get_character_full", lambda: (0, 0))   # never reaches (100,100)
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
    monkeypatch.setattr(recovery, "get_character_full", lambda: (100, 130))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    t = {"v": 0.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t.__setitem__("v", t["v"] + 0.2) or t["v"])
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    recovery.swim_to(100, 100, tol=(3, 3), cap=3.0, jump_after=0.35)
    assert recovery.JUMP in pressed          # hopped to climb the ledge


def test_swim_to_no_jump_when_descending(monkeypatch):
    # Going DOWN (y 80 < target 130) must NOT jump.
    monkeypatch.setattr(recovery, "get_character_full", lambda: (100, 80))
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
