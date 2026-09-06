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
    monkeypatch.setattr(recovery, "lie_check_full_tick", lambda *a, **k: None)
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


class _DetectSeq:                                    # detector returning a scripted per-frame list
    def __init__(self, seq):
        self.seq, self.i = list(seq), 0

    def __call__(self, f, roi):
        v = self.seq[self.i] if self.i < len(self.seq) else self.seq[-1]
        self.i += 1
        return v


def test_lone_flaky_detection_does_not_reset_deplete(monkeypatch):
    # A single flaky same-platform detection between empty frames must NOT reset the deplete
    # counter (needs >=2 consecutive) -- so a near-empty platform still depletes on schedule.
    mob = (0.7, 1200, 480, 60, 40)                   # same-platform but far (out of range)
    df, _, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0] * 12)
    df = _DetectSeq([[], [mob], [], []])             # empty, ONE mob, empty, empty
    # empty(1) -> mob(streak=1, no reset) -> empty(2) -> empty(3) -> DEPLETED at reads=3
    assert recovery.approach_shoot(10, df, anc, deplete_reads=3, attack_range=110,
                                   verbose=False) is recovery.DEPLETED


def test_sustained_detection_resets_deplete(monkeypatch):
    # Two consecutive detections DO clear deplete progress -> she keeps farming, not leaves.
    mob = (0.7, 1200, 480, 60, 40)
    df, _, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0, 0, 0, 0, 999])
    df = _DetectSeq([[], [mob], [mob], []])          # empty(1) -> mob -> mob(streak=2 -> reset)
    # after reset only one trailing empty (reads=1 < 3) then time runs out -> True, not DEPLETED
    assert recovery.approach_shoot(10, df, anc, deplete_reads=3, attack_range=110,
                                   verbose=False) is not recovery.DEPLETED


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


def test_ease_in_nudges_toward_near_target(monkeypatch):
    # A mob just OUTSIDE range but within the ease-in margin -> she nudges toward it (correct
    # direction, no fire) via the tap-release ease-in path instead of coasting past.
    mob = [(0.9, 910, 490, 40, 30)]                  # cx=930, dx=130: out of range 110, within +45
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, attack_range=110, ease_margin=45, verbose=False)
    assert Key.right in pressed and 'c' not in pressed   # eased toward it, did not fire


def test_attacks_in_place_when_in_range(monkeypatch):
    mob = [(0.7, 800, 480, 60, 40)]                  # cx=830, dx=30 <= range 110
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, attack_range=110, verbose=False)
    assert 'c' in pressed                            # fires


def test_continuous_attack_holds_key_across_beats(monkeypatch):
    # continuous_attack=True: HOLD the attack key -- pressed ONCE and kept down across beats,
    # not re-pressed 3x/beat like the burst. (safe_press only fires on a key change.)
    mob = [(0.9, 810, 490, 40, 30)]                   # in range dx~30
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0, 0, 999])
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            continuous_attack=True, verbose=False)
    assert pressed.count("x") == 1                    # held across 2 beats, not 6 burst presses


def test_burst_attack_presses_each_hit(monkeypatch):
    # Default (burst) still presses per hit -> more than one 'x' in a beat.
    mob = [(0.9, 810, 490, 40, 30)]
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x", verbose=False)
    assert pressed.count("x") >= 3                    # burst = several presses


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


def test_assumes_last_position_when_rooted_and_bar_hidden(monkeypatch):
    # In range (rooted, firing) the skill VFX hides the HP bar (anchor -> None). Because she's
    # rooted she hasn't moved -> assume last position and keep FIRING, not abandon the mob.
    mob = [(0.7, 800, 480, 60, 40)]                  # cx=830, dx=30 <= range -> in range, rooted
    df, pressed, _ = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0] * 8)
    anc = _AnchorSeq([(800, 500), None, None])        # lock (fires, roots), then bar hidden
    recovery.approach_shoot(10, df, anc, attack_range=110, verbose=False)
    assert 'c' in pressed                            # kept firing from the assumed last pos


def test_lost_mid_walk_does_not_false_stall(monkeypatch):
    # Anchor blips out WHILE walking (not rooted): her last pos is stale, so we must NOT freeze
    # it (that overshoots + trips the stall guard). She stops and waits -> no premature DEPLETED.
    mob = [(0.7, 1200, 480, 60, 40)]                 # far right, out of range -> walking
    df, pressed, _ = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0] * 20)
    anc = _AnchorSeq([(800, 500)] + [None] * 15)      # lock once, then lost for many frames
    r = recovery.approach_shoot(10, df, anc, attack_range=110, stall_limit=3, verbose=False)
    assert r is not recovery.DEPLETED                # stopped & waited; no fabricated stall
    assert 'c' not in pressed                        # never blind-attacked (she was walking)


def test_minimap_bound_fires_in_place_at_platform_edge(monkeypatch):
    # Mob far to the right (would step right), but she's at the right minimap bound ->
    # must FIRE IN PLACE, not walk off the platform.
    import numpy as np
    mob = [(0.9, 1600, 480, 60, 40)]                 # cx=1630, dx large, out of range
    monkeypatch.setattr(recovery, "capture", lambda: np.zeros((1000, 1600, 3), np.uint8))
    monkeypatch.setattr(recovery, "lie_check_fast_tick", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "lie_check_full_tick", lambda *a, **k: None)
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


def test_fall_off_platform_returns_FELL(monkeypatch):
    # She's knocked off P2 (mm_y=104) down to P3 (y~136): SUSTAINED out-of-band reads (>= fall_confirm)
    # make the fall check return FELL so the caller re-seats, instead of farming the platform below.
    df, _, anc = _setup(monkeypatch, mobs=[(0.9, 810, 500, 40, 30)], ptuple=(800, 500),
                        clock_vals=[0] * 8)
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (100, 136))   # fell to y=136
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: None)
    out = recovery.approach_shoot(10, df, anc, mm_y=104, fall_margin=22, fall_confirm=2,
                                  fall_check_every=0.0, verbose=False)
    assert out is recovery.FELL


def test_single_fall_read_debounced(monkeypatch):
    # A LONE out-of-band read (a bottom-edge minimap phantom / jump arc) must NOT trigger FELL --
    # otherwise one bad read costs a full re-seat (the P3 churn). Needs fall_confirm consecutive reads.
    df, _, anc = _setup(monkeypatch, mobs=[(0.9, 810, 500, 40, 30)], ptuple=(800, 500),
                        clock_vals=[0, 0, 999])                 # one fall check, then time out
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (100, 368))   # phantom at map bottom
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: None)
    out = recovery.approach_shoot(10, df, anc, mm_y=136, fall_margin=22, fall_confirm=2,
                                  fall_check_every=0.0, verbose=False)
    assert out is not recovery.FELL                            # single read debounced -> no re-seat


def test_fall_check_disabled_when_mm_y_none(monkeypatch):
    # mm_y=None disables the fall check entirely (how a no_fall_nodes platform like P3 is farmed):
    # even a wildly out-of-band read never returns FELL.
    df, _, anc = _setup(monkeypatch, mobs=[(0.9, 810, 500, 40, 30)], ptuple=(800, 500),
                        clock_vals=[0, 0, 999])
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (100, 368))   # bottom-edge phantom
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: None)
    out = recovery.approach_shoot(10, df, anc, mm_y=None, fall_check_every=0.0, verbose=False)
    assert out is not recovery.FELL                            # no fall check -> never bails


def test_no_fall_when_on_platform(monkeypatch):
    # Still on the platform (y within the band): no FELL -- the fall check must not false-trigger.
    df, _, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0, 0, 999])
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (100, 108))   # y within band of 104
    monkeypatch.setattr(recovery.kb, "safe_release_all", lambda: None)
    out = recovery.approach_shoot(10, df, anc, mm_y=104, fall_margin=22,
                                  fall_check_every=0.0, deplete_reads=4, verbose=False)
    assert out is not recovery.FELL


def test_attack_key_selected_by_mob_class(monkeypatch):
    # A goby in range -> the burst uses the class-mapped key 'z', not the default 'c'.
    mob = [(0.9, 810, 490, 40, 30, "goby")]          # 6-tuple with class; feet=520 near feet 500
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0, 999])
    recovery.approach_shoot(10, df, anc, attack_range=90, attack_key="c",
                            attack_keys={"goby": "z"}, verbose=False)
    assert "z" in pressed and "c" not in pressed


def test_attack_key_falls_back_to_default_for_unmapped_class(monkeypatch):
    # A fishhouse (not in attack_keys) in range -> uses the default 'c'.
    mob = [(0.9, 810, 490, 40, 30, "fishhouse")]
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0, 999])
    recovery.approach_shoot(10, df, anc, attack_range=90, attack_key="c",
                            attack_keys={"goby": "z"}, verbose=False)
    assert "c" in pressed and "z" not in pressed


def test_5tuple_detector_uses_default_key(monkeypatch):
    # A plain 5-tuple detection (template detector, no class) still works -> default key.
    mob = [(0.9, 810, 490, 40, 30)]
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0, 999])
    recovery.approach_shoot(10, df, anc, attack_range=90, attack_key="c", verbose=False)
    assert "c" in pressed


def test_priority_class_targets_fishhouse_over_nearer_goby(monkeypatch):
    # A goby sits point-blank (in range) and a fishhouse far right (out of range). WITHOUT
    # priority she'd fire at the goby; WITH priority_class='fishhouse' she walks toward the
    # fishhouse instead -- it SPAWNS the goby burst, so being on it when it dies is the win.
    mobs = [(0.9, 830, 490, 40, 30, "goby"),          # cx=850, dx~50 in range
            (0.9, 1280, 490, 40, 30, "fishhouse")]    # cx=1300, dx~500 out of range
    df, pressed, anc = _setup(monkeypatch, mobs=mobs, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"goby": "x", "fishhouse": "x"},
                            priority_class="fishhouse", verbose=False)
    assert Key.right in pressed and "x" not in pressed   # walked toward fishhouse, didn't fire goby


def test_priority_class_falls_back_to_nearest_when_absent(monkeypatch):
    # No fishhouse present -> priority has nothing to prefer -> nearest goby, fire in place.
    mobs = [(0.9, 810, 490, 40, 30, "goby")]          # dx~30 in range
    df, pressed, anc = _setup(monkeypatch, mobs=mobs, ptuple=(800, 500), clock_vals=[0, 0, 999])
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"goby": "x", "fishhouse": "x"},
                            priority_class="fishhouse", verbose=False)
    assert "x" in pressed                              # fired the goby (no fishhouse to prefer)


def test_priority_range_closes_more_on_priority_target(monkeypatch):
    # A fishhouse within the normal attack_range but beyond the tighter priority_range ->
    # she WALKS closer instead of firing from afar, so the self-centered AoE lands on the
    # spot where the 6 goby will spawn.
    mobs = [(0.9, 880, 490, 40, 30, "fishhouse")]     # cx=900, dx~100: < range 110 but > priority 60
    df, pressed, anc = _setup(monkeypatch, mobs=mobs, ptuple=(800, 500), clock_vals=[0, 0])
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"fishhouse": "x"}, priority_class="fishhouse",
                            priority_range=60, verbose=False)
    assert Key.right in pressed and "x" not in pressed   # closed in, did not fire from afar


def test_post_kill_hold_fires_when_fishhouse_dies(monkeypatch):
    # DEATH-TRIGGERED: burst on the fishhouse (frame 1), then when it's GONE next read (killed) the
    # hold fires to blanket the goby spot -- NOT on the same beat as the burst.
    fh = [(0.9, 795, 490, 40, 30, "fishhouse")]       # in range
    df, pressed, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0] * 8)
    df = _DetectSeq([fh, []])                          # hit the fishhouse, then it's gone (killed)
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"fishhouse": "x"}, priority_class="fishhouse",
                            priority_hold_hits=2, verbose=False)
    assert pressed.count("x") >= 5                     # burst(3) + post-kill hold(2)


def test_no_post_kill_hold_while_fishhouse_alive(monkeypatch):
    # Fishhouse stays present across beats -> only bursts, NO early hold (the "triggers too early"
    # complaint). Each in-range beat is exactly 3 hits; two beats = 6, not 3+hold.
    mob = [(0.9, 795, 490, 40, 30, "fishhouse")]
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0, 0, 999])
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"fishhouse": "x"}, priority_class="fishhouse",
                            priority_hold_hits=2, verbose=False)
    assert pressed.count("x") == 6                     # two bursts, no hold (fishhouse never died)


def test_no_priority_hold_for_nonpriority_target(monkeypatch):
    # A goby (not the priority class) in range -> only the normal 3-hit burst, no hold.
    mob = [(0.9, 810, 490, 40, 30, "goby")]
    df, pressed, anc = _setup(monkeypatch, mobs=mob, ptuple=(800, 500), clock_vals=[0, 0, 999])
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"goby": "x", "fishhouse": "x"},
                            priority_class="fishhouse", priority_hold_hits=3, verbose=False)
    assert pressed.count("x") <= 3                     # no post-kill hold on a goby


def test_priority_lock_holds_through_occlusion(monkeypatch):
    # Frame 1: fishhouse point-blank (engage). Frames 2+: a bone fish swam through -> fishhouse
    # NOT detected, only a FAR goby (out of range -> would need a WALK to reach). With grace she
    # HOLDS on the fishhouse's last spot and keeps FIRING in place (each fire beat = 3 'x'), so
    # across the engage + several occluded frames she racks up many hits instead of walking off.
    fh = (0.9, 795, 490, 40, 30, "fishhouse")         # cx=815, dx~15 in range
    goby_far = (0.9, 1285, 490, 40, 30, "goby")       # cx=1305, dx~505 far -> a walk, not a fire
    df, pressed, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0] * 8)
    df = _DetectSeq([[fh, goby_far]] + [[goby_far]] * 7)
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"fishhouse": "x", "goby": "x"},
                            priority_class="fishhouse", priority_lock_grace=3, verbose=False)
    assert pressed.count("x") >= 9                     # kept firing through multiple occluded frames


def test_priority_lock_gives_up_after_grace(monkeypatch):
    # Same setup, grace=1: engage (fire) + ONE occluded hold (fire) = 6 'x', then she concludes
    # the fishhouse is dead and pursues the far goby (walks, no more firing) so the platform clears.
    fh = (0.9, 795, 490, 40, 30, "fishhouse")
    goby_far = (0.9, 1285, 490, 40, 30, "goby")
    df, pressed, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0] * 8)
    df = _DetectSeq([[fh, goby_far]] + [[goby_far]] * 7)
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"fishhouse": "x", "goby": "x"},
                            priority_class="fishhouse", priority_lock_grace=1, verbose=False)
    assert pressed.count("x") == 6                     # engage + 1 hold, then walked (stopped firing)
    assert Key.right in pressed                        # pursued the goby after giving up


def test_priority_locks_to_one_fishhouse_despite_anchor_jump(monkeypatch):
    # Two fishhouses; the player anchor JUMPS from near A to near B between reads (the real bug:
    # noisy yolo_player/nametag px). Targeting must stay committed to the fishhouse she locked (A),
    # not flip to whatever is 'nearest' the jumped px (B) and abandon A half-killed.
    A = (0.9, 795, 490, 40, 30, "fishhouse")          # cx=815
    B = (0.9, 1385, 490, 40, 30, "fishhouse")         # cx=1405
    df, pressed, _ = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0, 0, 0, 999])
    df = _DetectSeq([[A, B], [A, B]])
    anc = _AnchorSeq([(800, 500), (1390, 500)])       # frame 2: anchor jumps to near B
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"fishhouse": "x"}, priority_class="fishhouse",
                            priority_range=60, verbose=False)
    # frame 1 locks A (near, fires). frame 2 anchor jumps to B, but she stays on A -> walks LEFT
    # back toward A instead of firing B. Without the lock she'd have fired B (nearest px).
    assert Key.left in pressed


def test_priority_lock_survives_full_occlusion_no_deplete(monkeypatch):
    # She's mid-kill on a fishhouse when her attack VFX (or a bone fish) hides the WHOLE platform.
    # Those empty reads must NOT count toward depletion (which abandons the fishhouse she's killing --
    # the #1 'gives up while attacking' case); she holds and keeps firing the lock spot instead.
    fh = (0.9, 795, 490, 40, 30, "fishhouse")
    df, pressed, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0] * 12)
    df = _DetectSeq([[fh], [], [], []])               # engage, then fully occluded reads
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"fishhouse": "x"}, priority_class="fishhouse",
                            priority_range=60, priority_lock_grace=3, deplete_reads=2, verbose=False)
    assert pressed.count("x") >= 9                     # kept firing through occlusion, not 3-then-deplete


def test_priority_lock_not_burned_while_approaching(monkeypatch):
    # Fishhouse locked but occluded WHILE she's still walking toward it (out of range), with a
    # goby now point-blank. The grace must NOT tick down while she's merely approaching (she has
    # not landed a hit yet) -- she keeps walking to the fishhouse spot instead of giving up and
    # firing the near goby, so a full-HP occluded fishhouse isn't abandoned unhit. grace=1 here
    # would give up immediately if the counter ran while walking.
    fh_far = (0.9, 1285, 490, 40, 30, "fishhouse")    # dx~505 -> out of range, she walks
    goby_near = (0.9, 810, 490, 40, 30, "goby")       # dx~30 in range (the tempting switch)
    df, pressed, anc = _setup(monkeypatch, mobs=[], ptuple=(800, 500), clock_vals=[0] * 6)
    df = _DetectSeq([[fh_far, goby_near]] + [[goby_near]] * 5)
    recovery.approach_shoot(10, df, anc, attack_range=110, attack_key="x",
                            attack_keys={"fishhouse": "x", "goby": "x"},
                            priority_class="fishhouse", priority_lock_grace=1,
                            stall_limit=8, verbose=False)
    assert Key.right in pressed and "x" not in pressed  # kept approaching the fishhouse, never fired the goby
