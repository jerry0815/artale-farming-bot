"""Extracted from core.py (behavior unchanged). Functions call the engine as core.<name>;
core.py re-exports them so callers/tests keep using core.<name> and monkeypatch still binds."""
import core


def walk_shoot(node_mm_x, seconds, detect_fn, attack_key='c', half=60, tol=(3, 3),
               walk_step=0.25, deplete_reads=2, verbose=True, label=""):
    """Sweep a platform end-to-end firing -- NO screen player-anchor (minimap only, so it's
    robust to the damage-number/monster-bar anchor mess). Walks between [center-half,
    center+half] on the MINIMAP x, stopping to fire an attack burst each step (the attack
    roots the char anyway). Depletion = detect_fn finds no mob in the central screen band
    for `deplete_reads` consecutive sweep-ends. Returns DEPLETED / True (beat done) / False."""
    left, right = max(0, node_mm_x - half), node_mm_x + half
    target = right                                        # sweep toward the right first
    t0, empty_ends, last_log = core.time.time(), 0, [0.0]

    def log(msg):
        if verbose and core.time.time() - last_log[0] > 0.6:
            last_log[0] = core.time.time(); print(f"[walk {label}] " + msg)

    def burst(n=2):
        for _ in range(n):
            if core.kb.pause:
                return
            core.kb.safe_press(attack_key); core.time.sleep(0.3); core.kb.safe_release(attack_key)

    def central_clear():
        f = core.capture()
        if f is None:
            return False
        H, W = f.shape[:2]
        band = (int(W * 0.12), int(H * 0.34), int(W * 0.88), int(H * 0.80))
        return len(detect_fn(f, band)) == 0

    try:
        while core.time.time() - t0 < seconds:
            if core.kb.pause or core.STOP.is_set():
                return False
            if not core.focus_ok_or_bail(label or "walk"):   # focus lost mid-sweep -> bail; caller re-seats
                return False
            core.lie_check_fast_tick()
            x, _y = core.stable_char(2)                         # minimap x (robust)
            if x < 0:                                      # no read -> fire in place, retry
                burst(1); continue
            if abs(x - target) <= tol[0] + 3:              # reached an end -> fire + reverse
                burst(2)
                target = left if target == right else right
                if central_clear():                        # depletion check at the sweep end
                    empty_ends += 1
                    log(f"sweep end clear ({empty_ends}/{deplete_reads})")
                    if empty_ends >= deplete_reads:
                        return core.DEPLETED
                else:
                    empty_ends = 0
                continue
            key = core.Key.right if target > x else core.Key.left    # step toward the end, then fire
            log(f"mmx={x} -> {'right' if target > x else 'left'} to {target}")
            core.kb.safe_press(key); core.time.sleep(walk_step); core.kb.safe_release(key)
            burst(1)
        return True
    finally:
        core.kb.safe_release_all()


def approach_shoot(seconds, detect_fn, anchor,
                   attack_range=110, band=70, step=0.14, attack_key='c',
                   deplete_reads=4, stall_limit=8, verbose=True, label="",
                   mm_bounds=None, mm_y=None, fall_margin=22, fall_check_every=0.9,
                   attack_keys=None, priority_class=None, priority_range=None,
                   priority_hold_hits=0, priority_lock_grace=0, fall_confirm=2, ease_margin=45,
                   continuous_attack=False, attack_dwell=0.25, face_deadzone=15,
                   face_settle=0.2, fire_stall_limit=5, lock_switch_px=70, combined=None,
                   lost_limit=12, collect_misses=False, fail_dump_interval=3.0,
                   band_up=None, band_down=None):
    """Close-range farming for a beat: repeatedly locate the player (HP-bar anchor) and
    the nearest SAME-PLATFORM mob (its box-bottom near the player's feet), walk toward it
    (facing it) and attack; fire in place once within `attack_range` px. Returns DEPLETED
    when no mob is on the platform, True after the beat, False if paused.

    `detect_fn(frame, roi) -> [(score, x, y, w, h)]` is the detector (YOLO or template) --
    approach is detector-agnostic. Screen-space: mob x and player x come from the same
    frame, so 'which way to walk' is their x difference. `band` = how close a mob's feet
    must be to the player's feet (px) to count as the same platform."""
    import player as _player
    # Asymmetric same-platform band: default both directions to the symmetric `band`, but a node
    # can tighten one side (e.g. P2 caps `band_up` so mobs on the platform ABOVE -- P1 -- aren't
    # picked up). `band_up` = max px a mob's feet may sit ABOVE her feet; `band_down` = below.
    band_up = band if band_up is None else band_up
    band_down = band if band_down is None else band_down
    t0 = core.time.time()
    empty_reads = 0
    nonempty_streak = 0     # consecutive frames WITH a same-platform mob (debounces deplete reset)
    best_absdx = None       # closest we've gotten to the current target (net-progress stall)
    no_improve = 0
    last_player = None      # sticky anchor: lock onto the bar nearest last frame's player
    _lost = 0               # consecutive beats the anchor found NOTHING -> re-seat past lost_limit
    # Miss-frame collection is OFF by default (set collect_miss_frames in the map to gather a
    # retrain batch). When off, skip the yolo-fail dump entirely -- no anchor resolution, no writes.
    _yolo_primary = core._yolo_primary_anchor(anchor) if collect_misses else None
    pri_lock_pos = None     # last screen pos of the engaged priority target (fishhouse)
    pri_miss = 0            # consecutive reads the priority target has been missing (occlusion debounce)
    pri_engaged = None      # x of the fishhouse last hit -> post-kill hold fires when it DISAPPEARS
    _prev_tx = None         # last chosen target x -> flag target SWITCHES in the debug trace
    rooted = False          # True only while firing in place -> the skill roots her, so a lost
                            # HP bar means she hasn't moved (assume last pos). While WALKING she
                            # IS moving, so a stale pos would overshoot + false-stall -> never fake it.
    held = [None]           # currently-held walk key -> HELD across frames for smooth motion
    _ha = [None]            # currently-held ATTACK key (continuous_attack) -> released on a move
    _faced = [None]         # direction she's currently facing -> a side change is a new decision
    _fire_stall = 0         # consecutive in-range beats on the SAME target that isn't dying
    _last_fire_tx = None    # target x of the last in-range beat (to detect a persistent target)
    last_log = [0.0]
    last_fall = [t0]        # throttle the minimap fall check (stable_char is not free)
    fall_streak = 0         # consecutive out-of-band fall reads (debounce: bottom-edge phantoms)

    def log(msg):
        if verbose and core.time.time() - last_log[0] > 0.6:
            last_log[0] = core.time.time()
            print(f"[approach {label}] " + msg if label else "[approach] " + msg)

    def stop_walk():
        if held[0] is not None:
            core.kb.safe_release(held[0]); held[0] = None

    def hold_attack(k):
        """Continuous attack: HOLD the key down (the game repeats the skill while held), only
        (re)pressing on a key change -- so attack uptime stays high across detection reads instead
        of the press/release burst's gaps. Released by release_attack() on a non-attack decision."""
        if _ha[0] != k:
            if _ha[0] is not None:
                core.kb.safe_release(_ha[0])
            core.kb.safe_press(k); _ha[0] = k

    def release_attack():
        if _ha[0] is not None:
            core.kb.safe_release(_ha[0]); _ha[0] = None

    def walk(key):
        """Hold `key` continuously (only (re)press on a direction change) so motion doesn't
        stutter between detection frames."""
        if held[0] != key:
            if held[0] is not None:
                core.kb.safe_release(held[0])
            core.kb.safe_press(key); held[0] = key
        _faced[0] = key                       # walking that way faces her that way

    try:
        while core.time.time() - t0 < seconds:
            if core.kb.pause:
                return False
            if not core.focus_ok_or_bail(label or "attack"):   # focus lost mid-attack -> bail; farm_node
                return False                                # re-seats and resumes (no key leak)
            core.lie_check_fast_tick()          # unified safety scan: lie-check + another-player
            # FALL check: she can be knocked (or walk) OFF the platform mid-beat; the screen-space
            # anchor keeps "farming" the platform below. Read her minimap y (robust, densest-cluster
            # so a phantom can't trip it) and if she has dropped well past the platform band, bail so
            # the caller RE-SEATS her instead of farming the wrong platform.
            if mm_y is not None and core.time.time() - last_fall[0] >= fall_check_every:
                last_fall[0] = core.time.time()
                _mx, _my = core.stable_char(2)
                if _my >= 0 and _my > mm_y + fall_margin:
                    # DEBOUNCE: a lone out-of-band read is usually a bottom-edge minimap phantom
                    # (or a jump/knock arc), not a real fall -- re-seating on it wrecks stacked
                    # platforms like P3. Only bail after `fall_confirm` consecutive confirmations.
                    fall_streak += 1
                    if fall_streak >= fall_confirm:
                        log(f"FELL off platform (y={_my} > {mm_y}+{fall_margin}, x{fall_streak}) -> re-seat")
                        core.kb.safe_release_all()
                        return core.FELL
                    log(f"fall read {fall_streak}/{fall_confirm} (y={_my}) -- confirming before re-seat")
                else:
                    fall_streak = 0                            # back in band -> reset the streak
            f = core.capture()
            if f is None:
                core.time.sleep(0.1); continue
            _shared_dets = None
            if combined is not None:                      # shared single pass: mobs + player box
                _shared_dets, _pbox = combined(f, anchor.last)   # ONE inference this beat...
                anchor.push(_pbox)                        # ...its player box feeds the anchor (falls
                #                                            back to nametag when None, as before)
            p = anchor.locate(f)                          # HP-bar/name-tag anchor
            if _yolo_primary is not None and not _yolo_primary.last_had_box:
                # TRUE recall failure this beat: YOLO returned no class-2 box (regardless of whether
                # the nametag then covered it). Dump it (throttled) to build a real retrain set --
                # this is the sample kind we found we DIDN'T have (past dumps were guard rejections).
                core._dump_yolo_fail(f, label, interval=fail_dump_interval)
            if p is None:
                _lost += 1
                if _lost >= lost_limit:                   # anchor blind for too long (YOLO HP-bar +
                    # nametag both failing) -> she'd otherwise blind-attack to the 90s cap. Re-seat
                    # instead, and SAVE the frame so the missed HP bar can be labeled + retrained.
                    if collect_misses:
                        core._dump_player_miss(f, label)
                    log(f"player lost {_lost} beats (anchor can't find her) -> re-seat")
                    core.kb.safe_release_all(); return core.LOST
                if last_player is None:                   # never locked yet -> brief blind attack
                    log("player NOT found (no prior lock) -> blind attack")
                    stop_walk()
                    core.kb.safe_press(attack_key); core.time.sleep(0.3); core.kb.safe_release(attack_key); continue
                if not rooted:                            # she was WALKING (moving): her last pos is
                    # already stale -- faking it overshoots the mob and trips the stall guard. Stop
                    # and wait a beat for the bar to re-appear (no skill VFX while walking, so brief).
                    log("player lost mid-walk -> stop, wait for re-lock")
                    stop_walk(); core.time.sleep(0.03); continue
                # Rooted: the attack SKILL's VFX hides the bar but also holds her still -- she hasn't
                # moved -- so assume her last position and keep firing.
                p = last_player
                log(f"player NOT found (rooted) -> assume last position {p}")
            else:
                _lost = 0                                 # a real lock -> reset the lost streak
            last_player = p
            if getattr(anchor, "which", None):            # composite fell past the primary anchor
                log(f"anchor fallback #{anchor.which} (nametag) located player at {p}")
            px, pfeet = p
            # Scan the FULL platform-width strip at the player's y-band (YOLO is cheap on the
            # whole frame). Catches far mobs (P6's rightmost fishhouse) without any patrol.
            H, W = f.shape[:2]
            # scan a strip that spans the full same-platform band both ways (+margins), so a
            # wider band actually reaches mobs above AND below her feet.
            strip = (0, max(0, pfeet - max(band, band_up) - 40), W, min(H, pfeet + band_down // 2 + 40))

            def same_platform(dets):
                # A detection is (score,x,y,w,h) or (score,x,y,w,h,cls_name) -> carry the class
                # (or None) through so the attack key can be chosen per mob. `d[:5]` handles both.
                # dy<0 = mob ABOVE her feet (upper platform); gate each side by its own band.
                out = []
                for d in dets:
                    _s, mx, my, mw, mh = d[:5]
                    dy = (my + mh) - pfeet
                    if -band_up <= dy <= band_down:
                        out.append((mx + mw // 2, my + mh, d[5] if len(d) > 5 else None))
                return out

            dets = _shared_dets if _shared_dets is not None else detect_fn(f, strip)
            same = same_platform(dets)
            core.STATUS["count"] = len(same)                    # live same-platform mob count for the panel
            if core.dbg_on():                                   # full per-frame trace (file only)
                _md = [(round(d[0], 2), d[1] + d[3] // 2, d[2] + d[4]) for d in dets]
                core.dbg(f"[app {label}] player=({px},{pfeet}) band={band} "
                    f"dets(score,cx,feet)={_md} same_platform={[(m[0], m[2]) for m in same]}")
            # DEATH-TRIGGERED post-kill hold: if the fishhouse we were just hitting is GONE now
            # (killed -- reliable at ~0.99 recall), blanket its spot for priority_hold_hits to catch
            # the goby that spawn there. Fires ONCE at the kill, not on every attack beat.
            if pri_engaged is not None and priority_hold_hits and priority_class and not continuous_attack:
                if not any(m[2] == priority_class and abs(m[0] - pri_engaged) <= band for m in same):
                    log(f"fishhouse killed (x~{pri_engaged}) -> post-kill hold +{priority_hold_hits}")
                    stop_walk()
                    for _ in range(priority_hold_hits):
                        if core.kb.pause:
                            break
                        core.kb.safe_press(attack_key); core.time.sleep(0.35); core.kb.safe_release(attack_key)
                        core.time.sleep(0.05)
                pri_engaged = None
            # Locked fishhouse occluded mid-kill (our own AoE VFX / a passing bone fish hides the WHOLE
            # platform): DON'T count it as depletion -- that abandons the fishhouse she's killing (the
            # #1 "gives up while attacking" case). Bridge it via the priority hold below (fires the lock
            # spot). Only genuine, unlocked emptiness advances the deplete counter.
            _lock_bridge = (priority_class and pri_lock_pos is not None and pri_miss < priority_lock_grace)
            if not same and not _lock_bridge:
                stop_walk(); release_attack(); rooted = False   # platform empty -> stop attacking too
                nonempty_streak = 0
                empty_reads += 1                          # DEBOUNCED: several empty frames -> clear
                feet = [d[2] + d[4] for d in dets]
                log(f"no same-platform mob ({empty_reads}/{deplete_reads}); "
                    f"pfeet={pfeet} band={band} detected feet={feet}")
                if empty_reads >= deplete_reads:          # debounced empty -> platform done
                    return core.DEPLETED
                core.time.sleep(0.12); continue
            # Only a SUSTAINED detection (>=2 consecutive frames) clears deplete progress -- a lone
            # flaky frame no longer resets the tail, so a near-empty platform depletes sooner.
            nonempty_streak += 1
            if nonempty_streak >= 2:
                empty_reads = 0
            # Target priority: prefer the priority_class (the fishhouse that SPAWNS the goby
            # burst) so she stands ON it when it dies -- the self-centered AoE then catches the
            # 6 goby the instant they pop, stacked, instead of chasing them one-by-one as they
            # scatter. No priority mob present -> nearest same-platform mob.
            pri = [m for m in same if m[2] == priority_class] if priority_class else []
            holding = False
            if pri:                                        # priority target visible -> commit to ONE
                # Pick the fishhouse nearest the EXISTING lock, not nearest px -- the player anchor
                # jumps hundreds of px between reads, and keying off px made "nearest" flip between
                # fishhouses so she abandoned the one she was killing. Fishhouses are stationary, so
                # matching to the lock keeps her on the same physical target until it dies.
                ref = pri_lock_pos[0] if pri_lock_pos is not None else px
                cand = min(pri, key=lambda m: abs(m[0] - ref))
                if (pri_lock_pos is not None and abs(cand[0] - ref) > lock_switch_px
                        and pri_miss < priority_lock_grace):
                    # The locked fishhouse isn't in this frame and the nearest one is a DIFFERENT,
                    # far fishhouse (P3 has two ~930px apart; the model sees one at a time). Don't
                    # oscillate to it -- HOLD the locked one (walk to it, it's stationary) until it
                    # re-appears/dies, else she thrashes back and forth across the platform.
                    tx, _tfy = pri_lock_pos; tcls = priority_class; holding = True
                else:
                    tx, _tfy, tcls = cand
                    pri_lock_pos = (tx, _tfy); pri_miss = 0
            elif (priority_class and pri_lock_pos is not None and pri_miss < priority_lock_grace):
                # Priority target BLINKED OUT (a bone fish swam through / our own AoE VFX) -- not
                # necessarily dead. Stay committed to its last position instead of switching to a
                # goby and leaving the fishhouse half-killed. The grace only ticks down while she's
                # IN RANGE firing the spot (below) -- an occlusion while still WALKING in hasn't
                # confirmed anything, so it must not burn the grace before she lands a hit. Once she
                # HAS been firing the spot and it still doesn't reappear, it's dead -> lock clears
                # and the goby it spawned become the target.
                tx, _tfy = pri_lock_pos; tcls = priority_class; holding = True
            else:
                pri_lock_pos = None                        # no lock (or gave up) -> nearest same-platform
                tx, _tfy, tcls = min(same, key=lambda m: abs(m[0] - px))
            # TARGET trace (file only): why she picked this target, and flag SWITCHES so target
            # changes are diagnosable. mode=lock (fishhouse, nearest the lock), hold (occluded
            # fishhouse), nearest (goby / no fishhouse). fh=candidate fishhouse x's.
            if core.dbg_on():
                _mode = "lock" if pri else ("hold" if holding else "nearest")
                _fh_xs = sorted(int(m[0]) for m in pri)
                _sw = "" if (_prev_tx is None or abs(tx - _prev_tx) <= 20) else f" SWITCH<-{_prev_tx}"
                core.dbg(f"[tgt {label}] mode={_mode} tx={int(tx)} cls={tcls} px={px} "
                    f"lock={None if pri_lock_pos is None else int(pri_lock_pos[0])} miss={pri_miss} "
                    f"fh={_fh_xs} goby={sum(1 for m in same if m[2] != priority_class)}{_sw}")
            _prev_tx = tx
            dx = tx - px
            key = core.Key.right if dx >= 0 else core.Key.left
            akey = (attack_keys or {}).get(tcls, attack_key)   # per-mob skill; falls back to default
            # Tighter close-in on the priority target: get point-blank on the fishhouse so the
            # goby spawn inside the AoE, not at its (wider) normal firing standoff.
            _rng = priority_range if (priority_range and tcls == priority_class) else attack_range
            if abs(dx) <= _rng:                           # in range: face + fire a burst
                if holding:                                # occluded AND firing the spot -> real evidence
                    pri_miss += 1                          # it's gone; count only in-range misses
                    log(f"priority occluded ({pri_miss}/{priority_lock_grace}) -> hold+fire last pos {tx}")
                best_absdx = None; no_improve = 0
                rooted = True                             # firing in place -> assume-last-pos is valid
                log(f"IN RANGE dx={dx} px={px} -> attack '{akey}' ({tcls}, same={len(same)})")
                stop_walk()
                if continuous_attack:
                    # A SIDE change is a different decision: if the mob is now on the opposite side
                    # of where she faces, drop the held attack (so the turn registers -- a facing tap
                    # is swallowed while the attack key is down), face the new side, then resume
                    # holding. Otherwise keep the attack held. A deadzone ignores dx jitter near 0.
                    if _faced[0] != key and abs(dx) > face_deadzone:
                        # A facing tap is SWALLOWED while the attack animation is still playing (she
                        # keeps attacking the old side). Drop attack, let the cast clear (face_settle),
                        # THEN turn with a firm tap, then resume. Without this she never actually turns.
                        release_attack()
                        core.time.sleep(face_settle)
                        core.kb.safe_press(key); core.time.sleep(0.12); core.kb.safe_release(key); _faced[0] = key
                    elif _faced[0] is None:
                        core.kb.safe_press(key); core.time.sleep(0.12); core.kb.safe_release(key); _faced[0] = key
                    hold_attack(akey)                     # HOLD -> keeps attacking through re-detect
                    core.time.sleep(attack_dwell)              # short dwell, then re-decide (still held)
                    # FIRE-STALL: the same in-range target persisting (not dying) means the turn was
                    # swallowed again OR it's out of reach -> force a fresh re-face next beat.
                    if _last_fire_tx is not None and abs(tx - _last_fire_tx) <= 30:
                        _fire_stall += 1
                    else:
                        _fire_stall = 0
                    _last_fire_tx = tx
                    if _fire_stall >= fire_stall_limit:
                        log(f"fire-stall at dx={dx} (target not dying) -> force re-face")
                        _faced[0] = None; _fire_stall = 0
                else:
                    core.kb.safe_press(key); core.time.sleep(0.03); core.kb.safe_release(key)   # face the target
                    for _ in range(3):                    # burst: several hits before re-evaluating
                        core.kb.safe_press(akey); core.time.sleep(0.35); core.kb.safe_release(akey)
                        core.time.sleep(0.05)
                if priority_class and tcls == priority_class:
                    pri_engaged = tx        # remember the fishhouse we hit; hold fires when it DIES
            else:                                         # nearest visible mob is OUT of range
                release_attack()                          # moving now -> the skill roots her, drop attack
                # NET-progress stall: give up (advance) only if we stop getting CLOSER (best
                # |dx| not improving) for stall_limit frames -- tolerates jitter + slow approach,
                # and escapes a genuinely unreachable mob so the platform can't hang.
                if best_absdx is None or abs(dx) < best_absdx - 4:
                    best_absdx = abs(dx); no_improve = 0
                else:
                    no_improve += 1
                if no_improve >= stall_limit:
                    log(f"stalled at dx={dx} (best={best_absdx}, unreachable) -> advance")
                    return core.DEPLETED
                # WALK ONLY -- do NOT attack while approaching: the attack skill roots her in
                # place, so firing mid-walk cancels her movement and she never closes in.
                # MINIMAP BOUND: navigation stays on the minimap. Never step OFF the platform
                # even if the (noisy) anchor says so -- fire in place at the edge instead. This
                # stops a bad anchor from walking her off-platform and breaking the next swim_to.
                if mm_bounds is not None:
                    mmx, _mmy = core.stable_char(2)
                    if mmx >= 0 and ((key == core.Key.right and mmx >= mm_bounds[1]) or
                                     (key == core.Key.left and mmx <= mm_bounds[0])):
                        log(f"platform edge (mmx={mmx} bounds={mm_bounds}) -> fire in place")
                        stop_walk(); rooted = True         # firing in place at the edge
                        core.kb.safe_press(akey); core.time.sleep(0.3); core.kb.safe_release(akey)
                        continue
                # CONTINUOUS walk: hold the key across frames (only re-press on a turn), so
                # motion is smooth instead of stutter-stepping between detections.
                log(f"dx={dx} px={px} -> walk {'right' if dx > 0 else 'left'} "
                    f"(same={len(same)}, best={best_absdx}, noimp={no_improve})")
                rooted = False                            # moving now -> a lost bar must NOT be faked
                # EASE-IN: once she's about to arrive, TAP-and-release instead of holding. The
                # detection read is ~0.7s, so a held key keeps her swimming through that whole read
                # and she COASTS PAST a tight target (then has to turn back -- the overshoot). A
                # short nudge can't overshoot far, and she re-reads from near a standstill.
                if abs(dx) <= _rng + ease_margin:
                    core.kb.safe_press(key); core.time.sleep(0.05); core.kb.safe_release(key); held[0] = None; _faced[0] = key
                else:
                    walk(key)                             # far -> hold continuously for smooth travel
                    if step > 0:                          # 0 = no pacing, run at compute speed
                        core.time.sleep(step)
        return True
    finally:
        release_attack()                          # drop any held (continuous) attack key
        core.kb.safe_release(attack_key)
        core.kb.safe_release(core.Key.left); core.kb.safe_release(core.Key.right)


def _dump_player_miss(frame, label="", interval=20.0):
    """Save a frame where the player anchor failed, to retrain the mob YOLO's player (HP-bar)
    class. Label them later with:  python label_mobs.py --grab-dir datasets/player_misses
    Throttled (one per `interval`s) so a stuck stretch can't flood; never raises."""
    now = core.time.time()
    if frame is None or now - core._last_player_miss[0] < interval:
        return
    core._last_player_miss[0] = now
    try:
        core.os.makedirs(core._PLAYER_MISS_DIR, exist_ok=True)
        ts = core.time.strftime("%Y%m%d_%H%M%S", core.time.localtime()) + f"_{int((now % 1) * 1000):03d}"
        path = core.os.path.join(core._PLAYER_MISS_DIR, f"{ts}_{label or 'miss'}.png")
        core.cv2.imwrite(path, frame)
        print(f"[player-miss] saved {path} (label -> retrain player class)")
    except Exception as e:
        print(f"[player-miss] dump failed: {e}")


def _dump_yolo_fail(frame, label="", interval=3.0, dup_thresh=6.0):
    """Save a frame where the YOLO player anchor returned ZERO class-2 boxes -- a TRUE recall
    failure (the HP bar was there but the model didn't fire), as opposed to _dump_player_miss
    which triggers on the LOST sentinel and, as it turned out, mostly caught GUARD REJECTIONS
    (YOLO did detect, the max_jump veto threw it out). This dump fires per-beat whenever YOLO
    saw nothing -- even when the nametag then covered it -- so we build a real recall-failure
    set to retrain on. Label with:  python label_mobs.py --grab-dir datasets/player_yolo_fail

    Two filters keep the set useful, not bloated: `interval` throttles the rate, and `dup_thresh`
    skips a frame that's near-identical (mean abs gray diff < thresh) to the last SAVED one -- so
    a stuck same-scene stretch adds VARIETY, not 200 copies of one burst. Never raises."""
    now = core.time.time()
    if frame is None or now - core._last_yolo_fail[0] < interval:
        return
    small = None
    try:
        small = core.cv2.resize(core.cv2.cvtColor(frame, core.cv2.COLOR_BGR2GRAY), (64, 36))
        if core._last_fail_small[0] is not None and \
                float(core.np.mean(core.cv2.absdiff(small, core._last_fail_small[0]))) < dup_thresh:
            return                                   # same scene as the last save -> skip (variety)
    except Exception:
        small = None
    core._last_yolo_fail[0] = now
    try:
        core.os.makedirs(core._YOLO_FAIL_DIR, exist_ok=True)
        ts = core.time.strftime("%Y%m%d_%H%M%S", core.time.localtime()) + f"_{int((now % 1) * 1000):03d}"
        path = core.os.path.join(core._YOLO_FAIL_DIR, f"{ts}_{label or 'fail'}.png")
        core.cv2.imwrite(path, frame)
        if small is not None:
            core._last_fail_small[0] = small
        print(f"[yolo-fail] saved {path} (YOLO returned no player box -> label to retrain recall)")
    except Exception as e:
        print(f"[yolo-fail] dump failed: {e}")


def _yolo_primary_anchor(anchor):
    """The YoloPlayerAnchor inside `anchor` (the composite's primary), or `anchor` itself when it
    IS one (no nametag fallback configured), or None. Detected by the .last_had_box flag it sets
    each locate() -- True when YOLO produced a class-2 box this beat, False when it saw none."""
    if hasattr(anchor, "last_had_box"):
        return anchor
    inner = getattr(anchor, "anchors", None)
    return inner[0] if inner and hasattr(inner[0], "last_had_box") else None


def _reactive_deplete(low_streak, count, threshold, debounce):
    """Debounce for the in-shoot count. A single-frame `count` below `threshold`
    extends `low_streak`; `debounce` consecutive lows -> rotate. Any healthy count,
    or a failed read (count is None), resets the streak. Returns (streak, rotate?)."""
    if count is not None and count < threshold:
        low_streak += 1
        return low_streak, low_streak >= debounce
    return 0, False


def _stand_shoot(node, seconds, count_fn=None, threshold=2, check_every=0.5, debounce=2):
    """STAND_SHOOT state: parked at home, fire in place for a beat. Returns False if
    she fell off the platform (walks back to home if knocked toward the right edge).

    If `count_fn` is given (a fast single-frame YOLO count) it is polled every
    `check_every`s while the attack is HELD; `debounce` consecutive reads below
    `threshold` return DEPLETED so the caller rotates immediately. The attack key
    stays held across the count capture, so counting never interrupts firing."""
    c = core.FARM_CTX[node]
    core.walk_to_x(c["home"], tol=2)                        # reposition to the LEFT home...
    core._face_right(0.04)                                 # ...facing right (short tap = minimal drift off home)
    t0, last = core.time.time(), (c["home"], c["home_y"])
    next_check, low = t0 + check_every, 0
    while core.time.time() - t0 < seconds:
        if core.kb.pause:
            break
        if not core.focus_ok_or_bail("dragon"):     # focus lost mid-stand -> release + refocus, end beat
            break
        core.kb.safe_press('c'); core.time.sleep(0.1)           # HOLD attack (continuous, not tapped)
        r = core._plausible_read(last)                     # rejects buff-glow phantom reads
        if r is not None:
            last = r
            if r[1] >= c["fall_y"]:
                core.kb.safe_release_all(); print(f"[stand] fell to {r}"); return False
            if r[0] >= c["edge"]:                      # knocked past the RIGHT edge -> walk left home
                core.kb.safe_release('c')
                if not core._walk_shoot(core.Key.left, c["home"], going_right=False,
                                   seed=(c["edge"], c["home_y"]), cap=c["cap"], fall_y=c["fall_y"]):
                    return False
                core._face_right(); last = (c["home"], c["home_y"])
            elif r[0] <= c["left"]:                     # knocked past the LEFT boundary -> walk right home
                print(f"[stand] {node}: left-guard fired @ x={r[0]} (<= {c['left']}) -> walk right to {c['home']}")
                core.kb.safe_release('c')
                if not core._walk_shoot(core.Key.right, c["home"], going_right=True,
                                   seed=(c["left"], c["home_y"]), cap=c["cap"],
                                   fall_y=c["fall_y"], edge_safety=c["edge"]):
                    return False
                core._face_right(); last = (c["home"], c["home_y"])
        if count_fn is not None and core.time.time() >= next_check:
            next_check = core.time.time() + check_every
            low, rotate = core._reactive_deplete(low, count_fn(), threshold, debounce)
            if rotate:
                core.kb.safe_release_all(); print(f"[stand] {node} depleted -> rotate"); return core.DEPLETED
        core.lie_check_fast_tick()      # cheap transparent-shape check while shooting (3s window)
    core.kb.safe_release_all()
    return True


def _walk_shoot_sweep(node):
    """WALK_SHOOT state: sweep right to x_far shooting, then back left to home, then
    face right. Returns False if she fell."""
    c = core.FARM_CTX[node]
    if not core._walk_shoot(core.Key.right, c["far"], going_right=True, seed=(c["home"], c["home_y"]),
                       cap=c["cap"], fall_y=c["fall_y"], edge_safety=c["edge"]):
        return False
    if not core._walk_shoot(core.Key.left, c["home"], going_right=False, seed=(c["far"], c["home_y"]),
                       cap=c["cap"], fall_y=c["fall_y"]):
        return False
    core._face_right()
    return True
