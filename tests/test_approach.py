"""approach_shoot decision logic (fakes for capture/player/fish/keys; no game)."""
import numpy as np
import recovery
import player
from pynput.keyboard import Key


class _Anchor:                                        # stand-in for a player anchor
    def __init__(self, ptuple):
        self.p = ptuple

    def locate(self, f):
        return self.p


def _setup(monkeypatch, mobs, ptuple, clock_vals):
    monkeypatch.setattr(recovery, "capture", lambda: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    it = iter(clock_vals)
    monkeypatch.setattr(recovery.time, "time", lambda: next(it, 10_000))
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    detect_fn = lambda f, roi: mobs
    return detect_fn, pressed, _Anchor(ptuple)


def test_depleted_when_no_same_platform_mob(monkeypatch):
    # no mobs -> needs deplete_reads consecutive empty reads before DEPLETED (debounced)
    df, _, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0, 0, 0])
    assert recovery.approach_shoot(10, df, anc, deplete_reads=2, verbose=False) is recovery.DEPLETED


def test_not_depleted_on_single_empty_frame(monkeypatch):
    # a single empty frame must NOT declare depleted (flaky detection guard)
    df, _, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0, 0, 999])
    # only one loop iteration (one empty read) then time runs out -> True, not DEPLETED
    assert recovery.approach_shoot(10, df, anc, deplete_reads=4, verbose=False) is True


def test_off_platform_mob_ignored_then_depleted(monkeypatch):
    # a mob far below the player's feet (different platform) -> not same-platform
    mob = [(0.7, 1200, 900, 60, 40)]                 # bottom=940 vs feet 500 -> |440|>band
    df, _, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0, 0])
    assert recovery.approach_shoot(10, df, anc, band=70, verbose=False,
                                   deplete_reads=2) is recovery.DEPLETED


def test_walks_right_toward_far_mob(monkeypatch):
    mob = [(0.7, 1200, 480, 60, 40)]                 # cx=1230, bottom=520 ~ feet 500 -> same
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, attack_range=110, verbose=False)
    assert Key.right in pressed and 'c' not in pressed   # walk only (attack roots her)


def test_walks_left_toward_far_mob(monkeypatch):
    mob = [(0.7, 100, 480, 60, 40)]                  # cx=130, far left of player 800
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, attack_range=110, verbose=False)
    assert Key.left in pressed and 'c' not in pressed    # walk only, no attack mid-approach


def test_attacks_in_place_when_in_range(monkeypatch):
    mob = [(0.7, 800, 480, 60, 40)]                  # cx=830, dx=30 <= range 110
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, attack_range=110, verbose=False)
    assert 'c' in pressed                            # fires


def test_blind_attack_when_anchor_lost(monkeypatch):
    mob = [(0.7, 800, 480, 60, 40)]
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=None, clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, verbose=False)
    assert 'c' in pressed                            # no anchor -> still attacks


class _AnchorSeq:                                     # anchor that returns a scripted sequence
    def __init__(self, seq):
        self.seq, self.i = list(seq), 0

    def locate(self, f):
        v = self.seq[self.i] if self.i < len(self.seq) else self.seq[-1]
        self.i += 1
        return v


def test_assumes_last_position_when_anchor_lost_after_lock(monkeypatch):
    # Skill VFX hides the HP bar (anchor -> None) but the skill ROOTS her, so once locked we
    # assume her last position and keep farming from there (walk toward the mob), NOT a blind
    # attack. Mob far right & out of range -> she should keep walking right, never firing 'c'.
    mob = [(0.7, 1200, 480, 60, 40)]                 # cx=1230 vs player 800 -> far right
    df, pressed, _ = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0] * 12)
    anc = _AnchorSeq([(800, 500), None, None, None])  # lock, then bar hidden by VFX
    recovery.approach_shoot(10, df, anc, attack_range=110, stall_limit=8, verbose=False)
    assert Key.right in pressed and 'c' not in pressed   # walked from the assumed last pos


def test_minimap_bound_fires_in_place_at_platform_edge(monkeypatch):
    # Mob far to the right (would step right), but she's at the right minimap bound ->
    # must FIRE IN PLACE, not walk off the platform.
    import numpy as np
    mob = [(0.9, 1600, 480, 60, 40)]                 # cx=1630, dx large, out of range
    monkeypatch.setattr(recovery, "capture", lambda: np.zeros((1000, 1600, 3), np.uint8))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(player, "find_player", lambda f, cfg=None, near=None: (800, 500))
    monkeypatch.setattr(recovery, "stable_char", lambda n=2: (180, 136))   # at right bound
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    presses = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: presses.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    it = iter([0, 0])
    monkeypatch.setattr(recovery.time, "time", lambda: next(it, 10_000))
    recovery.approach_shoot(10, lambda f, roi: mob, _Anchor((800, 500)), attack_range=90,
                            mm_bounds=(60, 180), verbose=False)
    assert Key.right not in presses      # did NOT walk off the platform
    assert 'c' in presses                # fired in place instead
