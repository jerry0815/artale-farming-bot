"""Route edge_jump zone: past until_x (where the minimap dot pins) she keeps fighting mobs on her
level (AoE only -- no push, the edge is somewhere ahead) and walks on in short edge steps until
she walks off, instead of skipping every mob on one blind walk to the edge."""
import pytest

import core
import sweep

farm_general = pytest.importorskip("farm_general")


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
