import pytest

def _skip_if_no_recovery():
    try:
        import recovery
        return recovery
    except Exception as e:
        pytest.skip(f"recovery not importable: {e}")

class _FakeKB:
    pause = False
    def safe_press(self, *a): pass
    def safe_release(self, *a): pass
    def safe_release_all(self, *a): pass

def test_record_climb_builds_reads(monkeypatch):
    recovery = _skip_if_no_recovery()
    seq = iter([(95, 143), (94, 130), (100, 118), (91, 100), (74, 85)])
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: next(seq, (74, 85)))
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    # stop after the sequence is exhausted by capping wall-time via a fake clock
    ticks = iter([0.0, 0.03, 0.06, 0.09, 0.12, 100.0])
    monkeypatch.setattr(recovery.time, "time", lambda: next(ticks, 100.0))
    tr = recovery.record_climb_attempt(cap=1.0)
    assert isinstance(tr, dict) and tr["reads"]
    assert tr["reads"][0][1:] == [95, 143]      # first (x,y)

def test_climb_plan_from_bottom_is_two_stage():
    recovery = _skip_if_no_recovery()
    assert recovery._climb_plan(143) == ["R_C->MID", "MID->TOP"]
    assert recovery._climb_plan(recovery.BOTTOM_Y_MIN) == ["R_C->MID", "MID->TOP"]

def test_climb_plan_from_mid_or_rest_is_one_stage():
    recovery = _skip_if_no_recovery()
    assert recovery._climb_plan(120) == ["MID->TOP"]                    # MID ledge
    assert recovery._climb_plan(105) == ["MID->TOP"]                    # REST platform
    assert recovery._climb_plan(recovery.BOTTOM_Y_MIN - 1) == ["MID->TOP"]

def test_climb_constants_present_and_ordered():
    recovery = _skip_if_no_recovery()
    assert recovery.R_C_X > recovery.R_L_X          # center rope is right of the left rope
    assert recovery.MID_Y_MIN <= recovery.MID_LEDGE_Y <= recovery.MID_Y_MAX
    assert recovery.TOP_EXIT_Y == recovery.ROPE_EXIT_TOP_Y

def _patch_climb(monkeypatch, recovery, ys, x=95):
    it = iter(ys)
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (x, next(it, ys[-1])))
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: True)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)

def test_climb_segment_reaches_exit_y(monkeypatch):
    recovery = _skip_if_no_recovery()
    _patch_climb(monkeypatch, recovery, [140, 130, 120, 110, 100, 92])
    assert recovery._climb_segment(95, 92, hop=True) is True

def test_climb_segment_stall_ok_arrives_at_ledge(monkeypatch):
    recovery = _skip_if_no_recovery()
    # climbs 140->118 then stalls at 118; exit_y=110 never reached, stall_ok -> True
    _patch_climb(monkeypatch, recovery, [140, 130, 120, 118, 118, 118, 118, 118])
    assert recovery._climb_segment(95, 110, hop=True, stall_ok=True) is True

def test_climb_segment_never_grabs_is_false(monkeypatch):
    recovery = _skip_if_no_recovery()
    _patch_climb(monkeypatch, recovery, [143, 143, 143, 143, 143, 143])
    assert recovery._climb_segment(95, 92, hop=True) is False

def test_climb_segment_walk_fail_is_false(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: False)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery._climb_segment(95, 92, hop=True) is False
