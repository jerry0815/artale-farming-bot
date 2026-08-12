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

def test_climb_constants_present():
    recovery = _skip_if_no_recovery()
    assert recovery.TOP_EXIT_Y == recovery.ROPE_EXIT_TOP_Y
    assert recovery.BRIDGE_Y_MIN < recovery.BRIDGE_Y_MAX
    assert 80 <= recovery.ROPE_X <= 100                  # the single climb column

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
    # with stall_ok=False and NO bridge_band, a transient stall must NOT abort.
    _patch_climb(monkeypatch, recovery, [140, 130, 124, 124, 118, 110, 100, 92])
    assert recovery._climb_segment(95, 92, hop=True, stall_ok=False) is True

def test_climb_segment_slow_climb_survives_flat_frames(monkeypatch):
    recovery = _skip_if_no_recovery()
    # Cumulative rise latches 'climbing' by y=112 (118-6); then several flat frames
    # (idle bob / template jitter) must NOT abort a real top-climb before exit_y.
    # Under the OLD per-frame gate 'climbing' never latches and no_grab aborts at the
    # third flat frame -> this asserts the cumulative-latch + no_grab-reset fix.
    _patch_climb(monkeypatch, recovery, [118, 115, 112, 112, 112, 112, 108, 104, 100, 96, 92])
    assert recovery._climb_segment(91, 92, hop=False, stall_ok=False) is True

def test_climb_segment_bridges_when_stalled_at_ladder_top(monkeypatch):
    recovery = _skip_if_no_recovery()
    # climbs to the ladder top (y124), STALLS there (inside bridge band 118-130), then a
    # bridge jump fires and she resumes onto the chain to reach exit_y. Asserts BOTH that a
    # JUMP was pressed mid-climb and that the segment succeeds.
    presses = []
    class RecKB:
        pause = False
        def safe_press(self, k): presses.append(k)
        def safe_release(self, k): pass
        def safe_release_all(self): pass
    ys = iter([136, 130, 124, 124, 124, 110, 100, 92])
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, next(ys, 92)))
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: True)
    monkeypatch.setattr(recovery, "kb", RecKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    ok = recovery._climb_segment(91, 92, hop=False, bridge_band=(118, 130))
    assert ok is True
    assert presses.count(recovery.JUMP) == 1             # exactly one bridge jump (hop=False)

def test_climb_and_jump_single_continuous_from_bottom(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, 143))   # bottom
    calls = []
    def fake_seg(grab_x, exit_y, hop, **kw):
        calls.append(dict(grab_x=grab_x, exit_y=exit_y, hop=hop, bridge=kw.get("bridge_band")))
        return True
    monkeypatch.setattr(recovery, "_climb_segment", fake_seg)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is True
    assert len(calls) == 1                                        # ONE continuous climb
    assert calls[0]["grab_x"] == recovery.ROPE_X                  # the single column
    assert calls[0]["hop"] is True                               # from the bottom platform
    assert calls[0]["bridge"] == (recovery.BRIDGE_Y_MIN, recovery.BRIDGE_Y_MAX)

def test_climb_and_jump_false_when_segment_misses(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, 143))
    monkeypatch.setattr(recovery, "_climb_segment", lambda *a, **k: False)
    released = {"all": False}
    class KB(_FakeKB):
        def safe_release_all(self, *a): released["all"] = True
    monkeypatch.setattr(recovery, "kb", KB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is False
    assert released["all"] is True                                # keys released on the miss
