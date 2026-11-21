"""Panel: the kite attack tap timing (shot_press_secs / shot_gap_secs) editor."""
import json
import panel


def _cfg(tmp_path, data):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_save_tap_sets_and_keeps_other_fields(tmp_path):
    f = _cfg(tmp_path, {"name": "a", "attack_key": "x"})
    ok, _ = panel.save_tap(f, {"shot_press_secs": 0.04, "shot_gap_secs": 0.03}, [f])
    assert ok
    d = json.loads(open(f, encoding="utf-8").read())
    assert d == {"name": "a", "attack_key": "x", "shot_press_secs": 0.04, "shot_gap_secs": 0.03}


def test_save_tap_blank_clears_to_default(tmp_path):
    f = _cfg(tmp_path, {"shot_press_secs": 0.04, "shot_gap_secs": 0.04})
    ok, _ = panel.save_tap(f, {"shot_press_secs": 0.06, "shot_gap_secs": None}, [f])
    assert ok and json.loads(open(f, encoding="utf-8").read()) == {"shot_press_secs": 0.06}


def test_save_tap_rejects_bad_values_and_paths(tmp_path):
    f = _cfg(tmp_path, {})
    assert not panel.save_tap(f, {"shot_press_secs": 0, "shot_gap_secs": None}, [f])[0]
    assert not panel.save_tap(f, {"shot_press_secs": 0.04}, [])[0]
