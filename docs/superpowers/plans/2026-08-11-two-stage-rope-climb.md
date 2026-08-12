# Two-Stage Rope Climb (bottom → mid → top) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bottom→top rope climb a two-segment climb — center rope `BOTTOM → MID` ledge, shift left, left rope `MID → TOP` — so `recover_to_farming` actually reaches the top from the bottom platform.

**Architecture:** Add a human-driven climb recorder to measure the map geometry, promote those into named constants, and rebuild `climb_and_jump` on a reusable single-segment climb primitive (`_climb_segment`) plus a pure stage-decision helper (`_climb_plan`). `recover_to_farming` and every caller stay unchanged — `climb_and_jump` keeps its name and `bool` contract, and now performs one or two rope segments depending on starting height.

**Tech Stack:** Python 3.11, pytest, pynput (via `keyboard.py` as `kb`), existing `recovery.py` sensing (`get_character_full`, `stable_char`, `walk_to_x`).

## Global Constraints

- **Interpreter:** `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe` (all Run commands; pytest installed).
- **No regression, ever.** `recover_to_farming` is the "anywhere-below → top" entry point used across both farming loops and `navmap.EDGE_ACTIONS`. `climb_and_jump` keeps signature `() -> bool` and its call site at `recovery.py` (`if not climb_and_jump():`) is unchanged. A climb starting from MID/REST must behave like today (single climb to top).
- **Do NOT change the `navmap` graph edges.** Changing `R_C` to `BOTTOM_FARM→MID` would make `navmap.travel` emit a `BOTTOM_FARM→MID` hop with no `EDGE_ACTIONS` executor and break `farming_loop_nav`. The `BOTTOM_FARM→TOP_FARM` edge stays a recover-macro that now climbs two segments internally.
- **Every early return releases keys** (`kb.safe_release_all()`), so the `recover_to_farming` round loop re-localizes and retries on any missed stage.
- **Pure/offline tests, no live game in unit tests.** Monkeypatch `get_character_full` / `walk_to_x` / `kb` / `recovery.time.sleep`; guard `import recovery` with `pytest.skip` (matches `tests/test_rope_grab.py`).
- **Reuse existing sensing/movement.** `get_character_full()`, `stable_char(n)`, `walk_to_x(target_x, tol=...)`, `kb.safe_press/release/safe_release_all`, `Key.up/left/right`, `JUMP`. Do not reimplement.
- **Data file:** climb trajectories → `climb_traj.jsonl` (git-ignored).
- **Calibrated bands (minimap frame):** `BOTTOM_Y_MIN=132`, `FALLEN_Y_MIN=96`, `ROPE_EXIT_TOP_Y=92`, `LOWER_LEDGE_Y=147`; navmap `MID` band y 111–131, x 80–140; `ROPE_X=91`, `BOTTOM_ROPE_X=95`.

---

## Task 1: Climb-trajectory recorder + `record-climb` CLI

**Files:**
- Modify: `recovery.py` (add `record_climb_attempt` above `recover_to_farming`; add a `record-climb` branch to the `__main__` CLI)
- Modify: `.gitignore`
- Test: `tests/test_climb.py` (create)

**Interfaces:**
- Consumes: `get_character_full`, `kb`, `time`.
- Produces: `record_climb_attempt(cap=20.0) -> dict` — a trajectory dict `{"reads": [[t, x, y], ...]}` (`t` = seconds since start). Presses NO keys; polls at ~30 Hz until F8 (`kb.pause`) or `cap`. Returns even if empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_climb.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_climb.py -k record_climb -v`
Expected: FAIL with `AttributeError: module 'recovery' has no attribute 'record_climb_attempt'`.

- [ ] **Step 3: Write minimal implementation**

```python
# recovery.py  (add just above recover_to_farming)
def record_climb_attempt(cap=20.0):
    """Data collection ONLY (sense-only, presses NO keys): while the USER manually
    climbs bottom->top, poll get_character_full() at ~30Hz into reads=[[t,x,y],...].
    Stops on F8 (kb.pause) or `cap` seconds. Zero-risk: never moves the character."""
    reads, t0 = [], time.time()
    while time.time() - t0 < cap:
        if kb.pause:
            break
        x, y = get_character_full()
        if x >= 0:
            reads.append([round(time.time() - t0, 3), int(x), int(y)])
        time.sleep(0.03)
    return {"reads": reads}
```

Then add the CLI branch (inside the `__main__` `elif` chain, e.g. after the `recover` branch):

```python
    elif cmd == "record-climb":
        import json as _json
        if not focus():
            print("no focus"); sys.exit(1)
        lis = Listener(on_press=kb.on_press); lis.start()      # F8 stops
        print("[record-climb] manually climb bottom->top; press F8 to stop")
        try:
            tr = record_climb_attempt()
        finally:
            kb.safe_release_all(); lis.stop()
        with open("climb_traj.jsonl", "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(tr) + "\n")
        print(f"[record-climb] wrote {len(tr['reads'])} reads to climb_traj.jsonl")
```

Add to `.gitignore` (new line): `climb_traj.jsonl`

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_climb.py -k record_climb -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_climb.py .gitignore
git commit -m "feat(recovery): sense-only climb-trajectory recorder + record-climb CLI"
```

**LIVE STEP (with the user, per-action OK — sense-only, no character movement):**
Run `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe recovery.py record-climb`, manually climb bottom→top once, press F8. Read the six constants off `climb_traj.jsonl` (first rising segment x = `R_C_X`; y where it stalls = `MID_LEDGE_Y`; x standing on the ledge = `MID_LAND_X`; second rising segment x = `R_L_X`; final y = confirms `TOP_EXIT_Y`; whether y dropped without a jump on the ledge = `R_L_HOP`). These update the seeded constants in Task 2.

---

## Task 2: Climb geometry constants + `_climb_plan` stage decision

**Files:**
- Modify: `recovery.py` (add a constants group near the existing rope constants ~line 38; add `_climb_plan` above `climb_and_jump`)
- Test: `tests/test_climb.py`

**Interfaces:**
- Consumes: `BOTTOM_Y_MIN`, `ROPE_EXIT_TOP_Y`.
- Produces:
  - Module constants: `R_C_X`, `R_L_X`, `MID_LEDGE_Y`, `MID_Y_MIN`, `MID_Y_MAX`, `MID_LAND_X`, `TOP_EXIT_Y`, `R_L_HOP`.
  - `_climb_plan(y0) -> list[str]` — `["R_C->MID", "MID->TOP"]` if `y0 >= BOTTOM_Y_MIN`, else `["MID->TOP"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_climb.py  (append)
def test_climb_plan_from_bottom_is_two_stage():
    recovery = _skip_if_no_recovery()
    assert recovery._climb_plan(143) == ["R_C->MID", "MID->TOP"]
    assert recovery._climb_plan(recovery.BOTTOM_Y_MIN) == ["R_C->MID", "MID->TOP"]

def test_climb_plan_from_mid_or_rest_is_one_stage():
    recovery = _skip_if_no_recovery()
    assert recovery._climb_plan(120) == ["MID->TOP"]                    # MID ledge
    assert recovery._climb_plan(105) == ["MID->TOP"]                    # REST platform
    assert recovery._climb_plan(recovery.BOTTOM_Y_MIN - 1) == ["MID->TOP"]

def test_climb_constants_present_and_ordered():
    recovery = _skip_if_no_recovery()
    assert recovery.R_C_X > recovery.R_L_X          # center rope is right of the left rope
    assert recovery.MID_Y_MIN <= recovery.MID_LEDGE_Y <= recovery.MID_Y_MAX
    assert recovery.TOP_EXIT_Y == recovery.ROPE_EXIT_TOP_Y
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_climb.py -k "climb_plan or climb_constants" -v`
Expected: FAIL with `AttributeError: module 'recovery' has no attribute '_climb_plan'`.

- [ ] **Step 3: Write minimal implementation**

```python
# recovery.py  (add near the rope constants, ~after ROPE_CLIMB_MAX)
# --- two-stage climb geometry (bottom -> MID ledge -> top). SEEDED from the current
# single-rope constants + navmap MID band; refine from `record-climb` (Task 1). ---
R_C_X = 95                     # center rope x (bottom -> MID ledge); cf. BOTTOM_ROPE_X
R_L_X = 91                     # left rope x   (MID/REST -> top);      cf. ROPE_X
MID_Y_MIN, MID_Y_MAX = 111, 131   # MID ledge y-band (mirrors navmap)
MID_LEDGE_Y = 118              # y at the top of the center rope (on the MID ledge)
MID_LAND_X = 100               # x where the center rope drops her on the ledge (pre-shift)
TOP_EXIT_Y = ROPE_EXIT_TOP_Y   # 92; then up-jump onto the top platform
R_L_HOP = False                # does grabbing the left rope from the ledge need a hop?
```

```python
# recovery.py  (add just above climb_and_jump)
def _climb_plan(y0):
    """Rope segments to climb from starting height y0. Bottom/lower -> center rope to
    the MID ledge, then left rope to the top. Mid/rest -> just the left rope to top."""
    if y0 >= BOTTOM_Y_MIN:
        return ["R_C->MID", "MID->TOP"]
    return ["MID->TOP"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_climb.py -k "climb_plan or climb_constants" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_climb.py
git commit -m "feat(recovery): two-stage climb geometry constants + _climb_plan"
```

---

## Task 3: `_climb_segment` single-rope primitive

**Files:**
- Modify: `recovery.py` (add `_climb_segment` above `climb_and_jump`)
- Test: `tests/test_climb.py`

**Interfaces:**
- Consumes: `walk_to_x`, `get_character_full`, `kb`, `Key`, `JUMP`, `ROPE_CLIMB_MAX`.
- Produces: `_climb_segment(grab_x, exit_y, hop, stall_ok=False, cap=ROPE_CLIMB_MAX) -> bool` — aligns to `grab_x`, grabs the rope (`hop` = Up+Jump for a rope whose base is above the floor, else press Up), holds Up until `y <= exit_y`. Returns `True` on reaching `exit_y`; if `stall_ok`, also returns `True` when she has climbed then stopped rising (arrived at a ledge). Never grabbing, or a non-`stall_ok` stall that never reaches `exit_y`, returns `False`. Always releases Up before returning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_climb.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_climb.py -k climb_segment -v`
Expected: FAIL with `AttributeError: module 'recovery' has no attribute '_climb_segment'`.

- [ ] **Step 3: Write minimal implementation**

```python
# recovery.py  (add just above climb_and_jump)
def _climb_segment(grab_x, exit_y, hop, stall_ok=False, cap=ROPE_CLIMB_MAX):
    """Align to grab_x, grab the rope, hold Up until y<=exit_y. `hop` = Up+Jump to
    catch a rope whose base is above the floor. `stall_ok` = also succeed when she has
    climbed then stopped rising (arrived at a ledge). Releases Up before returning;
    returns True iff the segment completed."""
    if not walk_to_x(grab_x, tol=1):
        kb.safe_release_all(); return False
    kb.safe_press(Key.up)
    if hop:
        kb.safe_press(JUMP); time.sleep(0.08); kb.safe_release(JUMP)
    last_y, no_grab, stalled, climbing, reached = None, 0, 0, False, False
    t0 = time.time()
    while time.time() - t0 < cap:
        if kb.pause:
            break
        x, y = get_character_full()
        if y >= 0:
            if y <= exit_y:
                reached = True; break
            if last_y is not None and y <= last_y - 6:        # rose >=6px -> climbing
                climbing = True; stalled = 0
            elif last_y is not None and y >= last_y - 1:      # not rising this frame
                if climbing:
                    stalled += 1
                    if stall_ok and stalled >= 3:             # arrived at the ledge
                        reached = True; break
                else:
                    no_grab += 1
                    if no_grab >= 3:                          # never grabbed -> give up
                        break
            last_y = y
        time.sleep(0.08)
    kb.safe_release(Key.up)
    return reached
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_climb.py -k climb_segment -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_climb.py
git commit -m "feat(recovery): _climb_segment single-rope climb primitive"
```

---

## Task 4: Two-stage `climb_and_jump`

**Files:**
- Modify: `recovery.py` (replace the body of `climb_and_jump`, ~lines 324-358)
- Test: `tests/test_climb.py`

**Interfaces:**
- Consumes: `_climb_plan`, `_climb_segment`, `walk_to_x`, `get_character_full`, `kb`, `Key`, `JUMP`, the Task 2 constants.
- Produces: `climb_and_jump() -> bool` (unchanged signature) — climbs one or two rope segments per `_climb_plan`, then up-jumps onto the top. `True` only if she reached the top; releases keys and returns `False` on any missed stage.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_climb.py  (append)
def test_climb_and_jump_two_stage_from_bottom(monkeypatch):
    recovery = _skip_if_no_recovery()
    # first read = bottom (y143) -> two-stage plan; after stage 1, read = MID ledge (y118)
    pos = iter([(95, 143), (100, 118)])
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: next(pos, (100, 118)))
    seg_calls, walks = [], []
    monkeypatch.setattr(recovery, "_climb_segment",
                        lambda grab_x, exit_y, hop, **k: seg_calls.append(grab_x) or True)
    monkeypatch.setattr(recovery, "walk_to_x", lambda x, **k: walks.append(x) or True)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is True
    assert seg_calls == [recovery.R_C_X, recovery.R_L_X]     # center rope, then left rope
    assert recovery.R_L_X in walks                           # the LEFT shift happened

def test_climb_and_jump_one_stage_from_mid(monkeypatch):
    recovery = _skip_if_no_recovery()
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: (91, 120))  # on MID
    seg_calls = []
    monkeypatch.setattr(recovery, "_climb_segment",
                        lambda grab_x, exit_y, hop, **k: seg_calls.append(grab_x) or True)
    monkeypatch.setattr(recovery, "walk_to_x", lambda *a, **k: True)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is True
    assert seg_calls == [recovery.R_L_X]                      # only the left rope

def test_climb_and_jump_bails_if_not_on_ledge_after_stage1(monkeypatch):
    recovery = _skip_if_no_recovery()
    # bottom start, but after stage 1 she is NOT in the MID band (fell to y145)
    pos = iter([(95, 143), (95, 145)])
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: next(pos, (95, 145)))
    monkeypatch.setattr(recovery, "_climb_segment", lambda *a, **k: True)
    walks = []
    monkeypatch.setattr(recovery, "walk_to_x", lambda x, **k: walks.append(x) or True)
    monkeypatch.setattr(recovery, "kb", _FakeKB())
    monkeypatch.setattr(recovery.time, "sleep", lambda *a: None)
    assert recovery.climb_and_jump() is False
    assert recovery.R_L_X not in walks                       # never attempted the left shift
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_climb.py -k climb_and_jump -v`
Expected: FAIL (old `climb_and_jump` calls real sensing / does not call `_climb_segment` in this order → assertions fail).

- [ ] **Step 3: Write minimal implementation**

Replace the entire existing `climb_and_jump` function (docstring + body) with:

```python
def climb_and_jump():
    """Climb the rope(s) to the top farming platform, then up-jump onto it. From the
    bottom she must climb the CENTER rope to the MID ledge, shift LEFT, and climb the
    LEFT rope to the top; from MID/REST only the left rope remains. Returns True only if
    she reached the top -- on any missed stage releases keys and returns False so the
    caller (recover_to_farming) re-localizes and retries."""
    x0, y0 = get_character_full()
    stages = _climb_plan(y0)
    if "R_C->MID" in stages:                              # center rope: bottom -> MID ledge
        if not _climb_segment(R_C_X, MID_LEDGE_Y, hop=True, stall_ok=True):
            kb.safe_release_all(); return False
        _xm, ym = get_character_full()
        if not (MID_Y_MIN <= ym <= MID_Y_MAX):            # not on the ledge -> bail, don't shift
            kb.safe_release_all(); return False
        if not walk_to_x(R_L_X, tol=1):                   # the measured LEFT shift to the left rope
            kb.safe_release_all(); return False
    if not _climb_segment(R_L_X, TOP_EXIT_Y, hop=R_L_HOP):  # left rope: MID -> top
        kb.safe_release_all(); return False
    kb.safe_press(JUMP); time.sleep(0.15); kb.safe_release(JUMP); time.sleep(0.12)
    kb.safe_release_all(); time.sleep(0.1)
    return True
```

- [ ] **Step 4: Run test to verify it passes + no regression in the suite**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/ -q`
Expected: PASS (all `test_climb.py`, `test_rope_grab.py`, `test_navmap.py`).

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_climb.py
git commit -m "feat(recovery): two-stage climb_and_jump (center->MID, shift left, left->top)"
```

---

## Task 5: navmap clarifying comment + handoff addendum

**Files:**
- Modify: `navmap.py` (comment only, near the `R_C` edges ~line 34)
- Modify: `HUMANIZE_HANDOFF.md` (addendum)

**Interfaces:** none (docs/comment only).

- [ ] **Step 1: Add the navmap comment (no edge change)**

Above the `EDGES` list `R_C` entries in `navmap.py`, add:

```python
# NOTE: the R_C edges are logical "recover-macro" edges. Physically the center rope
# only reaches the MID ledge; recovery.climb_and_jump then shifts LEFT and climbs the
# R_L rope to the top. The graph keeps a single BOTTOM_FARM->TOP_FARM edge (executed by
# recover_to_farming) so navmap.travel needs no per-rope executor. Do not split it into
# BOTTOM_FARM->MID without also adding an EDGE_ACTIONS executor for that hop.
```

- [ ] **Step 2: Append the handoff addendum**

Add to `HUMANIZE_HANDOFF.md`:

```markdown
### Two-stage rope climb (bottom -> MID -> top) — 2026-08-11
- Problem: climb_and_jump held Up on ONE rope bottom->top, but the center rope (R_C)
  only reaches the MID ledge; she must shift LEFT to the left rope (R_L) to reach the top.
- Fix: climb_and_jump now runs _climb_plan(y0) -> _climb_segment(R_C_X, MID_LEDGE_Y) then
  walk_to_x(R_L_X) then _climb_segment(R_L_X, TOP_EXIT_Y). MID/REST starts do one segment.
- Geometry constants (R_C_X, R_L_X, MID_LEDGE_Y, MID_Y_MIN/MAX, MID_LAND_X, R_L_HOP) are
  measured via `python recovery.py record-climb` (sense-only) -> climb_traj.jsonl (ignored).
- recover_to_farming and both farming loops are unchanged; navmap graph deliberately NOT
  split (would need a per-hop executor).
```

- [ ] **Step 3: Verify + commit**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -c "print('R_C' in open('navmap.py',encoding='utf-8').read() and 'Two-stage rope climb' in open('HUMANIZE_HANDOFF.md',encoding='utf-8').read())"`
Expected: `True`.

```bash
git add navmap.py HUMANIZE_HANDOFF.md
git commit -m "docs: two-stage climb note in navmap + handoff addendum"
```

---

## Live validation protocol (after the plan — with the user, per-action OK)

Not a coding task; the go/no-go for the geometry.
1. `python recovery.py record-climb` → user manually climbs bottom→top → set the six constants in `recovery.py` from `climb_traj.jsonl` (Task 1 LIVE STEP). Commit the constant update.
2. From the bottom platform, `python recovery.py recover` a few times → she should reach the top in one round: center rope → MID ledge → left shift → left rope → up-jump. Confirm MID/REST starts still recover in one segment (no regression).
3. If a stage misses, the round loop retries; if it consistently misses, re-check the measured `R_C_X`/`R_L_X`/`MID_LEDGE_Y` against a fresh `record-climb`.

---

## Self-Review Notes

- **Spec coverage:** recorder + constants (Task 1–2), `_climb_plan` (Task 2), `_climb_segment` (Task 3), two-stage `climb_and_jump` with left-shift + bail-if-not-on-ledge (Task 4), navmap honesty comment + handoff (Task 5), live measurement/validation (protocol). The spec's *optional* navmap edge change is intentionally replaced by a comment (see Global Constraints) to avoid regressing `farming_loop_nav`.
- **No-regression:** `climb_and_jump` keeps `() -> bool`; call site unchanged; MID/REST path is one segment as before; `recover_to_farming` and `navmap` graph untouched.
- **Type consistency:** `_climb_plan(y0) -> list[str]` with tokens `"R_C->MID"`/`"MID->TOP"`; `_climb_segment(grab_x, exit_y, hop, stall_ok=False, cap=...) -> bool`; constants `R_C_X`/`R_L_X`/`MID_LEDGE_Y`/`MID_Y_MIN`/`MID_Y_MAX`/`MID_LAND_X`/`TOP_EXIT_Y`/`R_L_HOP` used identically across Tasks 2–4.
- **Offline-testable:** all unit tests monkeypatch sensing/keys/sleep and guard `import recovery`; no live game in tests. Live steps are separated and labeled.
