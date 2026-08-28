"""Tests for the player HP-bar anchor (synthetic frames)."""
import numpy as np
import player


def _frame(bars):
    """bars: list of (cx, cy, w, h) red bars painted on a teal frame."""
    f = np.full((1000, 1600, 3), (120, 90, 30), np.uint8)   # tealish bg (BGR)
    for cx, cy, w, h in bars:
        f[cy - h // 2:cy + h // 2, cx - w // 2:cx + w // 2] = (30, 30, 230)  # bright red
    return f


def test_bar_candidates_finds_bar_and_rejects_blob():
    f = _frame([(800, 400, 60, 6)])          # a bar
    f[500:560, 300:360] = (30, 30, 230)      # a big square red blob (not bar-shaped)
    cands = player.bar_candidates(f)
    assert any(abs(c[0] - 800) <= 3 and abs(c[1] - 400) <= 3 for c in cands)
    assert all(not (abs(c[0] - 330) <= 5 and abs(c[1] - 530) <= 5) for c in cands)


def test_pick_center_prefers_bar_near_screen_center():
    f = _frame([(300, 400, 60, 6), (805, 430, 60, 6)])   # off-center + near-center(800)
    cands = player.bar_candidates(f)
    chosen = player.pick_center(cands, f.shape)
    assert abs(chosen[0] - 805) <= 4          # picks the centered one (monster-bar reject)


def test_find_player_returns_feet_below_bar():
    f = _frame([(805, 430, 60, 6)])
    p = player.find_player(f, cfg={"foot_offset": 135})
    assert p is not None
    assert abs(p[0] - 805) <= 4
    assert abs(p[1] - (430 + 135)) <= 4       # feet = bar_y + offset


def test_find_player_none_when_no_bar():
    f = np.full((1000, 1600, 3), (120, 90, 30), np.uint8)
    assert player.find_player(f) is None


def test_find_player_debug_shape():
    f = _frame([(805, 430, 60, 6)])
    d = player.find_player(f, debug=True)
    assert set(d) == {"player", "bar", "candidates"}
    assert d["bar"] is not None and len(d["candidates"]) >= 1


def test_nametag_anchor_locates_and_tracks():
    import numpy as np, cv2, player
    # a synthetic frame with a white "name" block on a dark bg
    frame = np.full((600, 800, 3), 30, np.uint8)
    frame[300:320, 400:470] = 220                     # white name text at (435,300)
    tagw = cv2.inRange(cv2.GaussianBlur(cv2.cvtColor(frame[298:322, 398:472], cv2.COLOR_BGR2GRAY), (3, 3), 0), 150, 255)
    a = player.NametagAnchor(tagw, feet_offset=6)
    p = a.locate(frame)
    assert p is not None
    assert abs(p[0] - 435) <= 8                        # name-tag center x ~ player x
    assert a.last is not None                          # locked -> caches location
    # a second frame with the name shifted right by 20 -> local-cache still finds it
    f2 = np.full((600, 800, 3), 30, np.uint8)
    f2[300:320, 420:490] = 220
    p2 = a.locate(f2)
    assert p2 is not None and abs(p2[0] - 455) <= 10


def test_nametag_stale_grace_keeps_last_on_brief_miss():
    import numpy as np, cv2, player
    frame = np.full((600, 800, 3), 30, np.uint8); frame[300:320, 400:470] = 220
    tagw = cv2.inRange(cv2.GaussianBlur(cv2.cvtColor(frame[298:322, 398:472], cv2.COLOR_BGR2GRAY), (3, 3), 0), 150, 255)
    a = player.NametagAnchor(tagw, stale_grace=2)
    assert a.locate(frame) is not None                 # lock
    blank = np.full((600, 800, 3), 30, np.uint8)       # tag gone (occluded)
    p1 = a.locate(blank); p2 = a.locate(blank); p3 = a.locate(blank)
    assert p1 is not None and p2 is not None           # stale: kept for 2 frames
    assert p3 is None                                  # grace exhausted -> lost


def test_composite_anchor_falls_back_to_secondary():
    class A:
        def __init__(self, p): self.p = p; self.last = (1, 2)
        def locate(self, f): return self.p
    c = player.CompositeAnchor([A(None), A((99, 88))])
    assert c.locate(None) == (99, 88) and c.which == 1  # primary None -> secondary used
    c2 = player.CompositeAnchor([A((5, 6)), A((7, 8))])
    assert c2.locate(None) == (5, 6) and c2.which == 0  # primary wins
