"""approach_shoot decision logic (fakes for capture/player/fish/keys; no game)."""
import numpy as np
import recovery
import player
from pynput.keyboard import Key


def _setup(monkeypatch, mobs, ptuple, clock_vals):
    monkeypatch.setattr(recovery, "capture", lambda: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(player, "find_player", lambda f, cfg=None: ptuple)
    monkeypatch.setattr(recovery.kb, "pause", False, raising=False)
    it = iter(clock_vals)
    monkeypatch.setattr(recovery.time, "time", lambda: next(it, 10_000))
    monkeypatch.setattr(recovery.time, "sleep", lambda s: None)
    pressed = []
    monkeypatch.setattr(recovery.kb, "safe_press", lambda k: pressed.append(k))
    monkeypatch.setattr(recovery.kb, "safe_release", lambda k: None)
    detect_fn = lambda f, roi: mobs
    return detect_fn, pressed


def test_depleted_when_no_same_platform_mob(monkeypatch):
    # no mobs -> needs deplete_reads consecutive empty reads before DEPLETED (debounced)
    df, _ = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0, 0, 0])
    assert recovery.approach_shoot(10, df, None, deplete_reads=2, verbose=False) is recovery.DEPLETED


def test_not_depleted_on_single_empty_frame(monkeypatch):
    # a single empty frame must NOT declare depleted (flaky detection guard)
    df, _ = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0, 0, 999])
    # only one loop iteration (one empty read) then time runs out -> True, not DEPLETED
    assert recovery.approach_shoot(10, df, None, deplete_reads=4, verbose=False) is True


def test_off_platform_mob_ignored_then_depleted(monkeypatch):
    # a mob far below the player's feet (different platform) -> not same-platform
    mob = [(0.7, 1200, 900, 60, 40)]                 # bottom=940 vs feet 500 -> |440|>band
    df, _ = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0, 0])
    assert recovery.approach_shoot(10, df, None, band=70, verbose=False,
                                   deplete_reads=2) is recovery.DEPLETED


def test_walks_right_toward_far_mob(monkeypatch):
    mob = [(0.7, 1200, 480, 60, 40)]                 # cx=1230, bottom=520 ~ feet 500 -> same
    df, pressed = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, None, attack_range=110, verbose=False)
    assert Key.right in pressed and 'c' in pressed   # dx=+430 -> step right while firing


def test_walks_left_toward_far_mob(monkeypatch):
    mob = [(0.7, 100, 480, 60, 40)]                  # cx=130, far left of player 800
    df, pressed = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, None, attack_range=110, verbose=False)
    assert Key.left in pressed and 'c' in pressed


def test_attacks_in_place_when_in_range(monkeypatch):
    mob = [(0.7, 800, 480, 60, 40)]                  # cx=830, dx=30 <= range 110
    df, pressed = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, None, attack_range=110, verbose=False)
    assert 'c' in pressed                            # fires


def test_blind_attack_when_anchor_lost(monkeypatch):
    mob = [(0.7, 800, 480, 60, 40)]
    df, pressed = _setup(monkeypatch, mobs=mob, ptuple=None, clock_vals=[0, 0])
    recovery.approach_shoot(10, df, None, verbose=False)
    assert 'c' in pressed                            # no anchor -> still attacks
