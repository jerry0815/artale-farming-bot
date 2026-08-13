import os, json
import numpy as np
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
    ticks = iter([0.0, 0.03, 0.06, 0.09, 0.12, 100.0])
    monkeypatch.setattr(recovery.time, "time", lambda: next(ticks, 100.0))
    tr = recovery.record_climb_attempt(cap=1.0)
    assert isinstance(tr, dict) and tr["reads"]
    assert tr["reads"][0][1:] == [95, 143]

def test_climb_constants_present():
    recovery = _skip_if_no_recovery()
    assert recovery.TOP_EXIT_Y == recovery.ROPE_EXIT_TOP_Y
    assert recovery.SCROLL_RISE_PX > 0
    assert recovery.CLIMB_STALL_S > 0 and recovery.MAX_GRABS >= 1
    assert 80 <= recovery.ROPE_X <= 100 and recovery.BOTTOM_ROPE_X >= recovery.ROPE_X

# --- minimap_scroll: terrain scroll recovers climb progress the pinned dot hides ---

def test_minimap_scroll_detects_vertical_shift():
    recovery = _skip_if_no_recovery()
    rng = np.random.RandomState(0)
    tex = rng.randint(0, 60, (120, 100), np.uint8)
    img = np.stack([tex + 150, tex + 40, tex], axis=2).astype(np.uint8)   # blue-ish (not yellow)
    shifted = np.roll(img, 6, axis=0)                                     # terrain moved 6 rows
    dy, resp = recovery.minimap_scroll(img, shifted)
    assert resp > 0.5
    assert abs(abs(dy) - 6) <= 2                                          # ~6px shift detected

def test_minimap_scroll_positive_on_real_ascent():
    recovery = _skip_if_no_recovery()
    import cv2
    if not os.path.exists("climb_frames/frames.jsonl"):
        pytest.skip("no climb_frames recording present")
    rows = [json.loads(l) for l in open("climb_frames/frames.jsonl") if l.strip()]
    acc, prev = 0.0, None
    for r in rows:
        idx = int(r["frame"].split("_")[1].split(".")[0])
        if not (20 <= idx <= 30):                                         # the pinned lower-rope ascent
            continue
        crop = recovery._minimap(cv2.imread("climb_frames/" + r["frame"]))
        if prev is not None:
            dy, _ = recovery.minimap_scroll(prev, crop); acc += dy
        prev = crop
    assert acc > 10                                                       # positive == rising

# --- _climb_to_top: scroll-tracked climb ---

def test_climb_to_top_reaches_when_dot_resolves(monkeypatch):
    recovery = _skip_if_no_recovery()
    ys = iter([136, 136, 120, 100, 90])                                  # pinned, then resolves to top
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: True)
    monkeypatch.setattr(recovery, "capture", lambda *a, **k: "IMG")
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, next(ys, 90)))
    monkeypatch.setattr(recovery, "_minimap", lambda img: img)
    monkeypatch.setattr(recovery, "minimap_scroll", lambda a, b, **k: (5.0, 0.9))
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery._climb_to_top(95, hop=True) is True

def test_climb_to_top_stuck_aborts(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: True)
    monkeypatch.setattr(recovery, "capture", lambda *a, **k: "IMG")
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (95, 136))  # pinned forever
    monkeypatch.setattr(recovery, "_minimap", lambda img: img)
    monkeypatch.setattr(recovery, "minimap_scroll", lambda a, b, **k: (0.0, 0.9))   # no net rise
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    clk = {"t": 0.0}
    def fake_time():
        clk["t"] += 0.3; return clk["t"]
    monkeypatch.setattr(recovery.time, "time", fake_time)
    assert recovery._climb_to_top(95, hop=True) is False                 # grace trips -> abort

def test_climb_to_top_bridges_on_midclimb_stall(monkeypatch):
    recovery = _skip_if_no_recovery()
    presses = []
    class RecKB:
        pause = False
        def safe_press(self, k): presses.append(k)
        def safe_release(self, k): pass
        def safe_release_all(self): pass
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: True)
    monkeypatch.setattr(recovery, "capture", lambda *a, **k: "IMG")
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (95, 136))  # pinned, stalled
    monkeypatch.setattr(recovery, "_minimap", lambda img: img)
    monkeypatch.setattr(recovery, "minimap_scroll", lambda a, b, **k: (0.0, 0.9))   # no net rise
    monkeypatch.setattr(recovery, "kb", RecKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    clk = {"t": 0.0}
    def fake_time():
        clk["t"] += 0.3; return clk["t"]
    monkeypatch.setattr(recovery.time, "time", fake_time)
    recovery._climb_to_top(95, hop=False)                # no initial hop, so JUMPs are all bridges
    assert presses.count(recovery.JUMP) >= 1             # a bridge jump fired during the stall

def test_climb_to_top_walk_fail_off_column_is_false(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: False)
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (60, 143))  # far from column
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery._climb_to_top(95, hop=True) is False                  # can't walk & off-column -> bail

def test_climb_to_top_proceeds_when_already_on_rope(monkeypatch):
    recovery = _skip_if_no_recovery()
    # walk_to_x fails (she's HANGING on the rope, can't walk) but she's already ON the column
    # (x==align) -> must proceed to hold Up and climb, not bail (this was the infinite-loop bug).
    ys = iter([105, 100, 95, 90])
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: False)
    monkeypatch.setattr(recovery, "capture", lambda *a, **k: "IMG")
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, next(ys, 90)))
    monkeypatch.setattr(recovery, "_minimap", lambda img: img)
    monkeypatch.setattr(recovery, "minimap_scroll", lambda a, b, **k: (5.0, 0.9))
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery._climb_to_top(91, hop=True) is True                   # on column -> climbs to top

# --- climb_and_jump: wiring ---

def test_climb_and_jump_from_bottom_uses_base_grab(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, 143))   # bottom
    calls = {}
    def fake(grab_x, hop, **k):
        calls["grab_x"] = grab_x; calls["hop"] = hop; return True
    monkeypatch.setattr(recovery, "_climb_to_top", fake)
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (74, 85))   # mounted after up-jump
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is True
    assert calls["grab_x"] == recovery.BOTTOM_ROPE_X                      # align to the rope BASE
    assert calls["hop"] is True                                          # hop-grab from the floor

def test_climb_and_jump_from_mid_still_hops(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (95, 136))   # mid ledge (pinned)
    calls = {}
    monkeypatch.setattr(recovery, "_climb_to_top",
                        lambda grab_x, hop, **k: (calls.update(grab_x=grab_x, hop=hop) or True))
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (74, 85))   # mounted after up-jump
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is True
    assert calls["hop"] is True                          # MUST hop (jump-grab) from the mid ledge too

def test_climb_and_jump_false_if_not_mounted(monkeypatch):
    recovery = _skip_if_no_recovery()
    # _climb_to_top reaches the rope top, but the up-jump never mounts -- stable_char keeps
    # reading the rope top (y=90 > ON_PLATFORM_Y). climb_and_jump must NOT report success.
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, 95))
    monkeypatch.setattr(recovery, "_climb_to_top", lambda *a, **k: True)
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (91, 90))    # still hanging
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is False            # y=90 > ON_PLATFORM_Y(88) -> not mounted

def test_climb_and_jump_false_when_climb_misses(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, 143))
    monkeypatch.setattr(recovery, "_climb_to_top", lambda *a, **k: False)
    released = {"all": False}
    class KB(_FakeKB):
        def safe_release_all(self, *a): released["all"] = True
    monkeypatch.setattr(recovery, "kb", KB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is False
    assert released["all"] is True
