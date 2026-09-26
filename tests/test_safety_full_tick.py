"""The background curse/monster tick: YOLO every tick, slow picture templates only every
full_template_secs (they cost ~1.5 s of CPU per tick; the YOLO check is ~16 ms)."""
import numpy as np

import core


def test_full_tick_runs_templates_rarely_and_yolo_every_tick(monkeypatch):
    now = [0.0]
    calls = {"tpl": 0, "yolo": 0}
    monkeypatch.setattr(core, "_lie_enabled", True)
    monkeypatch.setattr(core.time, "time", lambda: now[0])
    monkeypatch.setattr(core, "capture", lambda: np.zeros((10, 10, 3), np.uint8))

    def tpl(*a, **k):
        calls["tpl"] += 1
        return []

    def yolo(*a, **k):
        calls["yolo"] += 1
        return []
    monkeypatch.setattr(core, "detect_lie_check", tpl)
    monkeypatch.setattr(core, "detect_lie_check_yolo", yolo)
    core._last_full_tick[0] = -100.0
    core._last_full_tpl[0] = -100.0
    for t in range(0, 31, 2):                      # a tick every 2 s for 30 s
        now[0] = float(t)
        core.lie_check_full_tick(interval=1.5, template_every=15.0)
    assert calls["yolo"] == 16                     # every tick
    assert calls["tpl"] == 2                       # t = 0 and 16 (>= 15 s apart)
