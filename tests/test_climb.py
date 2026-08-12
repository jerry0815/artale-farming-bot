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
