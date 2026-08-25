"""Tests for the pure position-sampling core of the recorder."""
import json
import itertools
import record_route


def test_record_writes_position_samples(tmp_path):
    out = tmp_path / "cap.jsonl"
    xs = itertools.chain([(63, 85), (64, 86)], itertools.repeat((64, 86)))
    ticks = iter([0.0, 0.05, 0.10, 0.20, 1.0, 1.0])
    recs = record_route.record(lambda: next(xs), str(out),
                               poll_hz=1000, is_paused=lambda: False,
                               stop_after=3,
                               clock=lambda: next(ticks, 1.0),
                               sleep=lambda s: None)
    pos = [r for r in recs if "x" in r]
    assert len(pos) == 3
    assert pos[0]["x"] == 63 and pos[0]["y"] == 85
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 3


def test_record_skips_bad_reads_and_honors_pause(tmp_path):
    out = tmp_path / "cap.jsonl"
    reads = iter([(-1, -1), (10, 20), (11, 21)])
    recs = record_route.record(lambda: next(reads, (11, 21)), str(out),
                               poll_hz=1000, is_paused=lambda: False,
                               stop_after=2, clock=lambda: 0.0, sleep=lambda s: None)
    assert [(r["x"], r["y"]) for r in recs] == [(10, 20), (11, 21)]


def test_record_stops_on_listener_flag(tmp_path):
    out = tmp_path / "cap.jsonl"

    class L:
        stopped = True

    recs = record_route.record(lambda: (1, 2), str(out), listener=L(),
                               clock=lambda: 0.0, sleep=lambda s: None)
    assert recs == []


def test_key_name_normalizes():
    class KUp:
        name = "up"

    class KChar:
        char = "a"
        name = None

    assert record_route._key_name(KUp()) == "up"
    assert record_route._key_name(KChar()) == "a"


def test_route_recorder_marks_and_positions(tmp_path, monkeypatch):
    import time
    import build_route
    monkeypatch.setattr(record_route, "OUT_DIR", str(tmp_path))
    xs = iter([(10, 20), (11, 21), (12, 22)])
    rec = record_route.RouteRecorder("m", get_xy=lambda: next(xs, (12, 22)),
                                     poll_hz=1000, use_listener=False)
    rec.start()
    name = rec.mark("TOP")
    time.sleep(0.05)
    out = rec.stop()
    assert name == "TOP" and rec.marks == 1
    recs = build_route.load_capture(out)
    assert any("mark" in r for r in recs)
    assert any("x" in r for r in recs)


def test_route_recorder_auto_names_blank_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(record_route, "OUT_DIR", str(tmp_path))
    rec = record_route.RouteRecorder("m", get_xy=lambda: (-1, -1),
                                     poll_hz=1000, use_listener=False)
    rec.start()
    try:
        assert rec.mark("") == "N1"
        assert rec.mark(None) == "N2"
    finally:
        rec.stop()
