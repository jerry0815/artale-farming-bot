# Handoff: Make the dragon farming loop more human-like

**Status:** IMPLEMENTED (2026-08-09). §5 + §6 built and unit-tested. Remaining: rope
calibration + a live tuning pass (see §9 below). Rope-hang is coded but OFF by default.
**Date:** 2026-08-09
**Working dir for implementation:** `C:\jerry\toy_work\maple\auto_train` (the full code lives here)

> Note: the earlier session was opened in `C:\jerry\maple\auto_train`, which only had a 7 KB
> stub `train_2026.ipynb`. The real, full version is here in `toy_work`. Use the files in THIS
> folder as the source of truth.

---

## 1. Objective

Make the `blue_dragon_loop` (the one the user mainly runs) behave less like a bot and more
like a human. Goal chosen by the user: **BOTH** statistical unpredictability (defeat pattern-based
anti-cheat) **AND** believable-to-a-human-watcher behavior (a GM or nearby player should see
something that looks like a real person). User accepted **HIGH realism**, explicitly trading away
farming efficiency (breaks, idle, fatigue, occasional rope-hang) for believability.

Build shared human-like helpers so `egg_dragon_loop` benefits too, but `blue_dragon_loop` is the
priority.

---

## 2. Game / character / map context

- Game: **MapleStory Worlds - Artale**. Window title in code: `MapleStory Worlds-Artale (?????)`.
- Character: **Lulala**, Lv.123, **箭神 (Bowmaster / archer)** → attacks are **ranged** (`c`).
- Map: **隱密之地 / 藍翼龍巢穴** (Hidden Place / Blue-winged Dragon's Nest).
- Current farming behavior: stand on the **top long platform**, patrol **left ↔ right**, shoot `c`
  at the blue dragons. Because attacks are ranged, the character normally never leaves the platform.
- Patrol bounds already in code (minimap coords from `get_character()`):
  `CHARACTER_Y = 91`, `CHARACTER_X_L = 66`, `CHARACTER_X_R = 161`.
- There is a **rope hanging down from the farming platform** (user highlighted it in a screenshot,
  roughly center of the platform). It is reachable directly from the patrol platform.

### Key bindings (confirmed with user)
| Key | Action |
|-----|--------|
| `c` | attack (ranged) |
| `h` | heal / buff (pressed each loop) |
| `a` | skill (currently every 300s) |
| `Left` / `Right` arrows | walk |
| **LeftAlt (`Key.alt_l`)** | **Jump** (was NOT previously defined in code — new) |
| `F8` | toggle pause (existing `on_press` listener) |

---

## 3. Existing code map (in this folder)

- `train_2026.ipynb` — main notebook. Contains `blue_dragon_loop()`, `egg_dragon_loop()`,
  helpers: `safe_press/release/release_all`, `wait_with_pause`, `click_press`,
  `attack_during_walk`, `get_character`, `get_exp`, `get_enemy`, `goto_freemarket`, `show_maple`,
  `capture_window_screenshot`, and the `on_press` F8 pause listener.
- `auto_train.py` (33 KB) — full script version. Cross-check helper definitions here too.
- `detection.py` — minimap/template detection (`detect_character_on_minimap`, `detect_red_dots`).
- `exp_processor.py` — `ExpProcessor.process_exp_value` reads EXP OCR from a screen crop.
- `keyboard.py`, `ocr_processor.py`, `training.py`, `assets/`, `debug_output/`.

### Existing game-logic that MUST be preserved (do not break)
- **EXP check:** every cycle reads recent exp; if `recent_exp_gain < 4000` after 180s → `goto_freemarket()`.
- **Red-dot escape:** if `get_enemy()` (other players on minimap) returns any → `goto_freemarket()`.
- **`goto_freemarket()`** is the user's existing "panic / get to safety" behavior (pauses, waits 22s,
  clicks fixed screen coords to go to Free Market). Reuse it as the last-resort recovery.
- **F8 pause** via global `pause` and `wait_with_pause()`.
- Patrol **bounds** logic (`CHARACTER_X_L/R`, `CHARACTER_Y`).

---

## 4. What's currently robotic (the targets)

- Fixed `click_press('c', duration=3)` every cycle.
- `attack_during_walk` uses a perfect **0.05s press / 0.05s release** metronome.
- Hardcoded sleeps everywhere: `0.05`, `0.1`, `0.5`, `duration=3`, `walk_time+0.25`.
- `a` skill on a rigid **300s** timer; `h` pressed every single loop at the same point.
- Pure deterministic left→right ping-pong. Only `walk_time` has some randomness
  (`random.randint(20,30)/10` for blue, `40-50/10` for egg).

---

## 5. Agreed design — human-like layer (timing/rhythm over existing patrol)

Implement as a **thin timing-and-rhythm layer**; do NOT invent new navigation except the opt-in
rope-hang below. All of this is gated behind a `HUMAN` config dict at the top of the notebook so
the user can tune / disable pieces.

1. **Jittered timing primitive** — `human_sleep(base, spread=...)` returning a gaussian-jittered,
   never-negative delay. Replace every hardcoded sleep and key-hold duration with it (the 3s `c`
   hold, 0.05 attack ticks, 0.1 taps, 0.5 waits).
2. **Irregular attack rhythm** — in `attack_during_walk`, vary each shot's press/gap per swing,
   with occasional longer gaps (human hesitation), instead of the fixed 0.05/0.05.
3. **Patrol variety (within bounds only)** — randomize each leg's walk time (extend what's there),
   add occasional brief stops at turnarounds and rare mid-patrol pauses. **No new movement keys;
   stay inside `CHARACTER_X_L..R`.**
4. **Micro-pauses** — low-probability short idle (0.3–2s) standing still between legs.
5. **Breaks + fatigue** — every ~8–15 min (jittered) take a 30s–2min break (release all keys,
   stand idle OR rope-hang, see below). Over a session, slowly grow reaction times and break
   frequency to mimic tiring.
6. **Jittered skill/heal cadence** — `a` every **260–340s** (jittered) instead of exactly 300;
   vary where in the loop `h` fires.
7. **Preserve all game logic** from §3.

---

## 6. Agreed design — rope-hang break (opt-in, DEFENSIVE)

During a scheduled break, *sometimes* park on the rope (very human AFK tell), *sometimes* just
stand idle. Build as one self-contained routine, e.g. `rope_hang_break()`.

### Exact rope mechanic (confirmed with user)
- **Grab:** align x over the rope → hold **Down** + tap **Jump (LeftAlt)** to down-jump off the
  platform → **then tap Down AGAIN** to latch onto the rope while falling.
  *(User note: "you need to press down again after down+jump".)*
- **Exit:** hold **Up** to climb up the rope → then **Jump (LeftAlt)** to hop back onto the platform.
  *(User note: "climb up and jump".)*

### Routine steps (all gated / reversible)
1. **Align** — tap Left/Right using `get_character()` minimap x as feedback until within a tight
   tolerance of the rope's minimap x. Cap the attempts.
2. **Bail safely** — if it can't line up in N tries, do NOTHING risky: fall back to a normal
   stand-still break. Never blind down-jump off the platform.
3. **Grab** — Down + Alt, short fall, tap Down. Then **verify via minimap** that the grab worked
   (minimap y increased, x steady).
4. **Grab failed** → immediately run exit-recover (climb Up + Jump) to get back on the platform;
   if position still looks wrong → `goto_freemarket()` panic escape.
5. **Hang** — release keys, dangle for the break duration.
6. **Return** — climb Up, Jump onto platform, confirm x back within `CHARACTER_X_L..R`, resume.

### Calibration helper (build this)
`calibrate_minimap_x()` — prints current `get_character()` x so the user stands on the rope once
and captures its exact minimap x into a config constant `ROPE_MINIMAP_X`. No eyeballing pixel coords.

### Tunable constants to expose (best-guess, WILL need live tuning)
- `ROPE_MINIMAP_X` (from calibration)
- `ROPE_ALIGN_TOLERANCE` (minimap px)
- `ROPE_ALIGN_MAX_ATTEMPTS`
- `ROPE_FALL_TO_REGRAB_DELAY` (delay between down-jump and the second Down tap)
- `ROPE_CLIMB_DURATION` (how long to hold Up to reach the top)
- rope-hang probability per break, hang duration range

---

## 7. Open items / what the next session must do

1. **Read the real code** in this folder first (`train_2026.ipynb`, and `auto_train.py` to confirm
   helper signatures — the stub in `C:\jerry\maple` may differ). Confirm `get_character()` returns
   `(x, y)` minimap coords and how `get_enemy()` / `get_exp()` behave.
2. **Implement** §5 + §6 behind a `HUMAN` config dict. Extract shared helpers so both loops use them.
3. **Calibration:** have the user run `calibrate_minimap_x()` on the rope to get `ROPE_MINIMAP_X`.
4. **Live tuning session (expected):** the rope timing constants (fall-to-regrab, climb duration)
   are guesses; the first few grabs may mis-latch. User has ACCEPTED this and agreed to tune together.
   Everything is gated so a mis-grab recovers rather than dies — but plan a tuning pass.
5. **Optional, OFF by default (user must opt in):** jump-in-place idle, arrow-glancing.
   ⚠️ Do NOT auto-enable "look up" — pressing `Up` near a rope/ladder can grab it unintentionally.

### Screenshot / self-test capability (this machine)
- Python with all deps confirmed working: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe`
  (also 3.13). Deps OK: `mss, cv2, win32gui, pyautogui, pygetwindow, screeninfo, PIL, numpy`.
- The next session CAN do **read-only** screen capture (via `capture_window_screenshot`) to find the
  rope's minimap x and the character position for calibration — useful, do this instead of guessing.
- ⚠️ Do **NOT** send key inputs to the live game without asking the user each time — that moves their
  real character. Read-only capture is fine; actuating input (testing the grab) needs per-action OK.

---

## 8. Safety principles (carry into implementation)

- Idle / mid-patrol pause = just stop = always safe.
- Never introduce new directional movement that could walk off the platform. Only the opt-in
  rope-hang leaves the platform, and it's gated on alignment + verified + recoverable.
- Reuse `goto_freemarket()` as the last-resort "I'm lost, get to safety" fallback.
- Keep EXP-check, red-dot escape, F8 pause, and bounds logic intact.

---

## 9. Implementation notes (what was actually built — 2026-08-09)

**Where:** a single new notebook cell in `train_2026.ipynb` (cell id `human-layer`, sits
between the definitions cell and the run cell). It **redefines** `blue_dragon_loop()` /
`egg_dragon_loop()` so they *shadow* the originals — nothing above was deleted, so the old
behavior is one cell-skip away. Everything is also gated behind a `HUMAN` config dict; set
`HUMAN["enabled"] = False` to fall straight back to the original deterministic loops.

**Built (all in the `human-layer` cell):**
- `HUMAN` config dict — every tunable from §5/§6 lives here.
- Timing primitives: `human_sleep(base, ..., fatigue=False)`, `human_hold(key, base)`,
  `_reaction_wait(range)` — all gaussian-jittered, never-negative, F8-pausable.
- `human_attack_during_walk` — per-shot varied press/gap, occasional hesitation, rare
  mid-leg stop. Falls back to the original `attack_during_walk` when `HUMAN["enabled"]` is off.
- `maybe_turn_pause`, `maybe_micro_pause` — §5.3/§5.4 patrol variety (within bounds only).
- Breaks + fatigue (§5.5): `take_human_break`, `_schedule_next_break`, `_fatigue_factor`
  (reaction times grow and breaks come *sooner* over a session), `_idle_wait`.
- Jittered skill/heal cadence (§5.6): `a` now fires every `skill_interval_range` (260–340s).
- Rope-hang (§6): `rope_align`, `rope_grab`, `rope_exit`, `rope_hang_break` (aligns → grabs
  → **verifies via minimap** → hangs → returns; bails to idle if it can't align, and
  `goto_freemarket()` only as last resort if it ends up off-platform), plus
  `calibrate_minimap_x()`.
- All preserved game logic (EXP check, red-dot escape, F8 pause, patrol bounds) is intact.

**Unit-tested (pure logic, stubbed input — no live game):** jitter non-negativity &
centering, small-base clamp, fatigue growth, "tired → break sooner", attack-rhythm shot
generation, disabled-fallback delegation, rope-align convergence, and every
`rope_hang_break` branch (happy path, safe bail on grab-fail, panic-escape when lost).
Test lives in scratchpad; it does **not** import the live game.

### Live session addendum (2026-08-09, part 2): calibration + fall recovery

Ran live with the user. Findings and new capabilities:

**Environment facts learned**
- Display is **150% DPI**: `GetWindowRect` = 1294x757 (logical) but screen-grab returns
  1941x1135 (physical). Detection uses the physical image; the window was NOT resized.
- I **can self-focus** the game (no user click) via `AttachThreadInput` + alt-nudge +
  `SetForegroundWindow` — verified with `GetForegroundWindow`. In `recovery.py: focus()`.
- I **can self-screenshot** and visually interpret the scene.
- The bot's minimap crop is only a **band** (y 171..316); a character off the patrol
  platform falls outside it. Recovery uses a **taller crop** (y 171..430) — see
  `recovery.py: MM_H=259`, same offset so all existing calibration stays valid.

**Rope-hang breaks: ABANDON on this map.** Not a code bug — the rope sits in the dragon
spawn, so the *approach* (walking to align, then stopping to grab) takes a contact hit and
knocks the squishy bowmaster off. The grab/hang/exit mechanics themselves work. Keep
`rope_enabled=False`, `rope_hang_prob=0`; breaks = stand-still idle on the platform.

**Calibrated constants (full-minimap frame, offset 20,171):**
- rope x = **91**, rope y-band **99 (top) .. 129 (bottom)**
- farming platform: y ~**83-101** across its x-span (x 64→ up), patrol x 66..161
- left fallen platform ≈ **(77,110)**, right fallen platform ≈ **(120,131)**
- `PLATFORM_Y=101`, `ROPE_EXIT_TOP_Y=92`, `ROPE_GRAB_MIN_DROP=10`, `ROPE_GRAB_X_TOL=12`

**NEW: autonomous fall recovery — `recovery.py` (works, verified live both sides).**
`recover_to_farming()`: full-minimap sense → if fallen, safely step to rope x=91 (aborts
on a real y-jump = edge/fall, double-confirmed against spurious reads) → climb → up-jump
onto the farming platform → retry up to 3x → bail with keys released. Both the left and
right fallen platforms recover via the SAME rope (right just climbs from the rope bottom).
Helper/experiment scripts in `auto_train/`: `recovery.py` (the module), `calibrate_live.py`,
`climb_jump.py`, `full_sense.py`, `map_platforms.py` (auto platform-detect returns 0 here —
minimap is detailed terrain, not schematic), `trail_log.py` (records a manual path trail —
used to map the right-side route). `map_platforms`/`trail_log` are read-only.

Spurious detection note: an occasional false character match at **(122,188)** appears
(same value repeatedly). `stable_char()` (median of 3) + double-confirm filter it.

### Live session addendum (2026-08-09, part 3): color detection + safe break-drop

**Detection breakthrough — use COLOR, not template.** The yellow-dot template match
(`detect_character_on_minimap`) is fragile: returns a persistent phantom **(122,188)** at
some positions (a fixed yellow minimap feature) and false-matches during golden skill/buff
glows. Fix: **color-based detection** — HSV bright-yellow blob + connected components,
pick the plausible blob (size 3..400 px; nearest to last pos if several). Clean and stable
where the template failed. `recovery.py: get_character_color()`, and `get_character_full()`
now uses color first, template (th=0.7) as fallback. This fixed all the recovery flakiness.

**Level thresholds (color frame):** farming platform reads y ~76-90, left fallen y ~99-110
(idle animation bobs y ~10px). Boundary: `FARMING_Y_MAX=95`, `FALLEN_Y_MIN=96` (no dead
zone). Decisions use `stable_char(5)` (median of 5) to smooth the bob.

**NEW: safe human break via down-jump — `break_cycle()` (verified end-to-end).**
Instead of the abandoned rope-hang, the break drops to the *fallen platform* (below, AWAY
from dragons — HP even regenerates there) and recovers back:
1. `drop_to_fallen()` — walk to the narrow drop-through gap at **x=67** (x=64 & x=76 are
   SOLID — the gap is ~x=67, found via `trail_log`), down-jump straight down to the left
   fallen platform (y~109). Verifies via color sense; bails (stays on farming) if not on
   the farming platform or the drop didn't register.
2. idle on the fallen platform for the break duration (safe).
3. `recover_to_farming()` — walk to rope x=91, climb, up-jump back to farming.
Run standalone: `python recovery.py break <seconds>` (also `recover`, `drop`, `sense`,
`focus`). Recovery hardened: fall-abort confirmed by a STABLE median read (transient glow
reads lie); a walk abort retries the round instead of bailing; up to 5 rounds.

`DROP_X=67`, and recovery constants as in part 2. `trail_log.py` now uses color sensing.

### Live session addendum (2026-08-09, part 4): continuous movement + robustness

- **Recovery flakiness (intermittent)**: a dragon can hit her during the climb-up (the rope
  passes back through the dragon level). Retry budget raised `max_rounds=5 -> 8`. A failed
  recovery leaves her on the SAFE fallen platform (idle, never dying).
- **Continuous movement**: `walk_to_x` rewritten to HOLD the direction key and poll position
  (~30Hz) for one smooth walk, then ease in with a couple of small taps near the target
  (needed for the narrow x=67 drop gap). Previously it was tap-sense-tap, which "looked like a
  script." Fall-abort still active during the hold (release + stable-read confirm). The
  notebook farming patrol was already continuous (holds walk key while attacking).
- Possible further work: attack (`c`) while climbing to thin dragons by the rope; longer
  stress test for a reliability number.

### Live session addendum (2026-08-09, part 5): integrated loop

- `recovery.py: farming_loop(exp_check, enemy_check, panic, break_every, rest_range,
  skill_interval, max_seconds)` — the integrated human-like loop: humanized farm stints
  (continuous patrol+shoot, heal each stint, skill on jittered 260-340s), a jittered break
  every 8-15 min (drop to fallen platform -> rest -> recover), auto-recover if knocked off,
  and injected safety callbacks. `max_seconds` bounds it for testing. Uses `kb` (keyboard.py)
  + `kb.pause` (F8) as the SINGLE keyboard system.
- Notebook cell `integrated-loop` (inserted after `human-layer`, before the old run cell):
  wires `farming_loop` to the notebook's `get_exp`/`get_enemy` (screen reads, no keys) and a
  kb-based Free Market escape. RUN THIS CELL instead of the old run cell; F8 to start/pause.
- Bounded standalone test: `python recovery.py loop 50` (short break interval so a full
  break happens in the window).
- **FULL RUN (CLI, production): `python recovery.py run`** — starts PAUSED, F8 to begin.
  Wires the humanized farm + jittered 8-15min breaks + auto-recovery + the ported safety
  checks (`get_exp` EXP-stuck -> Free Market after 180s; `get_enemy` red-dot -> Free Market).
  `get_exp`/`get_enemy`/`capture_pil_np` were ported into recovery.py (faithful to the
  notebook; the width/height offsets normalize the 150%-DPI grab 1942x1136 -> 1920x1080).
  Safety reads verified live 2026-08-10 (get_exp read the bar, get_enemy=0). The notebook
  `integrated-loop` cell is the equivalent Jupyter path.
- Farm is now ORIGIN-STYLE: parked at left home (x~74) shooting in place, periodic
  right(x~112)->back-left sweep, `_face_right()` tap on return, `RIGHT_EDGE_SAFETY=128`
  forces a left-return if knocked toward the ~x140 edge. Attack cadence: c 0.1s/0.1s.
  Recover region widened to x 55-152 (right fallen platform reads up to ~x148).
- **NOT yet live-tested end-to-end**: the game window was closed at integration time
  (`FindWindow` returned 0). All building blocks (farm, drop, recover, `demo_sequence`) WERE
  verified live earlier this session; only the composed `farming_loop` + the notebook cell's
  EXP/enemy/panic wiring still need one live run. Do that first next session.
- Two-keyboard-system pitfall (important): the notebook cell-1 helpers and `keyboard.py`
  each have their OWN pynput Controller + `pause`. The integration standardizes on
  `keyboard.py` (`kb`) everywhere and a kb-based panic so keys can't get stuck. Do NOT mix
  the notebook's cell-1 `safe_press`/`goto_freemarket` with `farming_loop`.

### Live session addendum (2026-08-10): bottom farming platform + buff-glow robustness

**Map is THREE levels, all connected by the same rope (x=91):**
- Top farming platform: park x~74, y~85, dragons to the right
- Rest platform (break spot): x~77, y~110
- **Bottom farming platform: x~63, y~143, dragons to the right** (NEW)

**Built & verified live in recovery.py:**
- `go_to_bottom()` — top -> (down-jump x67) rest -> (down-jump) bottom. Verified (settles ~143;
  verify uses BOTTOM_Y_MIN=132 after a settle delay, since mid-landing reads ~133).
- `farm_bottom()` — park-left/sweep-right like the top but bottom y-context
  (`BOTTOM_FALL_Y=152`, home x=63, sweep to x~90, cap 2.2s). CLI: `python recovery.py farmbottom 20`.
- Recovery from the bottom is FREE via the existing rope logic: widened `RECOVER_Y_MAX=148`
  and `ROPE_CLIMB_MAX=6.0` (bottom->top is a longer climb, y143->85). Verified.
- CLI: `gobottom`, `farmbottom`.

**Buff-glow phantom problem (IMPORTANT, systemic):** the golden skill-buff glow overlays the
minimap and spawns MULTIPLE phantom yellow blobs in the corners (seen at ~(22,232),(2,242),
(36,153),(28,235)...). `get_character_color()`/`stable_char()` then pick a phantom some frames
-> false "fell" reads. Mitigation added: `_plausible_read(last)` rejects implausible jumps
(>45px) and is used in `farm`/`farm_bottom` stand phases and `_walk_shoot` (seeded from home).
This made bottom farming survive the glow. **STILL VULNERABLE:** standalone decision reads
(`stable_char` in go_to_bottom/recover/farming_loop) can be fooled during a HEAVY glow. Next
robustness step: make `get_character_color`/`stable_char` isolate the real character dot from
the glow (tighter yellow HSV and/or blob-size filter ~40-90px, and/or cluster-mode over more
samples). This benefits the WHOLE system, not just bottom farming.

**Bottom->top rope grab FIX (2026-08-10):** from the bottom platform the rope's grabbable
base (y~136) is ABOVE the floor (y~143), so pressing Up alone does NOT grab -- must
**jump+up** (Up held + a Jump tap) to catch the rope, THEN climb. `climb_and_jump()` now does
this hop-grab when `y0 >= BOTTOM_Y_MIN` (from the rest platform, y~110, the rope passes through
her level so Up alone still works). Also: recover now aligns to `ROPE_X+1, tol=1` (the grab
zone is ~1px wide) and `climb_and_jump()` returns whether it ACTUALLY climbed (so recover
re-aligns/retries instead of falsely "succeeding"). Bottom->top verified: (91,136) -> jump+up
-> (96,90) top, 2 rounds. Detection hardened: tighter yellow mask (sat/val >=165, size 15-120)
+ cluster-based `stable_char` to reject buff-glow phantoms (effectiveness vs the heat-aura glow
unconfirmed until it recurs; verified it doesn't break clean detection).

Note: she gets knocked from the top down to the rest platform when left IDLE among dragons
between manual test commands -- not a bug; the loop attacks continuously + auto-recovers.

**Down-jump CASCADE fix (2026-08-10):** a down-jump was holding Down ~0.24s, so landing on
the one-way rest platform mid-hold got read as a SECOND down-jump -> cascaded top->bottom
(two levels) unpredictably. Fix: release Down IMMEDIATELY after the jump (`_down_jump` and
`drop_to_fallen` now hold Down ~0.1s). Top->rest now lands on the rest platform reliably
(3/3 + rotation steps); an occasional cascade still slips through (self-corrects via recovery).
`rest_to_bottom()` added = one down-jump rest->bottom. Full rotation verified end-to-end:
left fallen -> top -> left fallen -> bottom -> top -> left fallen (all correct levels).

**Bottom-climb continuity (2026-08-10): known-rough, NOT solved.** The bottom->top rope grab
needs a precise x (~91, a ~1px zone) AND jump+up. The precise-stop approach
(`walk_to_x(ROPE_X+1, tol=1)` then `climb_and_jump`) WORKS but takes 2-5 retries (overshoots
to x=95, "Up didn't climb", re-align) -> looks steppy, not continuous. A "walk into the rope
+ jump+up on the fly" continuous attempt (`_climb_from_bottom_continuous`, still in the file
but NOT wired) was WORSE: detection reaction-lag makes her overshoot the rope onto the RIGHT
fallen platform and oscillate. Root cause = narrow-target precision + ~40ms detection lag; same
class of problem as the narrow x=67 drop gap (`drop_to_fallen` is also intermittent). Left as:
reliable-but-steppy. A real fix likely needs faster/predictive control or a wider grab
approach; revisit as a focused task. UPDATE 2026-08-10: tried 3 de-steppy fixes -- continuous
walk-into-rope (`_climb_from_bottom_continuous`), a y-guard settle (nudge-left until y~143),
and a tight x-align (`_align_to_rope`, still in file, unused). ALL regressed (failed or hung)
and were reverted. Root cause confirmed: grab zone x=91-92 (~1-2px) vs ~40ms sensor lag +
~3-4px tap granularity -> can't stop precisely, overshoots to ~95, retries until it lands.
Working version = `walk_to_x(ROPE_X+1, tol=1)` + `climb_and_jump()` with re-align-retry
(reaches top in ~5-8 rounds, always succeeds). Leave steppy; do NOT keep re-attempting without
a fundamentally different approach (e.g. predictive stop that leads the target by the lag).
MID-CLIMB STUTTER FIXED: the user's "steppy" = the CLIMB itself stopping ~3x mid-rope, not the
align retries. Cause: `climb_and_jump`'s stall-check released Up on a noisy "not rising" frame
mid-climb, then recovery re-pressed Up -> stutter (long bottom climb hit this; short upper rope
finished first). Fix: track `climbing` (y risen >=6px from start) -> once climbing, HOLD Up
straight to the top and ignore transient stalls; only give up if she NEVER grabbed (y flat for
~6 frames). Verified: bottom climb now goes 136->85 in ONE continuous motion.
STILL UNSOLVED (separate): the initial grab alignment -- walk_to_x overshoots the ~1-2px rope
grab-zone to x~95, so 1-2 "Up didn't climb -> re-align" rounds before she's on the rope. Sensor
lag vs narrow target; do not keep blindly re-attempting. `top->left fallen` = `drop_to_fallen` (works, intermittent
at the x=67 gap).

**SPLIT WIRED (2026-08-10): `farming_loop_split()`** — farm top a stint -> `go_to_bottom` ->
farm bottom a stint -> `recover_to_farming` (bottom->top) -> repeat. Jittered breaks (drop to
the left fallen platform + rest), heal each stint, skill on jittered cadence, EXP/red-dot
safety, auto-recovery, F8 pause. CLI: `python recovery.py runsplit` (full, starts paused) or
`python recovery.py split 90` (bounded test, short stints, no break). Bounded test verified:
alternates top<->bottom, self-corrects a misfired down-jump / steppy climb. `run` = top-only
(unchanged). Tunables: top_secs, bottom_secs, break_every, rest_range, skill_interval.
Remaining roughness (non-fatal, self-correcting): occasional down-jump misfire (go_to_bottom
retries next stint) and steppy bottom->top rope-grab retries.

**Lower-ledge recovery + Free Market disabled (2026-08-10):**
- The bottom area has TWO close ledges: bottom farming (~y143) and a LOWER ledge (~y150) she
  sometimes drops to. The rope does NOT reach the lower ledge. Recovery now: if `y >=
  LOWER_LEDGE_Y (147)`, tap Jump to hop UP onto the bottom platform (straight-up jump works --
  the ledges are aligned), then the next round does the normal rope climb. `RECOVER_Y_MAX`
  raised 148->156 and `BOTTOM_FALL_Y` 152->160 so the lower ledge is recoverable, not a bail.
  Verified: (63,150) -> jump up -> (63,143) -> climb -> top -> home. (Took 2 hops; first
  jump's landing didn't register -- minor, still recovers.)
- **Free Market escape DISABLED** per user: the `_panic` callback in `run`/`runsplit` now just
  releases keys and sets `kb.pause=True` (F8 to resume) instead of the pyautogui teleport.

**Recovery ends at the left home facing right (2026-08-10):** after reaching the top,
`recover_to_farming` now walks to `TOP_HOME_X=74` and taps right (`_face_right`) so she resumes
top-farming from the park-left spot facing the dragons (not wherever she landed near the rope).
Re-verifies she's still on top after the home-walk (retries if she slipped) to avoid false
success.

### What the NEXT session could still do (live, with the user)
- **Integrate `recover_to_farming()` into `blue_dragon_loop`** so it auto-triggers: each
  cycle, full-minimap sense; if `y >= FALLEN_Y_MIN` (off the patrol platform), run recovery
  before resuming patrol. (Notebook needs the `recovery.py` helpers imported or inlined; the
  notebook currently uses the band-crop `get_character` + `show_maple`, not the full crop /
  `focus()`.) NOT yet wired into the loop.
- Optionally add an HP read (OCR like `get_exp`) if an HP-safety bail is ever wanted — user
  said not needed for now.

### Original open items still relevant
1. **Run the `human-layer` cell** (after the definitions cell, before the run cell), then
   start as usual (run cell → F8 to unpause). Blue loop is now human-like out of the box;
   rope-hang stays OFF.
2. **Calibrate the rope:** have the user stand on the rope and run `calibrate_minimap_x()`.
   Put the printed value into `HUMAN["ROPE_MINIMAP_X"]`. (It should land inside 66–161; the
   return-to-platform bounds check assumes the rope is on the patrol platform.)
3. **Enable + tune rope-hang:** set `HUMAN["rope_enabled"] = True`. The timing guesses
   (`ROPE_FALL_TO_REGRAB_DELAY`, `ROPE_CLIMB_DURATION`) will likely need a few live
   iterations — the first grabs may mis-latch. This is expected and safe: a mis-grab
   recovers (climb+jump) or, worst case, `goto_freemarket()`. Tune together with the user.
   ⚠️ Per §7: only actuate live key input with the user's per-action OK. Read-only capture
   (`calibrate_minimap_x`, `get_character`) is fine to run for calibration.
4. **Optional flourishes remain OFF** (`idle_jump_prob`, `arrow_glance_prob` = 0). Do not
   auto-enable arrow-glancing — `Up` near the rope can grab it unintentionally.
