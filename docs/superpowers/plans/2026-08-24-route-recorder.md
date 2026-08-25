# Route Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record a path (character movement + key input, guided by hotkeys) once and auto-generate the node-graph map/route, replacing the hand-tuned `NODES`/`_BANDS`/`EDGES` in `navmap.py`.

**Architecture:** A sense-only recorder (`record_route.py`) logs `(t,x,y)` + key events + node marks to a `.capture.jsonl`. A pure offline transform (`build_route.py`) turns a capture into `route.json` (node bands from dwell windows; edges classified rope/downjump/walk with recorded params). `navmap.load_route()` loads the JSON into the existing graph (falling back to today's hard-coded graph when absent), and `recovery.EDGE_ACTIONS` becomes data-driven, reusing existing parametric primitives.

**Tech Stack:** Python 3, stdlib (`json`), `pynput` (recorder only), `pytest`. No new dependencies.

## Global Constraints

- No new third-party dependencies beyond what the repo already uses (`pynput`, `pytest`).
- `build_route.py` and `navmap.load_route` are **pure / offline** — no game, no screen capture, importable and unit-testable with no side effects.
- `navmap` MUST stay backward compatible: with no route file, `NODES`/`_BANDS`/`EDGES` remain exactly today's hard-coded values, and `test_navmap.py` keeps passing.
- Minimap coordinate convention: **y decreases going UP** (climbing a rope lowers y); x/y are ints in the full-minimap frame.
- Key-name normalization: `pynput` `Key.up` → `"up"`, `Key.alt_l` (JUMP) → `"alt_l"`, `Key.left`/`Key.right` → `"left"`/`"right"`, char keys → the char string. The classifier treats `up_keys={"up"}` and `jump_keys={"alt_l"}` as configurable defaults matching `recovery.JUMP`/`Key.up`.

---

### Task 1: Capture format + pure loader/splitter (`build_route.py`)

**Files:**
- Create: `build_route.py`
- Test: `tests/test_build_route.py`

**Interfaces:**
- Produces:
  - `split_records(records: list[dict]) -> tuple[list[dict], list[dict], list[dict]]`
    returning `(positions, keys, marks)` each sorted ascending by `"t"`. A record is a
    position if it has `"x"`, a mark if it has `"mark"`, else a key event.
  - Capture record shapes (JSONL, one dict per line):
    - position `{"t": float, "x": int, "y": int}`
    - key `{"t": float, "key": str, "ev": "down"|"up"}`
    - mark `{"t": float, "mark": "NODE", "name": str}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_route.py
import build_route

def test_split_records_partitions_and_sorts():
    recs = [
        {"t": 2.0, "x": 90, "y": 100},
        {"t": 0.5, "key": "up", "ev": "down"},
        {"t": 1.0, "mark": "NODE", "name": "TOP"},
        {"t": 0.1, "x": 91, "y": 101},
    ]
    positions, keys, marks = build_route.split_records(recs)
    assert [p["t"] for p in positions] == [0.1, 2.0]
    assert [k["t"] for k in keys] == [0.5]
    assert [m["name"] for m in marks] == ["TOP"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_build_route.py::test_split_records_partitions_and_sorts -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'build_route'`

- [ ] **Step 3: Write minimal implementation**

```python
# build_route.py
"""Pure, offline transform: a recorded capture -> a route.json node-graph.

No game, no screen capture. Import-safe and fully unit-testable. See
docs/superpowers/specs/2026-08-24-route-recorder-and-ui-design.md.
"""
import json


def split_records(records):
    """Partition raw capture records into (positions, keys, marks), each sorted by t.
    position: has 'x'; mark: has 'mark'; otherwise a key event."""
    positions, keys, marks = [], [], []
    for r in records:
        if "x" in r:
            positions.append(r)
        elif "mark" in r:
            marks.append(r)
        else:
            keys.append(r)
    for lst in (positions, keys, marks):
        lst.sort(key=lambda r: r["t"])
    return positions, keys, marks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_build_route.py::test_split_records_partitions_and_sorts -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add build_route.py tests/test_build_route.py
git commit -m "feat(route): pure capture record splitter"
```

---

### Task 2: Node band from a mark window

**Files:**
- Modify: `build_route.py`
- Test: `tests/test_build_route.py`

**Interfaces:**
- Produces: `node_band(positions, mark_t, window=0.6, margin=2) -> [ylo, yhi, xlo, xhi]`
  — bounding box (padded by `margin`) of positions within `±window` seconds of `mark_t`.
  Falls back to the single nearest position if none fall in the window.

- [ ] **Step 1: Write the failing test**

```python
def test_node_band_bbox_with_margin():
    positions = [
        {"t": 9.0, "x": 200, "y": 200},   # far in time -> excluded
        {"t": 10.0, "x": 60, "y": 85},
        {"t": 10.2, "x": 64, "y": 88},
        {"t": 10.4, "x": 62, "y": 86},
    ]
    band = build_route.node_band(positions, mark_t=10.2, window=0.6, margin=2)
    assert band == [83, 90, 58, 66]   # [min_y-2, max_y+2, min_x-2, max_x+2]

def test_node_band_falls_back_to_nearest_when_window_empty():
    positions = [{"t": 0.0, "x": 100, "y": 140}]
    band = build_route.node_band(positions, mark_t=99.0, window=0.6, margin=2)
    assert band == [138, 142, 98, 102]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_build_route.py -k node_band -v`
Expected: FAIL with `AttributeError: module 'build_route' has no attribute 'node_band'`

- [ ] **Step 3: Write minimal implementation**

```python
def node_band(positions, mark_t, window=0.6, margin=2):
    near = [p for p in positions if abs(p["t"] - mark_t) <= window]
    if not near:
        near = [min(positions, key=lambda p: abs(p["t"] - mark_t))]
    xs = [p["x"] for p in near]
    ys = [p["y"] for p in near]
    return [min(ys) - margin, max(ys) + margin, min(xs) - margin, max(xs) + margin]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_build_route.py -k node_band -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add build_route.py tests/test_build_route.py
git commit -m "feat(route): node band from mark window"
```

---

### Task 3: Traversal classifier (rope / downjump / walk)

**Files:**
- Modify: `build_route.py`
- Test: `tests/test_build_route.py`

**Interfaces:**
- Produces: `classify_traversal(positions, keys, up_keys={"up"}, jump_keys={"alt_l"}, dy_thresh=4) -> (kind, params)`
  where `positions`/`keys` are the records strictly between two marks (already time-sorted).
  - `kind == "rope"` when an up-key is held and y fell by more than `dy_thresh` from start
    to the minimum. `params = {"grab_x": int, "land_y": int, "dismount": "left"|"right"|None}`.
    `grab_x` = x at the first up-key down; `land_y` = last position y; `dismount` = the first
    of left/right pressed **after** the minimum-y sample (else None).
  - `kind == "downjump"` when a jump-key was pressed and y rose by more than `dy_thresh`.
    `params = {}`.
  - `kind == "walk"` otherwise. `params = {"target_x": int}` = last position x.

- [ ] **Step 1: Write the failing test**

```python
def test_classify_rope_extracts_grab_land_dismount():
    positions = [
        {"t": 0.0, "x": 132, "y": 190},
        {"t": 0.5, "x": 132, "y": 160},
        {"t": 1.0, "x": 132, "y": 151},   # min y here
        {"t": 1.4, "x": 140, "y": 151},   # dismounted right
    ]
    keys = [
        {"t": 0.1, "key": "up", "ev": "down"},
        {"t": 1.2, "key": "right", "ev": "down"},
    ]
    kind, params = build_route.classify_traversal(positions, keys)
    assert kind == "rope"
    assert params == {"grab_x": 132, "land_y": 151, "dismount": "right"}

def test_classify_downjump_when_y_rises_with_jump():
    positions = [{"t": 0.0, "x": 63, "y": 85}, {"t": 0.6, "x": 90, "y": 136}]
    keys = [{"t": 0.1, "key": "alt_l", "ev": "down"},
            {"t": 0.1, "key": "right", "ev": "down"}]
    kind, params = build_route.classify_traversal(positions, keys)
    assert kind == "downjump"
    assert params == {}

def test_classify_walk_when_flat():
    positions = [{"t": 0.0, "x": 63, "y": 85}, {"t": 0.4, "x": 112, "y": 85}]
    keys = [{"t": 0.1, "key": "right", "ev": "down"}]
    kind, params = build_route.classify_traversal(positions, keys)
    assert kind == "walk"
    assert params == {"target_x": 112}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_build_route.py -k classify -v`
Expected: FAIL with `AttributeError: ... 'classify_traversal'`

- [ ] **Step 3: Write minimal implementation**

```python
def _first_down(keys, names):
    for k in keys:
        if k.get("ev") == "down" and k.get("key") in names:
            return k
    return None


def classify_traversal(positions, keys, up_keys=("up",), jump_keys=("alt_l",), dy_thresh=4):
    up_keys, jump_keys = set(up_keys), set(jump_keys)
    y0 = positions[0]["y"]
    y_last = positions[-1]["y"]
    min_p = min(positions, key=lambda p: p["y"])   # highest point (smallest y)
    up_ev = _first_down(keys, up_keys)
    jump_ev = _first_down(keys, jump_keys)

    if up_ev is not None and (y0 - min_p["y"]) > dy_thresh:
        # grab_x = x at the first up-key down (nearest position in time)
        grab_p = min(positions, key=lambda p: abs(p["t"] - up_ev["t"]))
        dismount = None
        for k in keys:
            if k.get("ev") == "down" and k.get("key") in ("left", "right") and k["t"] >= min_p["t"]:
                dismount = k["key"]; break
        return "rope", {"grab_x": grab_p["x"], "land_y": y_last, "dismount": dismount}

    if jump_ev is not None and (y_last - y0) > dy_thresh:
        return "downjump", {}

    return "walk", {"target_x": positions[-1]["x"]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_build_route.py -k classify -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add build_route.py tests/test_build_route.py
git commit -m "feat(route): traversal classifier (rope/downjump/walk)"
```

---

### Task 4: Assemble the full route from a capture

**Files:**
- Modify: `build_route.py`
- Test: `tests/test_build_route.py`

**Interfaces:**
- Consumes: `split_records`, `node_band`, `classify_traversal`.
- Produces:
  - `build_route(records, map_name, farm_nodes=None, **cfg) -> dict` shaped
    `{"map", "nodes":[{"name","band"}], "edges":[{"src","dst","kind","params"}], "farm_nodes":[...]}`.
    One node per mark (later duplicate-name marks reuse the first node's slot but are still
    valid edge endpoints). One edge per consecutive pair of marks, using the positions/keys
    strictly between their timestamps.
  - `load_capture(path) -> list[dict]` reads a `.capture.jsonl` (one JSON dict per non-blank line).
  - `write_route(route, path) -> None` writes pretty JSON.
  - `main()` CLI: `python build_route.py <capture.jsonl> <out.route.json> <map_name> [farm_nodes_csv]`.

- [ ] **Step 1: Write the failing test**

```python
def test_build_route_end_to_end():
    recs = [
        {"t": 0.0, "mark": "NODE", "name": "TOP"},
        {"t": 0.0, "x": 63, "y": 85},
        {"t": 0.2, "x": 63, "y": 85},
        {"t": 0.3, "key": "right", "ev": "down"},
        {"t": 0.6, "x": 112, "y": 85},
        {"t": 0.8, "mark": "NODE", "name": "FAR"},
        {"t": 0.8, "x": 112, "y": 85},
    ]
    route = build_route.build_route(recs, "blue_dragon", farm_nodes=["TOP", "FAR"])
    assert route["map"] == "blue_dragon"
    assert route["farm_nodes"] == ["TOP", "FAR"]
    assert [n["name"] for n in route["nodes"]] == ["TOP", "FAR"]
    assert route["nodes"][0]["band"] == [83, 87, 61, 65]
    assert route["edges"] == [
        {"src": "TOP", "dst": "FAR", "kind": "walk", "params": {"target_x": 112}}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_build_route.py::test_build_route_end_to_end -v`
Expected: FAIL with `AttributeError: ... 'build_route'`

- [ ] **Step 3: Write minimal implementation**

```python
def build_route(records, map_name, farm_nodes=None, **cfg):
    positions, keys, marks = split_records(records)
    nodes, seen = [], set()
    for m in marks:
        if m["name"] in seen:
            continue
        seen.add(m["name"])
        nodes.append({"name": m["name"],
                      "band": node_band(positions, m["t"],
                                        window=cfg.get("window", 0.6),
                                        margin=cfg.get("margin", 2))})
    edges = []
    for a, b in zip(marks, marks[1:]):
        seg_pos = [p for p in positions if a["t"] <= p["t"] <= b["t"]]
        seg_keys = [k for k in keys if a["t"] <= k["t"] <= b["t"]]
        if len(seg_pos) < 2 or a["name"] == b["name"]:
            continue
        kind, params = classify_traversal(seg_pos, seg_keys,
                                          up_keys=cfg.get("up_keys", ("up",)),
                                          jump_keys=cfg.get("jump_keys", ("alt_l",)),
                                          dy_thresh=cfg.get("dy_thresh", 4))
        edges.append({"src": a["name"], "dst": b["name"], "kind": kind, "params": params})
    return {"map": map_name, "nodes": nodes, "edges": edges,
            "farm_nodes": farm_nodes or []}


def load_capture(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_route(route, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(route, fh, indent=2)


def main():
    import sys
    if len(sys.argv) < 4:
        print("usage: python build_route.py <capture.jsonl> <out.route.json> <map> [farm_csv]")
        return
    cap, out, mp = sys.argv[1], sys.argv[2], sys.argv[3]
    farm = sys.argv[4].split(",") if len(sys.argv) > 4 else []
    route = build_route(load_capture(cap), mp, farm_nodes=farm)
    write_route(route, out)
    print(f"[build_route] {len(route['nodes'])} nodes, {len(route['edges'])} edges -> {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_build_route.py -v`
Expected: PASS (all build_route tests)

- [ ] **Step 5: Commit**

```bash
git add build_route.py tests/test_build_route.py
git commit -m "feat(route): assemble route.json from a capture (+CLI)"
```

---

### Task 5: `navmap.load_route` with backward-compatible fallback

**Files:**
- Modify: `navmap.py` (add loader; move the hard-coded graph into a restorable default)
- Test: `tests/test_navmap.py` (add cases; keep existing)

**Interfaces:**
- Consumes: a route dict or a path to `route.json` (shape from Task 4).
- Produces:
  - `navmap.load_route(route_or_path) -> None` — sets module `NODES`, `_BANDS`, `EDGES`,
    `FARM_NODES` from the route. `_BANDS` entries stay `(name, ylo, yhi, xlo, xhi)` tuples so
    `classify_node` is unchanged. `EDGES` entries become
    `{"src","dst","kind", **params}` (params flattened, e.g. `grab_x`), preserving the
    `kind` key; a `"rope"`-metadata key is no longer required.
  - `navmap.reset_route() -> None` — restore the built-in hard-coded graph.
- Constraint: importing `navmap` with no `load_route` call leaves today's values intact.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_navmap.py (add)
import navmap

def test_load_route_replaces_graph_then_reset_restores():
    default_nodes = list(navmap.NODES)
    route = {
        "map": "t", "farm_nodes": ["A"],
        "nodes": [{"name": "A", "band": [0, 10, 0, 10]},
                  {"name": "B", "band": [20, 30, 0, 10]}],
        "edges": [{"src": "A", "dst": "B", "kind": "walk", "params": {"target_x": 5}}],
    }
    navmap.load_route(route)
    assert navmap.NODES == ["A", "B"]
    assert navmap.FARM_NODES == ["A"]
    assert navmap.classify_node(5, 5) == "A"
    assert navmap.classify_node(5, 25) == "B"
    e = navmap.EDGES[0]
    assert e["src"] == "A" and e["dst"] == "B" and e["kind"] == "walk" and e["target_x"] == 5
    assert navmap.plan("A", "B") == [navmap.EDGES[0]]
    navmap.reset_route()
    assert navmap.NODES == default_nodes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_navmap.py::test_load_route_replaces_graph_then_reset_restores -v`
Expected: FAIL with `AttributeError: module 'navmap' has no attribute 'load_route'`

- [ ] **Step 3: Write minimal implementation**

In `navmap.py`, after the existing `NODES`/`FARM_NODES`/`_BANDS`/`EDGES` definitions, snapshot
the defaults and add the loader (place near the top-level constants):

```python
import json as _json

# Built-in defaults (the hand-tuned graph) so load_route is reversible.
_DEFAULT_NODES = list(NODES)
_DEFAULT_FARM_NODES = list(FARM_NODES)
_DEFAULT_BANDS = list(_BANDS)
_DEFAULT_EDGES = [dict(e) for e in EDGES]


def load_route(route_or_path):
    """Replace the module graph from a route dict or route.json path. Backward
    compatible: never called -> the hand-tuned defaults above stay in force."""
    global NODES, FARM_NODES, _BANDS, EDGES
    route = route_or_path
    if isinstance(route_or_path, str):
        with open(route_or_path, encoding="utf-8") as fh:
            route = _json.load(fh)
    NODES = [n["name"] for n in route["nodes"]]
    FARM_NODES = list(route.get("farm_nodes", []))
    _BANDS = [(n["name"], *n["band"]) for n in route["nodes"]]
    EDGES = [dict(src=e["src"], dst=e["dst"], kind=e.get("kind", "walk"),
                  **e.get("params", {})) for e in route["edges"]]


def reset_route():
    """Restore the built-in hand-tuned graph."""
    global NODES, FARM_NODES, _BANDS, EDGES
    NODES = list(_DEFAULT_NODES)
    FARM_NODES = list(_DEFAULT_FARM_NODES)
    _BANDS = list(_DEFAULT_BANDS)
    EDGES = [dict(e) for e in _DEFAULT_EDGES]
```

- [ ] **Step 4: Run the full navmap suite to verify pass + no regressions**

Run: `python -m pytest tests/test_navmap.py -v`
Expected: PASS (new test + all existing)

- [ ] **Step 5: Commit**

```bash
git add navmap.py tests/test_navmap.py
git commit -m "feat(nav): navmap.load_route + reset_route (backward compatible)"
```

---

### Task 6: Data-driven edge executors in `recovery.py`

**Files:**
- Modify: `recovery.py` (add `walk_edge`, `downjump_edge`, `replay_macro`; make `execute_edge` dispatch by `kind`)
- Test: `tests/test_edge_dispatch.py` (new; uses fakes, no game)

**Interfaces:**
- Consumes: edge dicts of shape `{"src","dst","kind", **params}` (from `navmap.load_route`),
  plus the existing named executors for `kind == "macro"` / built-in edges.
- Produces:
  - `recovery.execute_edge(edge)` dispatches: `rope` →
    `climb_rope_hop(grab_x, land_y, dismount=..., land_node=edge["dst"])`; `walk` →
    `walk_edge(edge)`; `downjump` → `downjump_edge(edge)`; else if `(src,dst)` in the
    existing `EDGE_ACTIONS` table use that (keeps the hand-built recovery composites);
    else `replay_macro(edge)` (currently returns False + logs — reserved fallback).
  - `walk_edge(edge) -> bool` = `walk_to_x(edge["target_x"])`.
  - `downjump_edge(edge) -> bool` = walk toward `edge` dst then jump; verify via re-locate.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_dispatch.py
import recovery

def test_execute_edge_dispatches_rope_with_recorded_params(monkeypatch):
    calls = {}
    def fake_hop(grab_x, land_y_max, dismount=None, land_node=None, cap=12.0):
        calls.update(grab_x=grab_x, land_y_max=land_y_max, dismount=dismount, land_node=land_node)
        return True
    monkeypatch.setattr(recovery, "climb_rope_hop", fake_hop)
    edge = {"src": "PORTAL_BOT", "dst": "LOWER_R", "kind": "rope",
            "grab_x": 132, "land_y": 151, "dismount": "right"}
    assert recovery.execute_edge(edge) is True
    assert calls == {"grab_x": 132, "land_y_max": 151, "dismount": "right", "land_node": "LOWER_R"}

def test_execute_edge_dispatches_walk(monkeypatch):
    seen = {}
    monkeypatch.setattr(recovery, "walk_to_x", lambda x, **k: seen.setdefault("x", x) or True)
    edge = {"src": "TOP", "dst": "FAR", "kind": "walk", "target_x": 112}
    assert recovery.execute_edge(edge) is True
    assert seen["x"] == 112
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_edge_dispatch.py -v`
Expected: FAIL (dispatch keys on the `(src,dst)` table, not `kind`; rope branch missing)

- [ ] **Step 3: Write minimal implementation**

Replace `execute_edge` (currently at `recovery.py:873`) and add helpers above it:

```python
def walk_edge(edge):
    return bool(walk_to_x(edge["target_x"]))


def downjump_edge(edge):
    # walk to the recorded/adjacent x then jump in that direction; verify by re-locating.
    tx = edge.get("target_x")
    if tx is not None:
        walk_to_x(tx)
    key = Key.right if edge.get("dismount") != "left" else Key.left
    kb.safe_press(key); kb.safe_press(JUMP); time.sleep(0.12)
    kb.safe_release(JUMP); time.sleep(0.2); kb.safe_release(key)
    time.sleep(0.3)
    x, y = stable_char(3)
    return navmap.classify_node(x, y) == edge["dst"]


def replay_macro(edge):
    print(f"[nav] no typed executor for {edge['src']}->{edge['dst']} (kind={edge.get('kind')})")
    return False


def execute_edge(edge):
    kind = edge.get("kind")
    if kind == "rope" and "grab_x" in edge:
        return bool(climb_rope_hop(edge["grab_x"], edge["land_y"],
                                   dismount=edge.get("dismount"), land_node=edge["dst"]))
    if kind == "walk" and "target_x" in edge:
        return walk_edge(edge)
    if kind == "downjump":
        return downjump_edge(edge)
    fn = EDGE_ACTIONS.get((edge["src"], edge["dst"]))   # hand-built composites (recover chains)
    if fn is not None:
        return bool(fn())
    return replay_macro(edge)
```

- [ ] **Step 4: Run tests to verify pass + no regression**

Run: `python -m pytest tests/test_edge_dispatch.py tests/test_navmap.py -v`
Expected: PASS. (Existing hard-coded EDGES have no `kind` → fall through to `EDGE_ACTIONS`, unchanged.)

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_edge_dispatch.py
git commit -m "feat(nav): data-driven execute_edge (rope/walk/downjump + composite fallback)"
```

---

### Task 7: The recorder (`record_route.py`) + CLI wiring

**Files:**
- Create: `record_route.py`
- Modify: `recovery.py` (add a `record-route <map>` CLI command that calls it)

**Interfaces:**
- Consumes: `recovery.get_character_full`, `recovery.focus`, `keyboard` (F8 pause), `pynput`.
- Produces:
  - `record_route.record(get_xy, out_path, poll_hz=30, is_paused=lambda: False) -> dict`
    — sense-only loop. Logs position samples at `poll_hz`; a `pynput.Listener` logs key
    down/up (normalized names) and handles hotkeys: **F10** stop, **F9** mark (reads a name
    from stdin; blank → `N{n}`). Appends every record as a JSON line to `out_path` and also
    returns the in-memory list. Presses NO keys.
  - This function takes `get_xy`/`is_paused` injected so its record-assembly is unit-testable
    without the game; the live listener/timing is exercised only via the CLI.

**Note:** live behavior (real hotkeys, real game) is **verified by the user in-game** — the
recorder is sense-only and zero-risk. The unit test covers only the pure record-appending.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_record_route.py
import json, itertools, record_route

def test_record_writes_position_samples(tmp_path):
    out = tmp_path / "cap.jsonl"
    xs = itertools.chain([(63, 85), (64, 86)], itertools.repeat((64, 86)))
    ticks = iter([0.0, 0.05, 0.10, 0.20])   # 4 clock reads then stop
    def fake_clock(): return next(ticks, 1.0)
    def fake_xy(): return next(xs)
    recs = record_route.record(fake_xy, str(out),
                               poll_hz=1000, is_paused=lambda: False,
                               stop_after=3, clock=fake_clock, sleep=lambda s: None)
    pos = [r for r in recs if "x" in r]
    assert len(pos) == 3
    assert pos[0]["x"] == 63 and pos[0]["y"] == 85
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_record_route.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'record_route'`

- [ ] **Step 3: Write minimal implementation**

```python
# record_route.py
"""Sense-only route recorder: logs (t,x,y) + key events + node marks to a
.capture.jsonl while you play manually. Presses NO keys. build_route.py turns
the capture into a route.json. See the 2026-08-24 route-recorder spec."""
import json, time as _time


def record(get_xy, out_path, poll_hz=30, is_paused=lambda: False,
           stop_after=None, clock=_time.time, sleep=_time.sleep, listener=None):
    """Poll get_xy() -> (x,y) at poll_hz, appending {t,x,y} records to out_path.
    Stops after `stop_after` samples (test hook) or when `listener` signals stop.
    Pure w.r.t. the game: no key presses. Returns the in-memory record list."""
    period = 1.0 / poll_hz
    t0 = clock()
    recs = []
    with open(out_path, "w", encoding="utf-8") as fh:
        n = 0
        while True:
            if listener is not None and getattr(listener, "stopped", False):
                break
            if not is_paused():
                x, y = get_xy()
                if x is not None and x >= 0:
                    r = {"t": round(clock() - t0, 3), "x": int(x), "y": int(y)}
                    recs.append(r); fh.write(json.dumps(r) + "\n"); fh.flush()
                    n += 1
                    if stop_after is not None and n >= stop_after:
                        break
            sleep(period)
    return recs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_record_route.py -v`
Expected: PASS

- [ ] **Step 5: Add the live CLI (marks + key logging + hotkeys) and commit**

Add to `record_route.py` a `record_live(map_name)` that wires a real `pynput.Listener`
(F10 stop via a `.stopped` flag; F9 → `input()` a name and append a mark record; every
key down/up appends a normalized key record) around `record`, using
`recovery.get_character_full` for `get_xy` and `keyboard.pause` for `is_paused`.
Then add to `recovery.py`'s `__main__` dispatch:

```python
    elif cmd == "record-route":
        mp = sys.argv[2] if len(sys.argv) > 2 else "map"
        if not focus():
            print("no focus"); sys.exit(1)
        import record_route
        record_route.record_live(mp)
```

```bash
git add record_route.py recovery.py tests/test_record_route.py
git commit -m "feat(route): sense-only recorder + record-route CLI"
```

---

### Task 8: Live verification (user-driven) + wiring the recorded route

**Files:**
- Create (by recording): `routes/blue_dragon.capture.jsonl`, `routes/blue_dragon.route.json`
- Modify: `recovery.py` (optional `--route <path>` flag on `runnav`/`nav` that calls `navmap.load_route`)

**Interfaces:**
- Consumes: everything above.
- Produces: a `runnav`/`nav` that, given `--route routes/blue_dragon.route.json`, farms the
  recorded map; with no flag it uses today's hard-coded graph (unchanged).

- [ ] **Step 1** (user, in-game): `python recovery.py record-route blue_dragon` — walk the map, F9 at each platform (name them TOP_FARM/BOTTOM_FARM/…), F10 to stop.
- [ ] **Step 2**: `python build_route.py routes/blue_dragon.capture.jsonl routes/blue_dragon.route.json blue_dragon TOP_FARM,BOTTOM_FARM`
- [ ] **Step 3**: eyeball `route.json` — node bands and edge kinds/params sane vs the hand-tuned values.
- [ ] **Step 4** (user, in-game): `python recovery.py nav 90 --route routes/blue_dragon.route.json` — confirm travel/farm works.
- [ ] **Step 5: Commit the route files**

```bash
git add routes/blue_dragon.route.json recovery.py
git commit -m "feat(route): recorded blue_dragon route + --route flag"
```

---

## Self-Review

- **Spec coverage:** record (Task 7) ✓; generate nodes/bands (Tasks 2,4) ✓; edge classify with grab_x/land_y/dismount (Task 3) ✓; route.json format (Task 4) ✓; navmap loader + fallback (Task 5) ✓; data-driven executors reusing `climb_rope_hop` (Task 6) ✓; live verify (Task 8) ✓.
- **Placeholder scan:** none — every code step is concrete.
- **Type consistency:** `route.json` edges carry `params`; `navmap.load_route` flattens them into `EDGES` entries (`grab_x`, `target_x`, …); `execute_edge` reads the flattened keys. Consistent across Tasks 4→5→6.
- **Note:** live-only behavior (real hotkeys, in-game travel) is verified by the user; pure logic is fully unit-tested.
