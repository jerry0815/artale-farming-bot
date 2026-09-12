# Stacked JSON-route nav map — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second land-farming map — three stacked platforms linked by one rope chain — as a JSON route that reuses `farming_loop_nav`'s sweep/deplete/rope machinery, without changing how the existing dragon nest runs.

**Architecture:** The nav loop becomes opt-in route-aware: when the selected map JSON carries `nodes`, it loads that geometry via `navmap.load_route` and applies the per-map minimap crop + valid-dot box (direct-dot sensing, fixed minimap). Farm-node rotation is generalized from a 2-way toggle to an N-node cycle. All edge executors (`climb_rope_hop`, `walk_edge`, `downjump_edge`) and the locate/deplete/safety paths already exist and are reused unchanged. When a map has no `nodes`, the old nest runs exactly as today on `navmap.py`'s built-in defaults.

**Tech Stack:** Python 3, pytest, existing modules `navmap.py` (pure graph/pathfinding), `recovery.py` (live loop + screen I/O), `watermap.py` (map JSON loader), `monsters.py` (YOLO dragon detector).

## Global Constraints

- **Old nest untouched.** Every change is gated on the map JSON having a `nodes` key. With no route loaded, `navmap` keeps its built-in `NODES`/`FARM_NODES`/`_BANDS`/`EDGES` and `farming_loop_nav` behaves byte-for-byte as before.
- **Route mutation must be reversible.** `navmap.load_route(cfg)` mutates module globals; the loop MUST call `navmap.reset_route()` on exit (finally block) so one run never leaks geometry into the next.
- **Backward-compatible rotation.** The generalized `next_farm_target` must keep passing the existing 2-node tests (`test_next_farm_target_rotates_when_depleted`, `test_rotation_decision_end_to_end` in `tests/test_navmap.py`).
- **No new sensing/rope/safety code.** Reuse `stable_char`→`get_character_full` (direct-dot, no scroll-pin), `climb_rope_hop`/`walk_edge`/`downjump_edge`, and the loop's existing enemy/EXP/lie-check/pause/panic handling.
- **Edge param names are fixed by `execute_edge`** ([recovery.py:1626](../../../recovery.py)): rope edges use `grab_x` + `land_y` (+ optional `dismount`); walk/downjump edges use `target_x`. `navmap.load_route` flattens each edge's `params` block onto the edge dict, so the JSON writes them under `"params"`.
- **Map slug:** `dragon_nest_2` (in-game 米納爾森林 / 遭破壞的龍之巢穴).

---

### Task 1: Generalize `next_farm_target` to cycle N ordered farm nodes

**Files:**
- Modify: `navmap.py:131-136` (`next_farm_target`)
- Test: `tests/test_navmap.py` (add cases)

**Interfaces:**
- Consumes: `navmap.FARM_NODES` (list, ordered), the module already exports it.
- Produces: `next_farm_target(current, dragon_count, threshold=2) -> str | None` — unchanged signature. New behavior: when depleted and `current` is a farm node, returns the NEXT farm node cyclically (`FARM_NODES[(i+1) % len]`); a 2-element list still toggles exactly as before.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_navmap.py`:

```python
def test_next_farm_target_cycles_three_nodes():
    import navmap
    navmap.load_route({
        "map": "t", "farm_nodes": ["BOT", "MID", "TOP"],
        "nodes": [{"name": n, "band": [0, 10, i * 20, i * 20 + 10]}
                  for i, n in enumerate(["BOT", "MID", "TOP"])],
        "edges": [],
    })
    try:
        assert navmap.next_farm_target("BOT", 0) == "MID"
        assert navmap.next_farm_target("MID", 0) == "TOP"
        assert navmap.next_farm_target("TOP", 0) == "BOT"      # wrap to bottom
        assert navmap.next_farm_target("BOT", 5) is None        # populated -> stay
        assert navmap.next_farm_target("NOWHERE", 0) is None    # off a farm node
    finally:
        navmap.reset_route()
```

- [ ] **Step 2: Run the new tests + the existing ones to confirm the new ones fail**

Run: `python -m pytest tests/test_navmap.py -v`
Expected: `test_next_farm_target_cycles_three_nodes` FAILS at the `"TOP" -> "BOT"` wrap (current code returns `FARM_NODES[0]` only when `current == FARM_NODES[0]`, so `"MID"`/`"TOP"` misbehave). Existing 2-node tests still PASS.

- [ ] **Step 3: Implement the cycle**

Replace `navmap.next_farm_target` body:

```python
def next_farm_target(current, dragon_count, threshold=2):
    if dragon_count >= threshold:
        return None
    if current not in FARM_NODES:
        return None
    i = FARM_NODES.index(current)
    return FARM_NODES[(i + 1) % len(FARM_NODES)]
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_navmap.py -v`
Expected: ALL pass — the new 3-node cycle plus the existing 2-node toggle tests (a 2-element cycle is a toggle).

- [ ] **Step 5: Commit**

```bash
git add navmap.py tests/test_navmap.py
git commit -m "feat(navmap): cycle N ordered farm nodes in next_farm_target"
```

---

### Task 2: Add a testable `_apply_nav_map(cfg)` route-setup helper

**Files:**
- Modify: `recovery.py` (add `_apply_nav_map` near `farming_loop_nav`, ~line 2445)
- Test: `tests/test_recovery.py` (add cases)

**Interfaces:**
- Consumes: `watermap.load_map`, `watermap.minimap_crop`, `navmap.load_route`, `recovery.set_minimap`, `recovery.set_map_box` (all exist).
- Produces: `_apply_nav_map(cfg: dict) -> bool` — if `cfg` has a truthy `nodes` list: calls `navmap.load_route(cfg)`, `set_minimap(*watermap.minimap_crop(cfg))`, and (if `cfg.get("map_box")`) `set_map_box(*cfg["map_box"])`, then returns `True` (route mode). Otherwise makes no changes and returns `False`. Pure w.r.t. the game (only touches module globals), so unit-testable.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_recovery.py` (guard the import like the file's other tests do):

```python
def test_apply_nav_map_loads_route_and_returns_true():
    import recovery, navmap
    cfg = {
        "map": "t2", "farm_nodes": ["BOT", "TOP"],
        "minimap": {"x": 20, "y": 171, "w": 210, "h": 400},
        "map_box": [30, 200, 50, 250],
        "nodes": [{"name": "BOT", "band": [0, 10, 100, 110]},
                  {"name": "TOP", "band": [0, 10, 0, 10]}],
        "edges": [{"src": "BOT", "dst": "TOP", "kind": "rope",
                   "params": {"grab_x": 40, "land_y": 5}}],
    }
    try:
        assert recovery._apply_nav_map(cfg) is True
        assert navmap.FARM_NODES == ["BOT", "TOP"]
        assert navmap.classify_node(5, 5) == "TOP"
        assert (recovery.MM_X, recovery.MM_Y) == (20, 171)
        assert (recovery.MAP_X_MIN, recovery.MAP_Y_MAX) == (30, 250)
    finally:
        navmap.reset_route()

def test_apply_nav_map_without_nodes_is_noop_false():
    import recovery, navmap
    before = list(navmap.FARM_NODES)
    assert recovery._apply_nav_map({"loop": "nav"}) is False
    assert navmap.FARM_NODES == before        # built-in defaults untouched
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_recovery.py -k apply_nav_map -v`
Expected: FAIL with `AttributeError: module 'recovery' has no attribute '_apply_nav_map'`.

- [ ] **Step 3: Implement the helper**

Add just above `def farming_loop_nav(` in `recovery.py`:

```python
def _apply_nav_map(cfg):
    """Opt-in per-map geometry for the nav loop. If `cfg` carries a `nodes` list,
    load its route into navmap and apply the map's minimap crop + valid-dot box
    (direct-dot sensing, fixed minimap), and return True (route mode). Otherwise
    leave navmap on its built-in defaults and return False. Reversible via
    navmap.reset_route()."""
    import navmap, watermap
    if not cfg or not cfg.get("nodes"):
        return False
    navmap.load_route(cfg)
    set_minimap(*watermap.minimap_crop(cfg))
    if cfg.get("map_box"):
        set_map_box(*cfg["map_box"])
    return True
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_recovery.py -k apply_nav_map -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_recovery.py
git commit -m "feat(recovery): _apply_nav_map opt-in per-map geometry helper"
```

---

### Task 3: Wire route mode into `farming_loop_nav`

**Files:**
- Modify: `recovery.py:2447-2624` (`farming_loop_nav`)

**Interfaces:**
- Consumes: `_apply_nav_map` (Task 2), `navmap.FARM_NODES` (Task 1 cycle), `navmap.reset_route`.
- Produces: no new public symbol; `farming_loop_nav(..., cfg=cfg)` gains route-mode behavior. Panel already calls it with `cfg` ([panel.py:255](../../../panel.py)) so no caller change.

This task has no pure unit test (it is the live loop); it is verified live in Task 5. Keep each edit minimal and localized.

- [ ] **Step 1: Load the route at loop start and pick the home node**

In `farming_loop_nav`, right after the existing `if cfg:` cadence-override block (~line 2462), add:

```python
    route_mode = _apply_nav_map(cfg)          # opt-in per-map geometry (else built-in nest)
    home = navmap.FARM_NODES[0]               # nest default = "TOP_FARM"; new map = bottom node
```

Then change the hardcoded start `current = "TOP_FARM"` (~line 2471) to:

```python
    current = home
```

- [ ] **Step 2: Wrap the loop body so the route always resets on exit**

The loop has several `return` points. Guarantee cleanup by wrapping the `while True:` loop in `try/finally`. Immediately before `while True:` (~line 2516) add `try:`, indent the loop body one level, and after the loop add:

```python
    finally:
        if route_mode:
            navmap.reset_route()
```

(If restructuring indentation is risky, instead add `if route_mode: navmap.reset_route()` before EACH of the loop's `return` statements — there are the STOP, max_seconds, and both are inside the loop. The `try/finally` is preferred; it can't miss a path.)

- [ ] **Step 3: Gate the old-nest recovery paths behind `route_mode`**

The unmapped-position branch (~line 2548) calls `recover_to_farming()` (old-nest central-rope climb) and sets `current = "TOP_FARM"`. On a route map that executor is wrong. Replace that branch's recovery with:

```python
            print(f"[nav] unmapped position ({x},{y}) -> recover")
            if route_mode:
                # No scroll-pin recovery on a route map: an unclassifiable dot means
                # she's off the mapped platforms -> hand to panic (alarm+pause) rather
                # than driving the nest-specific central-rope climb.
                if panic:
                    print("[nav] unmapped on route map -> panic"); panic(); time.sleep(1)
                else:
                    time.sleep(1)
            elif recover_to_farming():
                current = home
            elif panic:
                print("[nav] unmapped & unrecoverable -> panic")
                panic(); time.sleep(1)
            else:
                time.sleep(1)
            continue
```

Also change the two other `current = "TOP_FARM"` assignments in the break block (~line 2581) and the post-`recover_to_farming` (~line 2550, now handled above) to `current = home`. The break block stays as-is otherwise but is only reached when `breaks` is true — the new map sets `breaks:false` (Task 4), so the nest-specific `drop_to_fallen`/`recover_to_farming` there won't run for it.

- [ ] **Step 4: Sanity-check nothing else references the old hardcoded name**

Run: `python -c "import ast,recovery"` then:
Run: `python -m pytest tests/test_recovery.py tests/test_navmap.py -q`
Expected: all existing tests still PASS (old nest path unchanged; route helpers pass).

- [ ] **Step 5: Byte-for-byte guard for the old nest**

Manually confirm: with `cfg` lacking `nodes` (the dragon_nest.json case), `route_mode` is `False`, so `_apply_nav_map` no-ops, `home == "TOP_FARM"`, the `finally` skips `reset_route`, and every recovery branch takes its original `elif`. No behavior change.

- [ ] **Step 6: Commit**

```bash
git add recovery.py
git commit -m "feat(recovery): route-mode farming_loop_nav (opt-in per-map geometry)"
```

---

### Task 4: Author `maps/dragon_nest_2.json` via the record/build pipeline

**Files:**
- Create: `maps/dragon_nest_2.json`

**Interfaces:**
- Consumes: `record_route.py` (RouteRecorder), `build_route.build_route`, the panel's Farm dropdown (auto-lists any `maps/*.json`).
- Produces: a route JSON with `loop:"nav"`, `minimap`, `map_box`, `nodes` (3 farm + any transit), `farm_nodes` ordered **bottom→top**, and `edges` (rope-up chain + downjump-down chain) with `params` matching `execute_edge` (`grab_x`+`land_y` for rope, `target_x` for walk/downjump).

This task is live authoring, not code; its deliverable is the JSON, verified by parsing + classify, not by a farm run (that's Task 5).

- [ ] **Step 1: Measure the minimap crop and valid-dot box**

With the game on the new map, capture one frame and find the minimap rectangle and the player-dot x/y range. Reuse the same approach used for `deep_sea_2` (see `debug_minimap.py` / the `map_box` note in `maps/deep_sea_2.json`). Record `minimap:{x,y,w,h}` and `map_box:[x_min,x_max,y_min,y_max]`.

- [ ] **Step 2: Record a route**

From the panel (or `python record_route.py dragon_nest_2`), walk the map once: stand on the **bottom** farm platform, mark it; climb to **mid**, mark; climb to **top**, mark; then walk to each rope's grab column and mark the drop/return points. Marks become nodes; positions become bands.

- [ ] **Step 3: Build the JSON**

Run `build_route.build_route(records, "dragon_nest_2", farm_nodes=["BOT","MID","TOP"], minimap=..., ...)` (mirror `docs/superpowers/plans/2026-08-25-water-map-workflow.md`). Then hand-edit the emitted file to:
- set `"loop": "nav"`, `"breaks": false`
- ensure `farm_nodes` is ordered bottom→top
- ensure each rope edge is `{"src","dst","kind":"rope","params":{"grab_x":<col>,"land_y":<top-y>}}`
- add the downjump return chain `TOP→…→BOT` as `{"kind":"downjump","params":{"target_x":<col>}}` (or a single `TOP→BOT` downjump if one drop clears all gaps)
- copy the detector/buff block from `maps/deep_sea_2.json` but keep the dragon YOLO defaults; set `deplete_threshold` (start at 1)

- [ ] **Step 4: Verify it parses and classifies**

Run:
```bash
python -c "import watermap,navmap; c=watermap.load_map('maps/dragon_nest_2.json'); navmap.load_route(c); print('NODES',navmap.NODES); print('FARM',navmap.FARM_NODES); print('plan BOT->TOP',navmap.plan(navmap.FARM_NODES[0],navmap.FARM_NODES[-1])); print('plan TOP->BOT',navmap.plan(navmap.FARM_NODES[-1],navmap.FARM_NODES[0])); navmap.reset_route()"
```
Expected: NODES/FARM print the 3 platforms bottom→top; both plans are non-None (climb up via rope edges, drop down via downjump edges). If `plan TOP->BOT` is None, the downjump return chain is missing an edge — add it.

- [ ] **Step 5: Confirm the panel lists it**

Open the panel; the Farm dropdown should show `dragon_nest_2`. (No code change needed — it globs `maps/`.)

- [ ] **Step 6: Commit**

```bash
git add maps/dragon_nest_2.json
git commit -m "feat(maps): dragon_nest_2 route (3 stacked platforms, rope chain)"
```

---

### Task 5: Live smoke run + tune

**Files:**
- Modify: `maps/dragon_nest_2.json` (tuning values only)

**Interfaces:**
- Consumes: Tasks 1-4. Runs `farming_loop_nav` via the panel Farm button with `dragon_nest_2` selected.
- Produces: a tuned map JSON that completes a full bottom→mid→top→bottom rotation autonomously.

- [ ] **Step 1: Dry locate check (no movement)**

Stand on each platform; confirm the panel status `node` shows the right node (BOT/MID/TOP). If a platform reads `None`, widen that node's band or fix `map_box`. If two platforms collide onto one node, tighten bands.

- [ ] **Step 2: Rope-climb tune**

Start a farm run. Watch each `climb_rope_hop`: if she misses the rope, adjust that edge's `grab_x`; if she overshoots/undershoots the landing, adjust `land_y`. Re-run after each edit (JSON reload is per-run).

- [ ] **Step 3: Downjump return tune**

At the top, confirm she drops back to the bottom (single downjump or chain). Adjust `target_x`/split into intermediate downjumps if she lands on a wrong ledge.

- [ ] **Step 4: Deplete + sweep tune**

Confirm the reactive YOLO count drives rotation: she clears a platform (sweep left/right via STAND/WALK) and rotates when it runs dry. Tune `deplete_threshold` (raise if she leaves early, lower if she lingers) and, if needed, the loop's `stand_secs`.

- [ ] **Step 5: Full-loop verification**

Let it run several full rotations. Verify: cycles all 3 platforms in order, wraps top→bottom, no stuck states, enemy/lie-check safety still fire, and the old `dragon_nest` map still farms unchanged (select it and confirm one rotation).

- [ ] **Step 6: Commit the tuned config**

```bash
git add maps/dragon_nest_2.json
git commit -m "chore(maps): live-tune dragon_nest_2 (ropes, drop, deplete)"
```

---

## Self-Review

- **Spec coverage:** §1 map JSON → Task 4; §2 loop generalizations: route-load/minimap/map_box → Tasks 2-3, start node → Task 3, `next_farm_target` cycle → Task 1, `execute_edge` params → already data-driven (verified, Global Constraints) so consumed in Task 4's JSON, direct-dot locate → reused (`_nav_locate` unchanged, confirmed no scroll-pin in `get_character_full`); §3 detection → reused (Task 4 detector block); §4 authoring workflow → Task 4; §5 detect/deplete → Task 5 §4; §6 safety → reused, gated in Task 3; §7 testing → Tasks 1-2 unit, Task 5 live; §8 unknowns → resolved in Tasks 4-5. All covered.
- **Placeholder scan:** No TBD/TODO; every code step shows real code; live-tuning steps name the exact JSON keys to change.
- **Type consistency:** `_apply_nav_map(cfg)->bool` used consistently (Tasks 2-3); `next_farm_target` signature unchanged (Task 1); edge param names `grab_x`/`land_y`/`target_x` match `execute_edge` everywhere. `home = navmap.FARM_NODES[0]` used in all three former `"TOP_FARM"` spots.
- **Old-nest regression guard:** made explicit (Global Constraints + Task 3 Step 5) and re-verified live (Task 5 Step 5).
