"""exp_record_loop: passive EXP recorder that logs a session on stop (no game)."""
import recovery


def test_exp_record_loop_appends_session_on_stop(monkeypatch):
    def fake_tracker(*a, **k):
        def tick():
            recovery.STATUS["exp_total"] = 500
            recovery.STATUS["exp_10min"] = 500
        return tick
    monkeypatch.setattr(recovery, "make_exp_tracker", fake_tracker)
    monkeypatch.setattr(recovery, "lie_check_tick", lambda: None)
    monkeypatch.setattr(recovery, "is_lie_check_active", lambda: False)
    monkeypatch.setattr(recovery, "lie_check_silence", lambda: None)
    t = {"v": 1000.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t["v"])
    saved = []
    monkeypatch.setattr(recovery, "append_exp_session", lambda row, **k: saved.append(row))

    def fake_sleep(s):
        t["v"] += 60                       # 60s session
        recovery.STOP.set()                # stop after one iteration
    recovery.exp_record_loop(label="human", sleep=fake_sleep)

    assert len(saved) == 1
    row = saved[0]
    assert row["label"] == "human"
    assert row["exp_gained"] == 500
    assert row["duration_s"] == 60
    assert row["exp_per_min"] == 500       # 500 gained over 1 minute
    assert recovery.STATUS["state"] == "idle"


def test_exp_record_loop_null_gain_when_no_exp(monkeypatch):
    monkeypatch.setattr(recovery, "make_exp_tracker", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(recovery, "lie_check_tick", lambda: None)
    monkeypatch.setattr(recovery, "is_lie_check_active", lambda: False)
    monkeypatch.setattr(recovery, "lie_check_silence", lambda: None)
    recovery.STATUS["exp_total"] = 0
    t = {"v": 5.0}
    monkeypatch.setattr(recovery.time, "time", lambda: t["v"])
    saved = []
    monkeypatch.setattr(recovery, "append_exp_session", lambda row, **k: saved.append(row))
    monkeypatch.setattr(recovery.STOP, "is_set", lambda: True)   # exit immediately
    recovery.exp_record_loop(label="bot", sleep=lambda s: None)
    assert saved[0]["exp_gained"] is None and saved[0]["exp_per_min"] is None
