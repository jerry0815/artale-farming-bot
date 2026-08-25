"""Tests for the recovery status surface + watch loop used by the control UI."""
import recovery


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
