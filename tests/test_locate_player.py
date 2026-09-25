"""locate_player: summarize UP presses / F9 marks per arrival side (portal x from both sides)."""
import locate_player as lp


def test_summary_groups_by_arrival_side_and_outcome():
    marks = [{"kind": "up", "x": 216, "y": 106, "side": "left", "ok": True},
             {"kind": "up", "x": 212, "y": 106, "side": "left", "ok": True},
             {"kind": "up", "x": 209, "y": 106, "side": "left", "ok": False},
             {"kind": "up", "x": 221, "y": 106, "side": "right", "ok": True},
             {"kind": "mark", "x": 219, "y": 106, "side": "right", "ok": None}]
    s = lp.summarize(marks)
    assert s["left"] == {"ok": [212, 216], "fail": [209], "mark": []}
    assert s["right"] == {"ok": [221], "fail": [], "mark": [219]}
    text = lp.format_summary(s)
    assert "walking LEFT" in text and "range 212..216" in text and "failed at x = [209]" in text


def test_summary_empty():
    assert "no UP presses" in lp.format_summary(lp.summarize([]))
