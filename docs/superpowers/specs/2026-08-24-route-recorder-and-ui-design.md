# Route Recorder + Control UI — Design

Date: 2026-08-24

Two independent subsystems, designed together, built in order:

1. **Route recorder** — record a path once (movement + key input, guided by hotkeys)
   and auto-generate the node-graph map/route, replacing the hand-tuned
   `NODES`/`_BANDS`/`EDGES` in `navmap.py`. *(Built first.)*
2. **Control UI + easy lie_check loop** — a local web control panel to start/pause/stop
   farming, recover, record routes, and passively watch for lie-check screens, replacing
   the CLI-only workflow. *(Built second.)*

Inspiration: KenYu910645/MapleStoryAutoLevelUp (record-driven routes).

---

## Goal 1 — Route Recorder

### Problem

`navmap.py`'s map is hand-tuned: rectangular `_BANDS` (minimap x/y → node name),
`EDGES` (rope/downjump transitions), and per-move constants scattered in `recovery.py`
(`ROPE_X`, `PORTAL_ROPE_X`, `RIGHT_ROPE_X`, `LOWER_R_Y_MAX`, dismount directions…). Every
value was tuned live, one fix at a time. Adding or re-tuning a map is slow and brittle.

### Decisions (from brainstorming)

- **Replay model:** auto-generate the *node-graph* (keep BFS `plan()`/`travel()` and the
  recovery executors). Not pure macro replay; not route-map painting.
- **Recording style:** guided / annotated — hotkeys mark nodes; traversals between marks
  become edges. Predictable and easy to correct.
- **Execution:** reuse the existing robust parametric primitives fed with *recorded*
  parameters. `climb_rope_hop(grab_x, land_y_max, dismount, land_node)` already takes
  exactly the values we record. Raw-macro replay is a last-resort fallback only.

### Pipeline (three stages)

**1. Record — `record_route.py` (sense-only; presses no keys)**

- Polls `recovery.get_character_full()` for `(t, x, y)` at ~30 Hz.
- Hooks the keyboard via `pynput.Listener` and logs every key down/up with timestamps.
- Hotkeys while you play manually:
  - **F10** — start / stop recording.
  - **F9** — mark a node at the current position (console prompts for a name; blank →
    auto-name `N1, N2, …`).
  - **F8** — existing pause (unchanged).
- Writes raw capture to `routes/<map>.capture.jsonl`.

**2. Generate — `build_route.py` (pure / offline; no game, fully unit-testable)**

- **Nodes:** each F9 mark → one node. Its **band** = bounding box `[ylo,yhi,xlo,xhi]` of
  the `(x,y)` samples within a short window around the mark, padded by a margin.
- **Edges:** each recorded traversal between two consecutive marks → one edge, classified
  by a pure function `classify_traversal(samples, key_events)`:
  - `y` decreased while **Up** held → **rope**; extract `grab_x` (x where Up+jump began),
    `land_y` (destination dwell y), `dismount` (`left`/`right`/`None` from the L/R key
    tapped after the climb).
  - `y` increased with a **jump** + direction → **downjump** (params: none needed beyond
    src/dst; executor walks to the src home and jumps toward dst).
  - only L/R, `y` roughly flat → **walk** (param: target x = dst home).
- Emits `routes/<map>.route.json`:
  ```json
  {
    "map": "blue_dragon",
    "nodes": [{"name": "TOP_FARM", "band": [0, 95, 60, 140]}],
    "edges": [{"src": "PORTAL_BOT", "dst": "LOWER_R", "kind": "rope",
               "params": {"grab_x": 132, "land_y": 151, "dismount": "right"}}]
  }
  ```

**3. Execute — `navmap` loads the JSON**

- `navmap.load_route(path)` replaces module-level `NODES`, `_BANDS`, `EDGES` from JSON.
- **Backward compatible:** if no route file is given/found, `navmap` keeps today's
  hard-coded graph verbatim. Migration is per-map and opt-in.
- `classify_node`, `plan`, `neighbors`, `travel` are unchanged.
- `recovery.EDGE_ACTIONS` becomes **data-driven** — dispatch by `edge["kind"]`:
  - `rope` → `climb_rope_hop(**edge["params"], land_node=edge["dst"])` *(exists today)*.
  - `walk` → new `walk_edge(edge)` = `walk_to_x(params.target_x)`.
  - `downjump` → new `downjump_edge(edge)` = walk to src home, jump toward dst, confirm.
  - unclassified → `replay_macro(edge)` fallback (raw recorded key timeline).
- The bespoke composites (`recover_to_farming`, `recover_from_portal`) stay as-is; a
  recorded route may reference them by keeping a `kind:"macro"`/named executor when a hop
  genuinely needs the adaptive multi-rope recovery logic.

### Components & boundaries

| Unit | Purpose | Depends on |
|------|---------|-----------|
| `record_route.py` | Capture (t,x,y) + key events + marks to `.capture.jsonl` | `recovery.get_character_full`, `pynput` |
| `build_route.py` | Pure transform capture → `route.json` (nodes+edges) | stdlib only |
| `navmap.load_route` | Load route.json into the graph; fallback to hard-coded | stdlib |
| `recovery` executors | `walk_edge`, `downjump_edge`, `replay_macro`; data-driven `EDGE_ACTIONS` | existing primitives |

### Testing

- `build_route.py`: unit tests on hand-written synthetic captures — one per edge kind
  (rope up, downjump, walk), node-band computation, auto-naming, degenerate/empty capture.
- `navmap.load_route`: round-trip a route.json → graph; fallback when file absent; existing
  `test_navmap.py` invariants still hold (every edge has an executor).
- Recording + live execution: verified by the user in-game (sense-only recorder is
  zero-risk; execution tested on a bounded `nav` run first).

---

## Goal 2 — Control UI + easy lie_check loop

### Problem

Control is CLI-only (`python recovery.py runnav`, F8 to start/pause). No at-a-glance
status, and no one-click way to run a light "just watch for lie-check" loop.

### Design

**`panel.py`** — local web control panel using stdlib `http.server` (same pattern as
`label_server.py`; **zero new dependencies**). Open `http://localhost:8080`.

- **Controls:** Start / Pause / Stop farming (`runnav`); Recover to top; Record route
  (start / stop / mark-node with a name field); **Watch-only** (runs just the lie_check
  ticks in a loop, no movement — the quick, easy, low-risk loop).
- **Live status (polled):** current node, reactive dragon count, running/paused/stopped,
  and a large **lie_check banner** (green OK / red "NEEDS HUMAN") from the alarm state
  already tracked in `recovery.py` (`_fast_alert` / `_full_alert`).

**Architecture:** the bot runs in a **background thread**, not a subprocess.

- The UI sets `kb.pause` and reads a shared `STATUS` dict; the existing F8 hotkey and every
  embedded `lie_check_*_tick` keep working unchanged.
- Thin refactor of `recovery.py`: (a) publish a small `STATUS` dict updated in
  `farming_loop_nav` (current node, count, state); (b) expose a `is_lie_check_active()`
  reading the alert controllers; (c) make `farming_loop_nav` and `record_route` callable in
  a thread with a cooperative stop flag. No rewrite of the loop body.
- Only one worker thread runs at a time (farm XOR record XOR watch); the panel enforces it.

### Components & boundaries

| Unit | Purpose | Depends on |
|------|---------|-----------|
| `panel.py` | HTTP UI: serve page, accept control POSTs, report STATUS JSON | `recovery`, `record_route`, stdlib |
| `recovery.STATUS` + `is_lie_check_active()` | Shared, thread-safe-enough status surface | existing globals |
| worker-thread wrappers | run farm / record / watch with a stop flag | existing loops |

### Testing

- `panel.py`: the control-state machine (which worker is allowed to start, stop
  transitions) unit-tested without the game by injecting fake worker callables.
- STATUS surface: unit test that `farming_loop_nav` updates the dict (inject fake
  locate/execute, run a couple of iterations).
- UI wiring: verified by the user in the browser against a live run.

---

## Build order

1. Recorder: `record_route.py` → `build_route.py` (+ tests) → `navmap.load_route` +
   data-driven `EDGE_ACTIONS` (+ tests) → live verify by recording the current map and
   running it.
2. UI: `recovery.STATUS`/`is_lie_check_active()` + thread wrappers → `panel.py` → live
   verify in the browser.

## Non-goals (YAGNI)

- No multi-map switching UI (route files are chosen by path/name; one map at a time).
- No in-browser route editor/visualizer (guided recording + JSON is enough; revisit only
  if recordings prove hard to correct).
- No auth / remote access (localhost only).
- No replacement of the bespoke `recover_to_farming` recovery chain — it stays.
