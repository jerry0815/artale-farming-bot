# tests/test_enemy_dots.py
"""Production another-player check: TEMPLATE is authoritative (drives the alarm); the HSV
color detector runs in SHADOW to validate the switch -- it logs presence agreement and dumps
sample frames (throttled) but never changes the alarm decision or crashes the safety loop."""
import numpy as np
import core


def _frame():
    return np.zeros((core.MM_Y + core.MM_H + 50,
                     core.MM_X + core.MM_W + 50, 3), dtype=np.uint8)


def _silence_shadow_io(monkeypatch):
    """Capture dump/log calls and reset throttle so a dump is never suppressed by prior tests."""
    dumps, logs = [], []
    monkeypatch.setattr(core, "_dump_enemy_sample", lambda frame, tag="": dumps.append(tag))
    monkeypatch.setattr(core, "_append_enemy_shadow_log", lambda row: logs.append(row))
    core._enemy_shadow_state["agree"] = 0.0
    core._enemy_shadow_state["disagree"] = 0.0
    return dumps, logs


def test_enemy_dots_returns_template_result_and_crops_minimap(monkeypatch):
    seen = {}

    def fake_template(mm, *a, **k):
        seen["shape"] = mm.shape          # confirm it received the minimap crop, not the full frame
        return [[5, 6]]

    monkeypatch.setattr(core, "detect_red_dots", fake_template)
    monkeypatch.setattr(core, "detect_red_dots_color", lambda *a, **k: [])
    _silence_shadow_io(monkeypatch)
    dots = core._enemy_dots(_frame())
    assert dots == [[5, 6]]                                        # TEMPLATE drives the return
    assert seen["shape"][0] <= core.MM_H and seen["shape"][1] <= core.MM_W


def test_enemy_dots_none_frame_is_empty():
    assert core._enemy_dots(None) == []


def test_enemy_dots_swallows_template_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cv2 blew up")

    monkeypatch.setattr(core, "detect_red_dots", boom)
    monkeypatch.setattr(core, "detect_red_dots_color", lambda *a, **k: [])
    _silence_shadow_io(monkeypatch)
    assert core._enemy_dots(_frame()) == []                   # never propagates into the safety loop


def test_shadow_dumps_on_agreement_when_both_fire(monkeypatch):
    monkeypatch.setattr(core, "detect_red_dots", lambda *a, **k: [[1, 1]])
    monkeypatch.setattr(core, "detect_red_dots_color", lambda *a, **k: [[1, 1]])
    dumps, logs = _silence_shadow_io(monkeypatch)
    out = core._enemy_dots(_frame())
    assert out == [[1, 1]]                                        # template result unaffected by shadow
    assert len(dumps) == 1 and "agree" in dumps[0]
    assert len(logs) == 1 and logs[0]["template"] == 1 and logs[0]["color"] == 1 and logs[0]["agree"] is True


def test_shadow_dumps_disagree_on_color_false_alarm(monkeypatch):
    monkeypatch.setattr(core, "detect_red_dots", lambda *a, **k: [])       # no real player
    monkeypatch.setattr(core, "detect_red_dots_color", lambda *a, **k: [[1, 1]])  # color false alarm
    dumps, logs = _silence_shadow_io(monkeypatch)
    out = core._enemy_dots(_frame())
    assert out == []                                             # template says clear -> NO alarm
    assert len(dumps) == 1 and "DISAGREE" in dumps[0]
    assert logs[0]["agree"] is False


def test_shadow_dumps_disagree_on_color_miss(monkeypatch):
    monkeypatch.setattr(core, "detect_red_dots", lambda *a, **k: [[2, 2]])       # real player
    monkeypatch.setattr(core, "detect_red_dots_color", lambda *a, **k: [])       # color missed it
    dumps, logs = _silence_shadow_io(monkeypatch)
    out = core._enemy_dots(_frame())
    assert out == [[2, 2]]                                       # alarm still fires (template)
    assert len(dumps) == 1 and "DISAGREE" in dumps[0]
    assert logs[0]["template"] == 1 and logs[0]["color"] == 0 and logs[0]["agree"] is False


def test_shadow_no_dump_when_both_clear(monkeypatch):
    monkeypatch.setattr(core, "detect_red_dots", lambda *a, **k: [])
    monkeypatch.setattr(core, "detect_red_dots_color", lambda *a, **k: [])
    dumps, logs = _silence_shadow_io(monkeypatch)
    out = core._enemy_dots(_frame())
    assert out == []
    assert dumps == [] and logs == []                            # an all-clear frame teaches nothing


def test_shadow_never_breaks_enemy_dots_on_color_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("color blew up")

    monkeypatch.setattr(core, "detect_red_dots", lambda *a, **k: [[9, 9]])
    monkeypatch.setattr(core, "detect_red_dots_color", boom)
    _silence_shadow_io(monkeypatch)
    assert core._enemy_dots(_frame()) == [[9, 9]]            # shadow error swallowed, alarm intact


def test_shadow_throttles_repeat_agreement(monkeypatch):
    monkeypatch.setattr(core, "detect_red_dots", lambda *a, **k: [[1, 1]])
    monkeypatch.setattr(core, "detect_red_dots_color", lambda *a, **k: [[1, 1]])
    dumps, logs = _silence_shadow_io(monkeypatch)
    core._enemy_dots(_frame())
    core._enemy_dots(_frame())                              # 2nd call within the throttle window
    assert len(dumps) == 1                                      # dump throttled...
    assert len(logs) == 2                                       # ...but every activity is still logged


def test_sample_dumps_are_off_by_default(monkeypatch, tmp_path):
    # the shadow comparison saved a full screenshot every ~10 s whenever template and color
    # disagreed -- 18 GB (566 frames in one day on flat_sweep). Opt-in now.
    monkeypatch.setattr(core, "_ENEMY_SAMPLE_DIR", str(tmp_path / "samples"))
    assert core._ENEMY_SAMPLE_DUMPS is False
    assert core._dump_enemy_sample(np.zeros((10, 10, 3), np.uint8), "x") is None
    assert not (tmp_path / "samples").exists()
    monkeypatch.setattr(core, "_ENEMY_SAMPLE_DUMPS", True)
    assert core._dump_enemy_sample(np.zeros((10, 10, 3), np.uint8), "x") is not None


def test_fast_tick_leaves_the_other_player_to_the_loop_when_told(monkeypatch):
    """other_player 'wait' mode: the loop handles a red dot (stop, wait, Free Market after N s);
    the fast safety scan must not also beep + pause."""
    calls = []
    monkeypatch.setattr(core, "_last_fast_tick", [0.0])
    monkeypatch.setattr(core, "_lie_enabled", False)
    monkeypatch.setattr(core, "capture", lambda: np.zeros((50, 50, 3), np.uint8))
    monkeypatch.setattr(core, "_enemy_dots", lambda f: [[5, 5]])
    monkeypatch.setattr(core, "enemy_alarm_on", lambda: calls.append("alarm"))
    monkeypatch.setattr(core.notify, "send", lambda *a, **k: calls.append("notify"))
    monkeypatch.setattr(core.kb, "pause", False, raising=False)
    monkeypatch.setattr(core.kb, "safe_release_all", lambda: None)
    monkeypatch.setattr(core, "_ENEMY_PAUSE", [False])
    core.lie_check_fast_tick()
    assert calls == [] and core.kb.pause is False
    monkeypatch.setattr(core, "_last_fast_tick", [0.0])
    monkeypatch.setattr(core, "_ENEMY_PAUSE", [True])            # default: old alarm + pause
    core.lie_check_fast_tick()
    assert "alarm" in calls and core.kb.pause is True
