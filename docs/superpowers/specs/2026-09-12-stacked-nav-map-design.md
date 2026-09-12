# Stacked JSON-route nav map (2nd dragon-nest farm)

**Date:** 2026-09-12
**Status:** Design approved, ready for implementation plan
**Map:** 米納爾森林 — 遭破壞的龍之巢穴 (Minar Forest — Destroyed Dragon's Nest). Working
map slug: `dragon_nest_2` (mirrors `deep_sea_2`).

## Goal

Add a second land-farming map: three **stacked** platforms linked by a **rope chain**.
The character (箭神 / Bow Master, ranged) parks near a rope and sweeps left/right to wipe
each platform, then climbs to the next; when the top is cleared it drops back to the
bottom and repeats. Mobs are the same LV.105 幼年龍 / LV.110 幼龍保護者 dragons the
existing YOLO dragon detector already handles.

## Key context / constraints

- **Reuse, not rebuild.** The land-farming state machine already exists in
  `farming_loop_nav` ([recovery.py](../../../recovery.py)): `_stand_shoot` (park + fire),
  `_walk_shoot_sweep` (out-and-back sweep = "wipe left/right"), reactive YOLO
  deplete-check, and depletion → rotate. The rope executor `climb_rope_hop(grab_x,
  land_y_max, ...)` is already parametric.
- **Fixed minimap (no scroll-pin).** Confirmed with the user: the whole map fits in the
  minimap and the player dot moves within it. So position sensing is **direct-dot**
  (water-map style, `map_box` + dot detection), NOT the old nest's scroll-pin path.
- **The old nest stays untouched.** `farming_loop_nav` is currently hardwired to the old
  nest via `navmap.py`'s built-in geometry. All changes here are **opt-in**: the loop
  only loads a per-map route when the map JSON carries one; otherwise it falls back to the
  built-in defaults exactly as today.
- **Approach A chosen** over forking a dedicated loop (B) or a second hardcoded route in
  navmap (C). A turns "add a nav map" into "record a route + drop a JSON," matching how
  water maps already work, and pays off on the next map.

## Architecture

```
maps/dragon_nest_2.json  (loop:"nav" + nodes/edges/farm_nodes + rope params + detector)
        │  watermap.load_map()
        ▼
farming_loop_nav(cfg)  ──calls──▶ navmap.load_route(cfg)   # NEW: opt-in geometry
        │                          (old nest: no route in cfg → built-in defaults, unchanged)
        ├── locate:  direct-dot read → navmap.classify_node(x,y)   # water-style, not scroll-pin
        ├── farm:    _stand_shoot / _walk_shoot_sweep  (sweep left/right)   # reused
        ├── deplete: monsters.detect_dragons_yolo (reactive single-frame)   # reused, same dragons
        └── rotate:  next_farm_target (N stacked) → navmap.travel → execute_edge → climb_rope_hop
```

Each unit stays independently testable: `navmap` is pure graph/geometry logic; the loop
orchestrates; `climb_rope_hop`/`execute_edge` execute one edge; the detector counts.

## Components

### 1. New map JSON — `maps/dragon_nest_2.json` (deliverable)

Same schema as the water maps, minus swim fields:

- `loop: "nav"`, `name`, `map`, `note`
- `minimap`: `{x, y, w, h}` crop for the dot read
- `map_box`: `[x_min, x_max, y_min, y_max]` valid-dot box (reject phantom reads)
- `nodes`: the 3 farm platforms + any transit/rest nodes; each `{name, band:[x_lo,x_hi,y_lo,y_hi]}`
- `farm_nodes`: the 3 platform names, **ordered bottom → top**
- `edges`:
  - `{src, dst, kind:"rope", params:{grab_x, land_y_max}}` — climb up
  - `{src, dst, kind:"downjump", params:{drop_x}}` — drop down (bottom-return chain)
- detector: reuse dragon YOLO (`monsters.load_dragon_model()`); `deplete_threshold`
- cadence: `breaks`, `break_every`, `rest_range`, `buff_groups` (箭神 keys)

### 2. Loop generalizations (small, opt-in code changes)

1. **`farming_loop_nav`**: if `cfg` carries `nodes`, call `navmap.load_route(cfg)`; else
   keep built-in defaults (old nest path unchanged). Restore defaults on exit so runs
   don't leak geometry into each other.
2. **Start node**: begin at `FARM_NODES[0]` (bottom) instead of the hardcoded
   `current = "TOP_FARM"`.
3. **`navmap.next_farm_target`**: generalize the 2-way toggle into a cycle over the
   ordered `FARM_NODES` (bottom → … → top → wrap to bottom).
4. **`execute_edge`**: read `grab_x`/`land_y_max` (rope) and `drop_x` (downjump) from
   `edge["params"]`, so `climb_rope_hop` / the drop are fully data-driven per edge.
5. **locate**: use the direct-dot read (as the water map does) → `classify_node`, instead
   of the scroll-pin `stable_char` path. Gate on the map being fixed-minimap.

### 3. Detection / deplete (reused)

Reactive single-frame YOLO count via `monsters.detect_dragons_yolo` with the existing
dragon model — same mobs, no retrain expected. `deplete_threshold` per map. Falls back to
the motion count when no model is present.

## Data flow (one rotation)

1. Locate dot → `classify_node` → current farm node.
2. If on a farm node: alternate `_stand_shoot` / `_walk_shoot_sweep`, polling the reactive
   count while shooting.
3. Count `< deplete_threshold` → `next_farm_target` returns the next node up.
4. `navmap.travel` plans the path; `execute_edge` runs the rope hop (`climb_rope_hop` with
   the edge's `grab_x`/`land_y_max`).
5. At the top and depleted → `next_farm_target` wraps to the bottom; `travel` runs the
   downjump chain (`drop_x` per edge).

## Error handling / safety (all inherited from `farming_loop_nav`)

Red-dot/enemy alarm + panic, EXP-drop guard, `lie_check` monster/curse popup handler,
unmapped-position → `recover_to_farming`, `'n'` pause, jittered breaks and buffs. No new
safety code.

## Testing / live-tuning

- **Unit tests** (pure logic, add to `tests/`): `next_farm_target` cycles 3 nodes correctly
  (incl. top→bottom wrap); `classify_node` returns the right node for sample band points;
  `plan` finds bottom→top and top→bottom paths.
- **Live tuning** (like the water map): minimap crop + `map_box`; each rope `grab_x` /
  `land_y_max`; downjump `drop_x`; `deplete_threshold`; sweep timings. Verify counts with
  the panel's detection preview.

## Geometry authoring workflow (no new tooling)

1. `record_route.py` — walk the map once; mark the 3 platforms + rope grab points + drop
   points.
2. `build_route.py` — emit `maps/dragon_nest_2.json`.
3. Panel Farm dropdown picks it up automatically (any file in `maps/`).

## Open unknowns (resolve during recording)

- Exact minimap crop + `map_box` for this map.
- Whether the bottom-return is one downjump chain or needs intermediate transit nodes.
- Rope grab columns — the stacked ropes may not share one X, so each rope edge likely
  needs its own `grab_x`.

## Out of scope (YAGNI)

- Retraining the dragon detector (same sprites).
- New minimap scroll-pin logic (map is fixed).
- Generalizing the water loop; this is the nav path only.
- Multi-rope branching / non-stacked circuits (this map is a single stacked chain).
