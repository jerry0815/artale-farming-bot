# Design: node-based map navigation + dragon-count-driven rotation

**Date:** 2026-08-10
**Working dir:** `C:\jerry\toy_work\maple\auto_train`
**Status:** DESIGN (approved in brainstorming; pending written-spec review)
**Related:** `HUMANIZE_HANDOFF.md` (the tuned movement/farming system this builds on)

---

## 1. Objective

Two capabilities on top of the existing tuned farming system:

1. **Node-based map** — replace the hand-coded, one-off transition functions
   (`go_to_bottom`, `recover_to_farming`, `rest_to_bottom`, …) with an explicit
   directed graph of platforms (nodes) and moves (edges), so routing between any two
   platforms is *computed* (shortest path) instead of hand-written per case.
2. **Dragon detection** — count blue-wing dragons on the current farming platform and,
   when depleted (`< 2` dragons, sampled over a few seconds), **path to the other
   farming platform** instead of farming a dead platform on a blind timer.

Both are additive and low-risk: new modules whose edges *call* the existing live-verified
primitives. The current `farming_loop_split` stays as a working fallback.

---

## 2. Decisions (locked in brainstorming)

- **Detection method:** port the reference project
  `C:\jerry\toy_work\maple\MapleStoryAutoLevelUp`'s template-matching approach —
  match the 49 green-screen `blue_wing_dragon` sprites (chroma-keyed → mask) against a
  screen-frame ROI. Reuse `detection.py: apply_nms`.
- **Move policy:** rotate **TOP ↔ BOTTOM**. When the current farming platform's dragon
  count falls below `2` (median over a short sample window, to ignore respawn/death
  flicker), `travel()` to the other farming node.
- **Integration:** **new modules** `navmap.py` + `monsters.py`; graph **edges reuse the
  tuned primitives** in `recovery.py` (`walk_to_x`, `climb_and_jump`, `drop_to_fallen`,
  `rest_to_bottom`, `_down_jump`, `stable_char`, `get_character_color`, …). New
  `farming_loop_nav()` uses them. `farming_loop_split` is left untouched.

---

## 3. Map model (derived from the reference minimap)

The node map is read off the reference minimap
`MapleStoryAutoLevelUp\minimaps\blue_dragon_nest\route_rest.png` (which paints platforms
and **three** ropes), and it aligns with `auto_train`'s live calibration: the painted
center rope sits at **x≈91**, exactly `recovery.py: ROPE_X=91`. So node coordinates read
off this image drop into `get_character_color()`'s frame with only a small live nudge — no
coordinate-transform layer.

### Nodes (minimap-frame y-bands; confirm/nudge live)

| Node          | y-band  | x-range | home x | role                         |
|---------------|---------|---------|--------|------------------------------|
| `TOP_FARM`    | ~80–90  | 66–134  | 74     | farming (dragons to right)   |
| `REST`        | ~97–103 | 67–84   | 77     | break / transit (left ledge) |
| `MID`         | ~114–126| 85–136  | —      | transit; farmable = TBD live |
| `BOTTOM_FARM` | ~146–156| 66–98   | 63     | farming (dragons to right)   |
| `LOWER_LEDGE` | ~157+   | —       | —      | recovery-only (rope can't reach; jump up to `BOTTOM_FARM`) |

### Ropes (each is its OWN edge — the key correction)

The rope is **not** one global `x=91` connector. There are three, each with its own
x-column and level-pair; `plan()` chooses which to use.

| Rope  | x-col | Connects       |
|-------|-------|----------------|
| `R_L` | ~90   | TOP ↔ MID      |
| `R_R` | ~125  | TOP ↔ MID      |
| `R_C` | ~91   | MID ↔ BOTTOM   |

### Edges

- **Rope edges** (bidirectional): align to the rope's x-column, then climb up / descend.
  Implemented via the existing rope logic (`climb_and_jump`, the alignment + jump+up grab
  from `recover_to_farming`). `R_L`, `R_R`, `R_C` as above.
- **Down-jump edges** (one-way, downward): drop through a platform gap. Implemented via
  `drop_to_fallen(DROP_X)`, `rest_to_bottom()`, `_down_jump()`. Candidate chain:
  `TOP→REST`, `REST→MID`, `MID→BOTTOM`. **Caveat:** auto_train's live code collapsed MID
  into a 3-level model (`top / rest / bottom`, all via the center rope), so the exact
  down-jump chain — which node actually drops to which, and whether REST is on the descent
  path or a side-ledge — is **finalized in the live pass**, not assumed here.
- **Recovery edge:** `LOWER_LEDGE → BOTTOM_FARM` via straight-up jump (existing recovery).

**Fixed now:** the node *set* and the three-rope topology (§3). **Finalized live:** the
down-jump chain edges above, and every edge's implementation *constants* (gap-x, exact
rope-x, climb duration) — reused from the tuned primitives or measured in the live pass.
`plan()`/`travel()` are written against the graph abstraction, so finalizing edges live is
data-only, not a code change. An annotated map image (`docs/.../node_map_annotated.png`, generated from
`route_rest.png`) ships as living documentation.

---

## 4. `navmap.py` — graph + pathfinding + travel

Pure-Python graph over the nodes/edges above.

- `NODES` — table of node → `{y_band, x_range, home_x, farmable}` classifier data.
- `EDGES` — list of `{src, dst, kind, action, params}` where `action` is a callable that
  performs the move using `recovery.py` primitives, and `kind ∈ {rope, downjump, jumpup}`.
- `locate() -> node | None` — `stable_char()` → classify by y-band (+ x to disambiguate).
  Returns `None`/`UNKNOWN` when off-map or ambiguous.
- `plan(src, dst) -> [edge]` — **BFS** shortest hop path. (Edge weights optional later:
  prefer fast/reliable edges; start with uniform-cost BFS.)
- `travel(dst, max_rounds=8) -> bool` —
  1. `cur = locate()`; if `cur == dst` return True.
  2. `path = plan(cur, dst)`; if none, treat as lost → recover toward nearest farm node.
  3. For each `edge` in path: run `edge.action()`, then **verify** `locate() == edge.dst`
     via a stable read; on mismatch re-`locate()` + re-`plan()` (within `max_rounds`).
  4. On repeated failure: release all keys and bail (safe idle), never blind-move.

This **unifies recovery and rotation**: recovery = `travel(TOP_FARM)` from wherever she
was knocked to; rotation = `travel(BOTTOM_FARM)` / `travel(TOP_FARM)`.

---

## 5. `monsters.py` — dragon detection & counting

Ported from `MapleStoryAutoLevelUp/src/engine/MapleStoryAutoLevelUp.py:
get_monsters_in_range`.

- **Templates:** load `blue_wing_dragon_*.png` (point at the reference folder or copy into
  `assets/monsters/blue_wing_dragon/`). Build a **mask from the green chroma-key
  background** (green pixels → ignored) so matching uses only the sprite silhouette.
  De-duplicate near-identical frames (many of the 49 are byte-identical sizes) to cut cost.
- `detect_dragons(frame, roi) -> [box]` — multi-template `matchTemplate`
  (`TM_SQDIFF_NORMED`, with mask) over a **playfield ROI** (right portion of the game
  screen; excludes minimap/UI to avoid false matches) → boxes → `apply_nms`.
- `count_dragons(samples=k, interval) -> int` — median count over a few frames (smooths
  death/respawn flicker).
- `is_depleted(threshold=2, sample_seconds) -> bool` — the rotation trigger.

**Known risk (flagged; live-tuning step, like the rope calibration was):** the reference
sprites may not match Artale's 150%-DPI physical grab at the same scale. Mitigations:
tunable `diff_thres`, optional template rescale, ROI restriction, frame de-dup, and a
calibration CLI `python monsters.py test` → screenshots, draws detections, prints the
count to an annotated `debug_output/` image. Detection runs on a slow cadence (every few
seconds), **not** every attack tick, so cost is bounded.

---

## 6. `farming_loop_nav()` — the integrated loop

New loop (in `recovery.py` or a thin runner), reusing everything tuned:

1. Farm the current farming node a short stint via existing `farm()` / `farm_bottom()`.
2. Periodically `count_dragons()`; if median `< 2` over the sample window →
   `travel(other_farm_node)` and switch `current_farm`.
3. Preserve all existing game logic: EXP-stuck check → panic; red-dot (`get_enemy`)
   escape → panic; F8 pause (`kb.pause`); jittered breaks (drop to `REST` → rest →
   recover); jittered skill/heal cadence.
4. Auto-recovery is free: if knocked off, `locate()` sees she's off a farm node and
   `travel()` paths her back.

CLI: `python recovery.py runnav` (full, starts paused; F8 to begin) and a bounded test
`python recovery.py nav <seconds>`.

---

## 7. Testing

- **Pure-logic unit tests** (stubbed positions, no live game): `plan()` correctness for
  every src→dst pair; `locate()` node classification including band boundaries;
  `is_depleted` decision from sampled counts; `travel()` retry/recover branches with a
  stubbed `locate`. Matches the project's existing "unit-test pure logic, tune live"
  pattern (see `HUMANIZE_HANDOFF.md §9`).
- **Detection offline test:** run `detect_dragons` against saved game screenshots; assert
  plausible counts; visually check the annotated debug image.
- **Live tuning pass** (with the user, per-action OK for any key input): calibrate node
  y-bands and the three rope x-columns; tune `diff_thres` / template scale until the
  dragon count is stable; end-to-end `runnav`.

---

## 8. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Template scale mismatch (reference sprites vs Artale 150%-DPI) | `monsters.py test` calibration CLI; tunable threshold + optional rescale; live pass |
| Detection cost (49 templates × ROI/frame) | Slow cadence, ROI restriction, frame de-dup, downscale |
| False matches (blue UI/background) | Chroma-mask matching + ROI excludes minimap/UI + NMS |
| Narrow-target moves still steppy (x=67 gap, bottom rope grab) | Unchanged — reuse existing tuned primitives; `travel()` verifies + retries, never worse than today |
| Node misclassification during buff-glow phantoms | Reuse `stable_char`/`_plausible_read` hardening already in `recovery.py` |

---

## 9. Non-goals (YAGNI)

- No adoption of the reference project's painted-PNG pixel-command routing scheme (a node
  graph fits this small discrete map better).
- No rewrite of `recovery.py`'s tuned primitives or `farming_loop_split`.
- No weighted/optimal pathfinding initially (uniform-cost BFS is enough for 5 nodes).
- No HP-based safety bail (user said not needed).
- MID as a *farmable* node is deferred (modeled as transit; can be enabled live).
