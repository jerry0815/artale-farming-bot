# Portal-Bottom Right-Side Rope Recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the character from the portal-bottom fall by climbing the right-side rope chain back to `TOP_FARM`, instead of panicking to Free Market.

**Architecture:** Extend the existing `navmap` node-graph with three right-side nodes (`PORTAL_BOT`, `LOWER_R`, `BOTTOM_R`) and rope edges, so `travel()` chains the hops: `PORTAL_BOT → LOWER_R → BOTTOM_R → TOP_FARM`. The first two hops use a new bounded `climb_rope_hop` primitive; the last reuses the existing `recover_to_farming` (mirrors `BOTTOM_FARM → TOP_FARM`). A failed hop degrades to today's panic-to-safety.

**Tech Stack:** Python 3, `pytest`, OpenCV/mss (screen), `pynput` (keys). Pure graph logic in `navmap.py`; live screen/key logic in `recovery.py`.

**Spec:** [docs/superpowers/specs/2026-08-16-portal-rope-recovery-design.md](../specs/2026-08-16-portal-rope-recovery-design.md)

## Global Constraints

- **Coordinate frame:** minimap frame from `recovery.get_character_full()` (offset 20,171; `ROPE_X=91`). All band/`grab_x` numbers are in this frame.
- **Bands are PROVISIONAL** until Task 1 finalizes them live. Task 2 ships with the provisional numbers below; if Task 1 measured different extents, use those instead.
- **Keep the suite green at every commit:** `python -m pytest tests/test_navmap.py tests/test_recovery.py -q`. In particular `test_every_edge_has_an_executor` couples `navmap.EDGES` and `recovery.EDGE_ACTIONS` — they must change together in one commit.
- **`climb_rope_hop` is live-tested only** (screen + keys), like `climb_and_jump`. No unit test asserts its climb behavior; Task 3 verifies it against the live game.
- **Safety invariant:** a hop that cannot reach its target returns `False` → `travel()` returns `False` → the nav loop calls `panic()`. Never let a hop hang or walk blindly into a gap.
- **Seam bias:** at the `LOWER_R`/`BOTTOM_R` boundary, ambiguous reads must classify as `LOWER_R` (climb the rope — safe), never `BOTTOM_R` (walks toward the central rope, could drop into the gap).

---

### Task 1: Finalize the three right-side bands live (calibration)

Live measurement, no code commit. Produces the final band numbers Task 2 hard-codes. Requires the game running with the character able to reach each ledge; F8 stops a run.

**Files:**
- Use (already exists): `verify_bands.py`

- [ ] **Step 1: Walk `PORTAL_BOT` and record its extent**

Run:
```bash
python verify_bands.py
```
Stand on the portal-bottom platform; walk to its far-left and far-right edges; press **F8**. Record the printed `THIS PLATFORM` raw box as `PORTAL_BOT` extent.

- [ ] **Step 2: Walk `LOWER_R` (portal-rope landing) and record its extent**

Run `python verify_bands.py` again; stand on the ledge the portal rope lands on (y≈149); walk edge-to-edge; F8. Record as `LOWER_R` extent.

- [ ] **Step 3: Walk `BOTTOM_R` (right-rope top) and record its extent**

Run `python verify_bands.py` again; stand on the ledge the right rope tops out on (y≈136, central-reachable); walk edge-to-edge; F8. Record as `BOTTOM_R` extent.

- [ ] **Step 4: Derive final bands (raw extent ± ~3px margin), applying the seam rule**

For each node: `y_lo,y_hi,x_lo,x_hi` = raw box padded ~3px. Then enforce:
- `BOTTOM_R.y_hi` and `LOWER_R.y_lo` must be **adjacent with no overlap** (e.g. 142 | 143), seam sitting in the measured gap between the ledges. If the padded ranges overlap, shrink `BOTTOM_R.y_hi` down so `BOTTOM_R` (checked first) never claims a `LOWER_R` read.
- `PORTAL_BOT` must not swallow the left `LOWER_LEDGE`: keep `PORTAL_BOT.x_lo` ≥ ~108 (its measured left edge).

Provisional values (use these if Step 1–3 match; else use your measured ones):
```
PORTAL_BOT : y 178-190, x 108-162
LOWER_R    : y 143-154, x 128-163
BOTTOM_R   : y 129-142, x 141-166
```

- [ ] **Step 5: Sanity-check the numbers offline**

Run:
```bash
python -c "
B=[('TOP_FARM',0,95,60,140),('REST',96,110,60,100),('MID',111,131,80,140),('BOTTOM_R',129,142,141,166),('LOWER_R',143,154,128,163),('BOTTOM_FARM',132,156,55,100),('PORTAL_BOT',178,190,108,162),('LOWER_LEDGE',157,200,40,160)]
def c(x,y):
  for n,ylo,yhi,xlo,xhi in B:
    if ylo<=y<=yhi and xlo<=x<=xhi: return n
  return None
for p,w in [((153,183),'PORTAL_BOT'),((140,149),'LOWER_R'),((146,136),'BOTTOM_R'),((150,143),'LOWER_R'),((150,142),'BOTTOM_R'),((135,120),'MID'),((63,145),'BOTTOM_FARM'),((63,165),'LOWER_LEDGE')]:
    g=c(*p); print(p,g,'' if g==w else 'MISMATCH '+w)
"
```
Expected: every line ends without `MISMATCH`. If any mismatch, adjust the bands and re-run. Carry the final band tuple list into Task 2.

---

### Task 2: navmap nodes + edges + `climb_rope_hop` + executors

All code lands here so `test_every_edge_has_an_executor` (which couples `navmap.EDGES` ↔ `recovery.EDGE_ACTIONS`) stays green. Pure tests must pass at the commit; the climb *behavior* is verified live in Task 3.

**Files:**
- Modify: `navmap.py` (`NODES`, `_BANDS`, `EDGES`)
- Modify: `recovery.py` (`EDGE_ACTIONS`, new `climb_rope_hop`)
- Modify: `tests/test_navmap.py` (replace `MID_R` tests)

**Interfaces:**
- Consumes: `navmap._BANDS`, `navmap.classify_node`, `navmap.plan`, `recovery.walk_to_x`, `recovery.get_character_full`, `recovery.stable_char`, `recovery.recover_to_farming`, `recovery.kb`, `recovery.Key`, `recovery.JUMP`.
- Produces: nodes `PORTAL_BOT`/`LOWER_R`/`BOTTOM_R`; edges `PORTAL_BOT→LOWER_R`, `LOWER_R→BOTTOM_R`, `BOTTOM_R→TOP_FARM`; `recovery.climb_rope_hop(grab_x:int, target:str, dismount:str|None=None, cap:float=6.0) -> bool`.

- [ ] **Step 1: Replace the `MID_R` classify/plan tests with the new right-side tests**

In `tests/test_navmap.py`, delete `test_classify_right_drop_ledge` and `test_plan_recovery_from_right_drop_ledge_uses_rope_up`, and add:

```python
def test_classify_right_side_ledges():
    assert navmap.classify_node(153, 183) == "PORTAL_BOT"
    assert navmap.classify_node(140, 149) == "LOWER_R"
    assert navmap.classify_node(146, 136) == "BOTTOM_R"
    # the central nodes are not stolen by the right ledges
    assert navmap.classify_node(135, 120) == "MID"
    assert navmap.classify_node(63, 145) == "BOTTOM_FARM"

def test_lower_r_bottom_r_seam_biases_to_lower_r():
    # stacked ledges; the ambiguous seam read must be LOWER_R (climb rope), not BOTTOM_R
    assert navmap.classify_node(150, 143) == "LOWER_R"
    assert navmap.classify_node(150, 142) == "BOTTOM_R"

def test_plan_portal_bottom_to_top_is_three_right_hops():
    path = navmap.plan("PORTAL_BOT", "TOP_FARM")
    assert path is not None
    assert [(e["src"], e["dst"]) for e in path] == [
        ("PORTAL_BOT", "LOWER_R"),
        ("LOWER_R", "BOTTOM_R"),
        ("BOTTOM_R", "TOP_FARM"),
    ]
    assert all(e["kind"] == "rope" for e in path)

def test_mid_r_is_gone():
    assert "MID_R" not in navmap.NODES
    assert navmap.plan("MID_R", "TOP_FARM") is None
```

- [ ] **Step 2: Run the new navmap tests — verify they FAIL**

Run: `python -m pytest tests/test_navmap.py -k "right_side or seam or three_right or mid_r_is_gone" -q`
Expected: FAIL (nodes/edges not defined yet; `MID_R` still present).

- [ ] **Step 3: Update `navmap.py` — nodes, bands, edges**

In `navmap.py`, set `NODES` (line 11) to:
```python
NODES = ["TOP_FARM", "REST", "MID", "BOTTOM_R", "LOWER_R", "BOTTOM_FARM", "PORTAL_BOT", "LOWER_LEDGE"]
```
Replace `_BANDS` (lines 14–28, including the `MID_R` comment block) with (**use Task 1's finalized numbers**; provisional shown):
```python
# (name, y_lo, y_hi, x_lo, x_hi) inclusive bands; ordered so the first match wins.
# Right-side ledges (_R) mirror the central stack by height but recover via the RIGHT
# ropes, so they are DISTINCT nodes. BOTTOM_R is checked before LOWER_R so the seam
# biases to LOWER_R (climb the rope) rather than BOTTOM_R (walk toward central -> gap).
# PORTAL_BOT is before LOWER_LEDGE so the deep portal ledge isn't read as LOWER_LEDGE.
_BANDS = [
    ("TOP_FARM",     0,  95, 60, 140),
    ("REST",        96, 110, 60, 100),
    ("MID",        111, 131, 80, 140),
    ("BOTTOM_R",   129, 142, 141, 166),   # right twin of BOTTOM_FARM (central-reachable)
    ("LOWER_R",    143, 154, 128, 163),   # right twin of LOWER_LEDGE (portal-rope landing)
    ("BOTTOM_FARM",132, 156, 55, 100),
    ("PORTAL_BOT", 178, 190, 108, 162),   # before LOWER_LEDGE (deep ledge by the portal)
    ("LOWER_LEDGE",157, 200, 40, 160),
]
```
Replace the `MID_R` edge (lines 48–50) with the three right-side edges:
```python
    # Right-side rope chain (portal-bottom recovery). PORTAL_BOT climbs the portal
    # rope to LOWER_R, then the right rope to BOTTOM_R, then the existing central
    # recovery (mirrors BOTTOM_FARM->TOP_FARM) up to the top.
    {"src": "PORTAL_BOT", "dst": "LOWER_R",  "kind": "rope", "rope": "R_PORTAL"},
    {"src": "LOWER_R",    "dst": "BOTTOM_R", "kind": "rope", "rope": "R_RIGHT"},
    {"src": "BOTTOM_R",   "dst": "TOP_FARM", "kind": "rope", "rope": "R_C"},
```

- [ ] **Step 4: Add `climb_rope_hop` to `recovery.py`**

Add this function next to `climb_and_jump` (after `_climb_to_top`, near line 427). It references `navmap`, already imported:
```python
def climb_rope_hop(grab_x, target, dismount=None, cap=6.0):
    """Climb ONE rope up onto the ledge that classifies as `target`, then dismount.
    A bounded single-rope sibling of _climb_to_top (which always rides the central
    column to the top). `grab_x` is the rope's minimap column; `dismount` is 'left'/
    'right' to tap off onto a side ledge, or None if the rope tops out on the ledge.
    Returns True once settled in `target`'s band; else releases keys and returns
    False (so travel() gives up -> the nav loop panics). Never hangs (bounded by cap)."""
    band = next((b for b in navmap._BANDS if b[0] == target), None)
    if band is None:
        print(f"[hop] unknown target {target}"); return False
    _n, y_lo, y_hi, _xl, _xh = band
    if not walk_to_x(grab_x, tol=1):                 # reach the rope base
        cx, _cy = get_character_full()
        if not (cx >= 0 and abs(cx - grab_x) <= 5):  # ok if already hanging on the column
            kb.safe_release_all(); return False
    kb.safe_press(Key.up); kb.safe_press(JUMP)       # hop-grab the rope
    time.sleep(0.08); kb.safe_release(JUMP)
    t0 = time.time()
    while time.time() - t0 < cap:
        if kb.pause:
            break
        _x, y = get_character_full()
        if 0 <= y <= y_hi:                           # reached the target level
            break
        time.sleep(0.05)
    kb.safe_release(Key.up)
    if dismount in ("left", "right"):                # step off onto the side ledge
        key = Key.left if dismount == "left" else Key.right
        kb.safe_press(key); time.sleep(0.12); kb.safe_release(key)
    time.sleep(0.2)                                  # settle
    x, y = stable_char(3)
    ok = navmap.classify_node(x, y) == target
    if not ok:
        print(f"[hop] landed ({x},{y}) != {target}")
    kb.safe_release_all()
    return ok
```

- [ ] **Step 5: Rewire `EDGE_ACTIONS` in `recovery.py`**

In the `EDGE_ACTIONS` dict, remove the `("MID_R", "TOP_FARM")` line and add the three right-side executors (grab-x values are provisional — tuned in Task 3):
```python
    ("PORTAL_BOT", "LOWER_R"):   lambda: climb_rope_hop(132, "LOWER_R", dismount="right"),
    ("LOWER_R", "BOTTOM_R"):     lambda: climb_rope_hop(147, "BOTTOM_R"),
    ("BOTTOM_R", "TOP_FARM"):    lambda: recover_to_farming(),   # mirrors BOTTOM_FARM->TOP_FARM
```

- [ ] **Step 6: Run the full pure suite — verify it PASSES**

Run: `python -m pytest tests/test_navmap.py tests/test_recovery.py -q`
Expected: PASS (all tests, including `test_every_edge_has_an_executor` and the new right-side tests). If `test_every_edge_has_an_executor` fails, an edge/executor pair is out of sync — reconcile `navmap.EDGES` with `recovery.EDGE_ACTIONS`.

- [ ] **Step 7: Confirm classification offline against the calibration points**

Run:
```bash
python -c "import navmap as n; print([n.classify_node(*p) for p in [(153,183),(140,149),(146,136),(150,143),(150,142),(135,120)]])"
```
Expected: `['PORTAL_BOT', 'LOWER_R', 'BOTTOM_R', 'LOWER_R', 'BOTTOM_R', 'MID']`

- [ ] **Step 8: Commit**

```bash
git add navmap.py recovery.py tests/test_navmap.py
git commit -m "feat(nav): right-side rope chain recovery (PORTAL_BOT->LOWER_R->BOTTOM_R->TOP)"
```

---

### Task 3: Live end-to-end verification and tuning

Verify the two new hops on the real game and tune the live constants. No unit tests (screen/keys). F8 aborts.

**Files:**
- Modify (tuning only, if needed): `recovery.py` (`grab_x` in `EDGE_ACTIONS`, `cap`/dismount timing in `climb_rope_hop`)

**Interfaces:**
- Consumes: `recovery.recover_to_farming` (routes `PORTAL_BOT → … → TOP_FARM` via `travel`/`EDGE_ACTIONS`), `recovery.climb_rope_hop`.

- [ ] **Step 1: Verify hop 1 (portal rope) in isolation**

Stand the character on the portal-bottom platform. Run:
```bash
python -c "import recovery as r; r.focus(); print('hop1', r.climb_rope_hop(132, 'LOWER_R', dismount='right'))"
```
Expected: she walks left to the rope, climbs, steps right, and prints `hop1 True` standing on `LOWER_R`. If she overshoots/undershoots the rope, adjust `grab_x` (132) toward the column she actually climbs; if she doesn't step onto the ledge, flip/lengthen the `dismount` tap.

- [ ] **Step 2: Verify hop 2 (right rope) in isolation**

Stand her on `LOWER_R`. Run:
```bash
python -c "import recovery as r; r.focus(); print('hop2', r.climb_rope_hop(147, 'BOTTOM_R'))"
```
Expected: she walks right to the rope, climbs, and prints `hop2 True` on `BOTTOM_R`. Tune `grab_x` (147) / `cap` if needed.

- [ ] **Step 3: Verify the full chain end-to-end**

Stand her on the portal-bottom platform. Run:
```bash
python recovery.py recover
```
Expected: `recover_to_farming` locates `PORTAL_BOT` and `travel` runs all three hops — climbs to `LOWER_R`, then `BOTTOM_R`, then the central rope to `TOP_FARM` — ending `RESULT: True` on the top platform. (Note: `recover_to_farming` must reach `travel`; if it instead bails at its own envelope check, confirm the loop path routes `PORTAL_BOT` through `travel`/`EDGE_ACTIONS` rather than the bare envelope guard — see spec "Error handling".)

- [ ] **Step 4: Verify the safety fallback still holds**

Confirm a *failed* hop degrades safely: temporarily stand her somewhere a hop can't complete (or observe a genuine miss) and confirm `recover`/the loop ends in `panic()`/`False`, never an infinite retry or a blind walk into the gap.

- [ ] **Step 5: Commit any live tuning**

```bash
git add recovery.py
git commit -m "tune(nav): live-calibrate portal/right rope-hop grab_x, cap, dismount"
```

---

## Self-Review

**Spec coverage:**
- New nodes `PORTAL_BOT`/`LOWER_R`/`BOTTOM_R`, `MID_R` removed → Task 2 Steps 1,3.
- Two rope edges + `BOTTOM_R→TOP_FARM` reuse → Task 2 Step 3.
- `climb_rope_hop` primitive (contract from spec) → Task 2 Step 4.
- Executor wiring, drop `MID_R` executor → Task 2 Step 5.
- Seam bias to `LOWER_R` → `_BANDS` order (BOTTOM_R before LOWER_R) + `test_lower_r_bottom_r_seam_biases_to_lower_r`.
- Bands finalized live first → Task 1.
- Degrade-to-panic safety → Task 3 Step 4.
- Tests updated (remove MID_R, add new) → Task 2 Step 1.

**Placeholder scan:** none — all code blocks are concrete; provisional band/grab_x numbers are explicit and flagged for live tuning, not TBDs.

**Type consistency:** `climb_rope_hop(grab_x, target, dismount=None, cap=6.0) -> bool` — same signature in the interface block, Task 2 Step 4 definition, Step 5 lambdas, and Task 3 calls. Node/edge names (`PORTAL_BOT`, `LOWER_R`, `BOTTOM_R`) consistent across `NODES`, `_BANDS`, `EDGES`, `EDGE_ACTIONS`, and tests.
