"""Pure-logic tests for recovery helpers that don't need the live game.

recovery.py imports mss/pyautogui/pynput (installed here), so importing it is
fine; nothing at import time touches the screen or sends keys.
"""
import pytest

recovery = pytest.importorskip("recovery")


def test_pick_char_blob_prefers_in_box_over_corner_phantom():
    # real dot on a platform + a heavy-glow phantom at the minimap corner
    real = (64, 109, 54)
    phantom = (2, 242, 60)          # bigger, but out of the valid map box
    # even though the phantom is larger, the in-box real dot must win
    assert recovery._pick_char_blob([phantom, real])[:2] == (64, 109)


def test_pick_char_blob_rejects_documented_phantoms():
    real = (63, 143, 50)
    for ph in [(22, 232, 70), (2, 242, 40), (36, 153, 55), (28, 235, 80), (122, 188, 90)]:
        got = recovery._pick_char_blob([ph, real])
        assert got[:2] == (63, 143), f"phantom {ph} was picked over the real dot"


def test_pick_char_blob_near_prefers_in_box():
    real = (70, 90, 45)
    phantom = (30, 240, 45)
    # a stale `near` close to the phantom must NOT drag the pick out of the map box
    assert recovery._pick_char_blob([phantom, real], near=(28, 238))[:2] == (70, 90)


def test_pick_char_blob_falls_back_when_none_in_box():
    # genuine off-map read (deep fall): no in-box blob -> still return something so
    # recovery/panic can act, rather than losing her entirely
    deep = (120, 205, 50)
    assert recovery._pick_char_blob([deep])[:2] == (120, 205)


def test_pick_char_blob_empty_is_none():
    assert recovery._pick_char_blob([]) is None


def test_reactive_deplete_debounce():
    # healthy count (>= threshold) resets the streak, never rotates
    assert recovery._reactive_deplete(1, 5, 2, 2) == (0, False)
    assert recovery._reactive_deplete(1, 2, 2, 2) == (0, False)   # == threshold is healthy
    # first low read: streak advances, no rotate yet
    assert recovery._reactive_deplete(0, 1, 2, 2) == (1, False)
    # second consecutive low read: rotate
    assert recovery._reactive_deplete(1, 0, 2, 2) == (2, True)
    # a failed frame (count is None) resets the streak (conservative, no rotate)
    assert recovery._reactive_deplete(1, None, 2, 2) == (0, False)


def test_exp_tracker_window_gain_filters(monkeypatch):
    """make_exp_tracker sums positive same-digit-count deltas per window, skipping
    negatives (bad OCR) and digit-count changes (level-ups / OCR errors)."""
    clock = [1000.0]
    monkeypatch.setattr(recovery.time, "time", lambda: clock[0])
    monkeypatch.setattr(recovery, "ExpProcessor", lambda *a, **k: object())
    nums = [100, 150, 149, 700, 1200]        # +50 | -1 skip | +551 | +500 digit-change skip
    calls = [0]

    def fake_num(_proc):
        i = calls[0]; calls[0] += 1
        return nums[i] if i < len(nums) else None
    monkeypatch.setattr(recovery, "get_exp_number", fake_num)

    tick = recovery.make_exp_tracker(sample_secs=1, window_secs=5, label="test")
    for t in range(1001, 1006):              # samples at 1001..1005; window logs at 1005
        clock[0] = float(t)
        tick()
    assert recovery.STATUS["exp_10min"] == 601   # 50 + 551, negatives & digit-change dropped
    assert recovery.STATUS["exp_total"] == 601   # cumulative gain since run start
    # live per-minute run average: 601 gained over 5s elapsed = 601 * 60/5 = 7212
    assert recovery.STATUS["exp_per_min"] == 7212


def test_exp_tracker_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(recovery, "ExpProcessor", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not construct")))
    tick = recovery.make_exp_tracker(log_exp=False)
    tick()                                        # must not raise / not sample
