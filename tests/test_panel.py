"""Tests for the panel.Controller single-worker state machine (no server/game)."""
import panel


class _Rec:
    def __init__(self, log):
        self.log = log
        self.marks = 0

    def mark(self, n):
        self.marks += 1
        self.log.append(("mark", n))
        return n or f"N{self.marks}"

    def stop(self):
        self.log.append("recstop")
        return "out.jsonl"


def _make():
    log = []
    c = panel.Controller({
        "farm": lambda: log.append("farm"),
        "watch": lambda: log.append("watch"),
        "recover": lambda: log.append("recover"),
        "make_recorder": lambda mp: log.append(("rec", mp)) or _Rec(log),
        "pause_toggle": lambda: log.append("pause"),
        "stop": lambda: log.append("stop"),
    })
    return log, c


def test_single_worker_enforced():
    log, c = _make()
    assert c.start("farm")[0] is True
    assert c.start("watch")[0] is False        # busy
    assert c.stop()[0] is True
    assert c.start("watch")[0] is True
    assert "farm" in log and "watch" in log and "stop" in log


def test_unknown_mode_rejected():
    _, c = _make()
    assert c.start("fly")[0] is False
    assert c.mode == "idle"


def test_record_flow():
    log, c = _make()
    assert c.record_start("blue")[0] is True
    assert c.mode == "recording"
    assert c.record_mark("TOP")[0] is True
    ok, out = c.record_stop()
    assert ok is True and out == "out.jsonl"
    assert c.mode == "idle"
    assert ("rec", "blue") in log and ("mark", "TOP") in log and "recstop" in log


def test_record_requires_idle_and_active():
    log, c = _make()
    assert c.record_mark("X")[0] is False       # not recording
    c.start("farm")
    assert c.record_start("m")[0] is False       # busy farming


def test_stop_while_recording_stops_recorder():
    log, c = _make()
    c.record_start("m")
    ok, out = c.stop()                           # stop routes to record_stop
    assert ok is True and out == "out.jsonl"
    assert c.mode == "idle" and "recstop" in log


def test_pause_toggles():
    log, c = _make()
    assert c.pause()[0] is True
    assert log.count("pause") == 1
