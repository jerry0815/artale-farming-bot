"""Tests for the recovery status surface + watch loop used by the control UI."""
import numpy as np
import recovery


def test_full_tick_dumps_near_miss_and_throttles(monkeypatch):
    frame = np.zeros((10, 10, 3), np.uint8)
    monkeypatch.setattr(recovery, "capture", lambda: frame)
    monkeypatch.setattr(recovery, "_lie_enabled", True)
    monkeypatch.setattr(recovery, "is_lie_check_active", lambda: False)

    def fake_detect(f, scores=None, **k):
        if scores is not None:
            scores["monster_instr_box.png"] = 0.75      # in [floor 0.72, threshold) -> near-miss
        return []                                        # ...but no hit
    monkeypatch.setattr(recovery, "detect_lie_check", fake_detect)
    dumps = []
    monkeypatch.setattr(recovery, "dump_frame", lambda reason, f=None, tag="": dumps.append(reason))
    monkeypatch.setattr(recovery.time, "time", lambda: 100000.0)

    recovery._last_full_tick[0] = 0.0
    recovery._last_near_miss_dump[0] = 0.0
    recovery.lie_check_full_tick()
    assert dumps == ["near_miss"], dumps          # captured the sub-threshold frame

    recovery._last_full_tick[0] = 0.0             # allow the tick; near-miss throttle must block
    recovery.lie_check_full_tick()
    assert dumps == ["near_miss"], dumps          # still one -- throttled within the interval


def test_is_lie_check_active_reads_alarms(monkeypatch):
    monkeypatch.setattr(recovery._fast_alert.alarm, "_thread", None)
    monkeypatch.setattr(recovery._full_alert.alarm, "_thread", None)
    assert recovery.is_lie_check_active() is False


def test_watch_loop_stops_on_event(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(recovery, "lie_check_tick", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(recovery, "lie_check_silence", lambda: None)
    monkeypatch.setattr(recovery, "is_lie_check_active", lambda: False)

    # Stop after the first tick: the sleep hook trips STOP so the loop exits promptly.
    def stop_soon(_s):
        recovery.STOP.set()

    recovery.watch_loop(sleep=stop_soon)
    recovery.STOP.clear()
    assert calls["n"] == 1
    assert recovery.STATUS["state"] == "idle"
