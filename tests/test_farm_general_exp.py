"""The general sweep loop must drive the EXP tracker every beat, like the water/nav loops --
otherwise the panel's EXP/min, 10-min gain and total stay blank on sweep maps."""
import pytest

import core

farm_general = pytest.importorskip("farm_general")


def test_general_loop_ticks_the_exp_tracker(monkeypatch):
    ticks = []

    def fake_tracker(**kw):
        def tick():
            ticks.append(kw)
            core.STOP.set()                    # one beat is enough -> next guard() stops the loop
        return tick

    monkeypatch.setattr(core, "make_exp_tracker", fake_tracker)
    monkeypatch.setattr(core, "focus", lambda: True)
    monkeypatch.setattr(core, "set_minimap", lambda *a: None)
    monkeypatch.setattr(core, "ensure_game_focused", lambda **k: True)
    monkeypatch.setattr(core, "lie_check_fast_tick", lambda: None)
    monkeypatch.setattr(core, "is_lie_check_active", lambda: False)
    monkeypatch.setattr(core, "capture", lambda: None)
    monkeypatch.setattr(core.kb, "pause", False, raising=False)
    monkeypatch.setattr(core.kb, "safe_release_all", lambda: None)
    import mob_detect
    monkeypatch.setattr(mob_detect, "load_yolo", lambda p: object())

    cfg = {"loop": "sweep", "minimap": {"x": 0, "y": 0, "w": 10, "h": 10}, "exp_sample_secs": 7}
    farm_general.farming_loop_general(cfg, max_seconds=2)   # hang guard
    assert ticks and ticks[0]["sample_secs"] == 7.0
