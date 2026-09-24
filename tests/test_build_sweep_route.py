"""Extract a sweep route (ordered segments) from a record-route capture."""
import os
import pytest

import build_sweep_route as bsr


def _pos(t, x, y):
    return {"t": t, "x": x, "y": y}


def _key(t, k, ev):
    return {"t": t, "key": k, "ev": ev}


def _walk(t0, t1, x0, x1, y, step=0.1):
    n = max(1, int(round((t1 - t0) / step)))
    return [_pos(round(t0 + i * step, 3), round(x0 + (x1 - x0) * i / n), y) for i in range(n + 1)]


def test_right_then_left_with_up():
    recs = (_walk(0, 5, 40, 200, 106) + [_key(0.0, "right", "down"), _key(5.0, "right", "up"),
            _key(2.0, "z", "down"), _key(2.1, "z", "up")]
            + _walk(5.2, 8, 200, 150, 106) + [_key(5.2, "left", "down"), _key(8.0, "left", "up"),
            _key(8.1, "up", "down"), _key(8.3, "up", "up")])
    segs = bsr.extract_segments(sorted(recs, key=lambda r: r["t"]), 0, 9)
    assert [s["dir"] for s in segs] == ["right", "left"]
    assert segs[0]["attack"] is True and segs[0]["then"] is None
    assert abs(segs[0]["until_x"] - 200) <= 6
    assert segs[1]["attack"] is False and segs[1]["then"] == "up"
    assert abs(segs[1]["until_x"] - 150) <= 6


def test_same_direction_runs_merge_across_a_kill_pause():
    # walk right, stop 2s to kill (no transition), walk right again -> ONE segment
    recs = (_walk(0, 3, 40, 100, 106) + _walk(5, 8, 100, 170, 106)
            + [_key(0, "right", "down"), _key(3, "right", "up"),
               _key(5, "right", "down"), _key(8, "right", "up")])
    segs = bsr.extract_segments(sorted(recs, key=lambda r: r["t"]), 0, 9)
    assert len(segs) == 1 and abs(segs[0]["until_x"] - 170) <= 6


def test_double_jump_is_one_jump_transition_on_the_segment_it_ends():
    recs = (_walk(0, 3, 100, 195, 55) + _walk(4, 7, 195, 260, 62)
            + [_key(0, "right", "down"), _key(3, "right", "up"),
               _key(4, "right", "down"), _key(4.5, "alt_l", "down"), _key(4.55, "alt_l", "up"),
               _key(4.8, "alt_l", "down"), _key(4.85, "alt_l", "up"), _key(7, "right", "up")])
    segs = bsr.extract_segments(sorted(recs, key=lambda r: r["t"]), 0, 8)
    assert [s["then"] for s in segs] == ["jump", None]
    assert abs(segs[0]["until_x"] - 195) <= 6


def test_phantom_minimap_reads_are_ignored():
    recs = (_walk(0, 3, 40, 100, 106) + [_pos(3.0, 9, 7)]       # bogus read at the end (y < 48)
            + [_key(0, "right", "down"), _key(3.0, "right", "up")])
    segs = bsr.extract_segments(sorted(recs, key=lambda r: r["t"]), 0, 4)
    assert abs(segs[0]["until_x"] - 100) <= 6


CAP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "routes", "flat_sweep.capture.jsonl")


@pytest.mark.skipif(not os.path.exists(CAP), reason="local capture (gitignored)")
def test_flat_sweep_cycle1_matches_the_confirmed_7_step_loop():
    segs = bsr.extract_segments(bsr.load(CAP), 0.7, 81.6)
    assert [(s["dir"], s["then"]) for s in segs] == [
        ("right", None), ("left", "up"), ("left", None), ("right", "jump"),
        ("right", None), ("left", "up"), ("left", None)]
    assert [s["attack"] for s in segs] == [True, False, False, True, True, True, False]
