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

def test_climb_segment_transient_stall_does_not_abort(monkeypatch):
    recovery = _skip_if_no_recovery()
    # climbs 140->130->124, stalls briefly (124->124), resumes (124->118->...->92);
    # with stall_ok=False, transient stall must NOT abort -> continues and reaches exit_y
    _patch_climb(monkeypatch, recovery, [140, 130, 124, 124, 118, 110, 100, 92])
    assert recovery._climb_segment(95, 92, hop=True, stall_ok=False) is True

def test_climb_and_jump_two_stage_from_bottom(monkeypatch):
    recovery = _skip_if_no_recovery()
    # first read = bottom (y143) -> two-stage plan; after stage 1, read = MID ledge (y118)
    pos = iter([(95, 143), (100, 118)])
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: next(pos, (100, 118)))
    seg_calls, walks = [], []
    monkeypatch.setattr(recovery, "_climb_segment",
                        lambda grab_x, exit_y, hop, **k: seg_calls.append(grab_x) or True)
    monkeypatch.setattr(recovery, "walk_to_x", lambda x, **k: walks.append(x) or True)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is True
    assert seg_calls == [recovery.R_C_X, recovery.R_L_X]     # center rope, then left rope
    assert recovery.R_L_X in walks                           # the LEFT shift happened

def test_climb_and_jump_one_stage_from_mid(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, 120))  # on MID
    seg_calls = []
    monkeypatch.setattr(recovery, "_climb_segment",
                        lambda grab_x, exit_y, hop, **k: seg_calls.append(grab_x) or True)
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: True)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is True
    assert seg_calls == [recovery.R_L_X]                      # only the left rope

def test_climb_segment_slow_climb_survives_flat_frames(monkeypatch):
    recovery = _skip_if_no_recovery()
    # Cumulative rise latches 'climbing' by y=112 (118-6); then several flat frames
    # (idle bob / template jitter) must NOT abort a real top-climb before exit_y.
    # Under the OLD per-frame gate 'climbing' never latches and no_grab aborts at the
    # third flat frame -> this asserts the cumulative-latch + no_grab-reset fix.
    _patch_climb(monkeypatch, recovery, [118, 115, 112, 112, 112, 112, 108, 104, 100, 96, 92])
    assert recovery._climb_segment(91, 92, hop=False, stall_ok=False) is True


def test_climb_and_jump_bails_if_not_on_ledge_after_stage1(monkeypatch):
    recovery = _skip_if_no_recovery()
    # bottom start, but after stage 1 she is NOT in the MID band (fell to y145)
    pos = iter([(95, 143), (95, 145)])
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: next(pos, (95, 145)))
    monkeypatch.setattr(recovery, "_climb_segment", lambda *a, **k: True)
    walks = []
    monkeypatch.setattr(recovery, "walk_to_x", lambda x, **k: walks.append(x) or True)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is False
    assert recovery.R_L_X not in walks                       # never attempted the left shift
