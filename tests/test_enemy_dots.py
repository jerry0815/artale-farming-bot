# tests/test_enemy_dots.py
"""Production another-player check: TEMPLATE is authoritative (drives the alarm); the HSV
color detector runs in SHADOW to validate the switch -- it logs presence agreement and dumps
sample frames (throttled) but never changes the alarm decision or crashes the safety loop."""
import numpy as np
import recovery


def _frame():
    return np.zeros((recovery.MM_Y + recovery.MM_H + 50,
                     recovery.MM_X + recovery.MM_W + 50, 3), dtype=np.uint8)


def _silence_shadow_io(monkeypatch):
    """Capture dump/log calls and reset throttle so a dump is never suppressed by prior tests."""
    dumps, logs = [], []
    monkeypatch.setattr(recovery, "_dump_enemy_sample", lambda frame, tag="": dumps.append(tag))
    monkeypatch.setattr(recovery, "_append_enemy_shadow_log", lambda row: logs.append(row))
    recovery._enemy_shadow_state["agree"] = 0.0
    recovery._enemy_shadow_state["disagree"] = 0.0
    return dumps, logs


def test_enemy_dots_returns_template_result_and_crops_minimap(monkeypatch):
    seen = {}

    def fake_template(mm, *a, **k):
        seen["shape"] = mm.shape          # confirm it received the minimap crop, not the full frame
        return [[5, 6]]

    monkeypatch.setattr(recovery, "detect_red_dots", fake_template)
    monkeypatch.setattr(recovery, "detect_red_dots_color", lambda *a, **k: [])
    _silence_shadow_io(monkeypatch)
    dots = recovery._enemy_dots(_frame())
    assert dots == [[5, 6]]                                        # TEMPLATE drives the return
    assert seen["shape"][0] <= recovery.MM_H and seen["shape"][1] <= recovery.MM_W


def test_enemy_dots_none_frame_is_empty():
    assert recovery._enemy_dots(None) == []


def test_enemy_dots_swallows_template_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cv2 blew up")

    monkeypatch.setattr(recovery, "detect_red_dots", boom)
    monkeypatch.setattr(recovery, "detect_red_dots_color", lambda *a, **k: [])
    _silence_shadow_io(monkeypatch)
    assert recovery._enemy_dots(_frame()) == []                   # never propagates into the safety loop


def test_shadow_dumps_on_agreement_when_both_fire(monkeypatch):
    monkeypatch.setattr(recovery, "detect_red_dots", lambda *a, **k: [[1, 1]])
    monkeypatch.setattr(recovery, "detect_red_dots_color", lambda *a, **k: [[1, 1]])
    dumps, logs = _silence_shadow_io(monkeypatch)
    out = recovery._enemy_dots(_frame())
    assert out == [[1, 1]]                                        # template result unaffected by shadow
    assert len(dumps) == 1 and "agree" in dumps[0]
    assert len(logs) == 1 and logs[0]["template"] == 1 and logs[0]["color"] == 1 and logs[0]["agree"] is True


def test_shadow_dumps_disagree_on_color_false_alarm(monkeypatch):
    monkeypatch.setattr(recovery, "detect_red_dots", lambda *a, **k: [])       # no real player
    monkeypatch.setattr(recovery, "detect_red_dots_color", lambda *a, **k: [[1, 1]])  # color false alarm
    dumps, logs = _silence_shadow_io(monkeypatch)
    out = recovery._enemy_dots(_frame())
    assert out == []                                             # template says clear -> NO alarm
    assert len(dumps) == 1 and "DISAGREE" in dumps[0]
    assert logs[0]["agree"] is False


def test_shadow_dumps_disagree_on_color_miss(monkeypatch):
    monkeypatch.setattr(recovery, "detect_red_dots", lambda *a, **k: [[2, 2]])       # real player
    monkeypatch.setattr(recovery, "detect_red_dots_color", lambda *a, **k: [])       # color missed it
    dumps, logs = _silence_shadow_io(monkeypatch)
    out = recovery._enemy_dots(_frame())
    assert out == [[2, 2]]                                       # alarm still fires (template)
    assert len(dumps) == 1 and "DISAGREE" in dumps[0]
    assert logs[0]["template"] == 1 and logs[0]["color"] == 0 and logs[0]["agree"] is False


def test_shadow_no_dump_when_both_clear(monkeypatch):
    monkeypatch.setattr(recovery, "detect_red_dots", lambda *a, **k: [])
    monkeypatch.setattr(recovery, "detect_red_dots_color", lambda *a, **k: [])
    dumps, logs = _silence_shadow_io(monkeypatch)
    out = recovery._enemy_dots(_frame())
    assert out == []
    assert dumps == [] and logs == []                            # an all-clear frame teaches nothing


def test_shadow_never_breaks_enemy_dots_on_color_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("color blew up")

    monkeypatch.setattr(recovery, "detect_red_dots", lambda *a, **k: [[9, 9]])
    monkeypatch.setattr(recovery, "detect_red_dots_color", boom)
    _silence_shadow_io(monkeypatch)
    assert recovery._enemy_dots(_frame()) == [[9, 9]]            # shadow error swallowed, alarm intact


def test_shadow_throttles_repeat_agreement(monkeypatch):
    monkeypatch.setattr(recovery, "detect_red_dots", lambda *a, **k: [[1, 1]])
    monkeypatch.setattr(recovery, "detect_red_dots_color", lambda *a, **k: [[1, 1]])
    dumps, logs = _silence_shadow_io(monkeypatch)
    recovery._enemy_dots(_frame())
    recovery._enemy_dots(_frame())                              # 2nd call within the throttle window
    assert len(dumps) == 1                                      # dump throttled...
    assert len(logs) == 2                                       # ...but every activity is still logged
