"""Tests for the core status surface + watch loop used by the control UI."""
import numpy as np
import core


def test_full_tick_dumps_near_miss_and_throttles(monkeypatch):
    frame = np.zeros((10, 10, 3), np.uint8)
    monkeypatch.setattr(core, "capture", lambda: frame)
    monkeypatch.setattr(core, "_lie_enabled", True)
    monkeypatch.setattr(core, "is_lie_check_active", lambda: False)

    def fake_detect(f, scores=None, **k):
        if scores is not None:
            scores["monster_instr_box.png"] = 0.75      # in [floor 0.72, threshold) -> near-miss
        return []                                        # ...but no hit
    monkeypatch.setattr(core, "detect_lie_check", fake_detect)
    dumps = []
    monkeypatch.setattr(core, "dump_frame", lambda reason, f=None, tag="": dumps.append(reason))
    monkeypatch.setattr(core.time, "time", lambda: 100000.0)

    core._last_full_tick[0] = 0.0
    core._last_near_miss_dump[0] = 0.0
    core.lie_check_full_tick()
    assert dumps == ["near_miss"], dumps          # captured the sub-threshold frame

    core._last_full_tick[0] = 0.0             # allow the tick; near-miss throttle must block
    core.lie_check_full_tick()
    assert dumps == ["near_miss"], dumps          # still one -- throttled within the interval


def test_watch_loop_stops_on_event(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(core, "lie_check_tick", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(core, "lie_check_silence", lambda: None)
    monkeypatch.setattr(core, "is_lie_check_active", lambda: False)

    # Stop after the first tick: the sleep hook trips STOP so the loop exits promptly.
    def stop_soon(_s):
        core.STOP.set()

    core.watch_loop(sleep=stop_soon)
    core.STOP.clear()
    assert calls["n"] == 1
    assert core.STATUS["state"] == "idle"
