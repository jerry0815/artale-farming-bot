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


def test_composite_anchor_push_forwards_and_falls_back():
    import player

    class _Yolo:                     # fake YoloPlayerAnchor: locates iff a non-None box was pushed
        def __init__(self): self.pushed = None; self.last = None
        def push(self, box): self.pushed = box
        def locate(self, f):
            b = self.pushed; self.pushed = None
            return (b[1], b[2]) if b else None

    class _Nametag:
        last = (5, 5)
        def locate(self, f): return (5, 5)

    yolo, nt = _Yolo(), _Nametag()
    c = player.CompositeAnchor([yolo, nt])
    c.push((0.9, 100, 200, 10, 10))
    assert yolo.pushed == (0.9, 100, 200, 10, 10)   # forwarded to anchor 0
    assert c.locate(None) == (100, 200)             # used the pushed box
    assert c.which == 0
    c.push(None)                                     # player missed this beat
    assert c.locate(None) == (5, 5)                 # anchor 0 -> None -> nametag fallback
    assert c.which == 1


def test_composite_anchor_rejects_far_fallback_phantom():
    import player

    class _Yolo:                       # locates once at (150,300), then misses
        def __init__(self): self.r = [(150, 300)]; self.last = (150, 300)
        def locate(self, f): return self.r.pop(0) if self.r else None

    class _Nametag:                    # always the phantom at x=635 (far from 150)
        last = (635, 350)
        def locate(self, f): return (635, 350)

    c = player.CompositeAnchor([_Yolo(), _Nametag()], max_jump=250)
    assert c.locate(None) == (150, 300)         # yolo hit -> good = 150
    assert c.locate(None) is None               # yolo misses -> nametag 635 is 485px away -> rejected

    c2 = player.CompositeAnchor([_Yolo(), _Nametag()])   # no cap -> legacy: phantom slips through
    c2.locate(None)
    assert c2.locate(None) == (635, 350)


def test_composite_self_guarded_primary_not_rejected_by_phantom_good():
    import player
    # Reproduces the P6 log: a pinned nametag phantom set _good far right (x=1485); the player
    # really walked left and the SELF-GUARDED YOLO primary (has its own max_jump) reports x=510.
    # The composite must TRUST the primary (skip its cross-anchor guard) so YOLO reclaims the
    # lock -- else the low-priority phantom locks out the real anchor forever.
    class _Yolo:                           # trusted primary (YoloPlayerAnchor sets .trusted = True)
        trusted = True
        last = (510, 300)
        def locate(self, f): return (510, 300)

    class _Nametag:                        # phantom, no self-guard
        last = (1485, 802)
        def locate(self, f): return (1485, 802)

    c = player.CompositeAnchor([_Yolo(), _Nametag()], max_jump=250)
    c._good = (1485, 802)                  # phantom pinned _good far from the real player
    assert c.locate(None) == (510, 300)    # primary trusted despite 975px gap -> reclaims lock
    assert c.which == 0 and c._good == (510, 300)   # _good follows the real player back


def test_composite_guard_still_rejects_unguarded_primary_phantom():
    import player
    # The guard must STILL protect a nametag-PRIMARY map (both anchors are nametags, no self-guard):
    # a primary with no max_jump attr that leaps far from _good is still rejected as a phantom.
    class _Nametag:
        def __init__(self, p): self.p = p; self.last = p
        def locate(self, f): return self.p

    c = player.CompositeAnchor([_Nametag((999, 0)), _Nametag((100, 0))], max_jump=250)
    c._good = (100, 0)                      # primary is 899px away -> guard applies (not self-guarded)
    assert c.locate(None) == (100, 0) and c.which == 1   # primary rejected -> secondary used


def test_composite_anchor_reacquires_after_sustained_rejection():
    import player

    class _Far:                            # always reports a spot far from _good -> rejected
        last = (999, 0)
        def locate(self, f): return (999, 0)

    c = player.CompositeAnchor([_Far()], max_jump=250)
    c._good = (100, 0)                     # 899px away -> guard rejects
    for _ in range(3):
        assert c.locate(None) is None      # rejected while _good is fresh
    assert c.locate(None) == (999, 0)      # after 3 rejections _good dropped -> re-acquired


# --- home prior (flat_sweep, 2026-09-24): after a push dash she moves > near_tol on screen, the
# sticky lock fails, and the center pick grabbed a red sliver at the TOP of the screen (feet y 275
# vs her ~798) and followed it. The camera keeps her at ~the same spot, so re-acquire near `home`.

def test_lost_lock_reacquires_near_home_not_screen_center():
    # her bar at (1020, 663); a sliver near screen center (800, 150); last lock far away (dash)
    f = _frame([(1020, 663, 50, 8), (800, 150, 40, 8)])
    p = player.find_player(f, cfg={"foot_offset": 135}, near=(500, 900), home=(1020, 798))
    assert abs(p[0] - 1020) <= 4 and abs(p[1] - 798) <= 4


def test_sticky_lock_at_the_wrong_height_is_rejected():
    # last lock sits on the sliver (feet 285) -- but her floor's feet height is ~798
    f = _frame([(1020, 663, 50, 8), (800, 150, 40, 8)])
    p = player.find_player(f, cfg={"foot_offset": 135}, near=(800, 285), home=(1020, 798))
    assert abs(p[0] - 1020) <= 4


def test_sticky_lock_at_the_right_height_still_wins_over_home():
    # camera clamped at a map end: she is legitimately far from home x, same height
    f = _frame([(400, 663, 50, 8), (1020, 663, 50, 8)])
    p = player.find_player(f, cfg={"foot_offset": 135}, near=(410, 798), home=(1020, 798))
    assert abs(p[0] - 400) <= 4


def test_hpbar_anchor_uses_its_home():
    a = player.HPBarAnchor(cfg={"foot_offset": 135})
    a.home = (1020, 798)
    a.last = (500, 900)
    f = _frame([(1020, 663, 50, 8), (800, 150, 40, 8)])
    assert abs(a.locate(f)[0] - 1020) <= 4


def test_with_home_and_nothing_near_it_returns_none_not_junk():
    # her bar hidden by skill effects; only a sliver far from home -> None (caller rides last)
    f = _frame([(800, 150, 40, 8)])
    assert player.find_player(f, cfg={"foot_offset": 135}, home=(1020, 798)) is None


# --- feet band: at the start there is no home yet, and the center pick locked a red object near
# the TOP of the screen (feet y 365 vs her ~798 of 1135) for 13 s -> upper-floor mobs counted as
# hers -> pushes at nothing. cfg feet_band = allowed feet y as a fraction of the frame height.

def test_feet_band_drops_candidates_high_on_screen():
    f = _frame([(800, 150, 40, 8), (400, 600, 50, 8)])     # 1000-high frame: feet 285 vs 735
    p = player.find_player(f, cfg={"foot_offset": 135, "feet_band": [0.5, 0.9]})
    assert abs(p[0] - 400) <= 4                              # the high one (center-nearer) is out


def test_no_feet_band_by_default():
    f = _frame([(800, 150, 40, 8)])
    assert player.find_player(f, cfg={"foot_offset": 135}) is not None
