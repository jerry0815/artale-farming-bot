# Node-Map Navigation + Dragon-Count Rotation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a node-graph map with BFS pathfinding and blue-dragon detection so the farming bot routes between platforms declaratively and leaves a platform when it is depleted (`< 2` dragons) instead of on a blind timer.

**Architecture:** Two new pure-ish modules — `navmap.py` (graph + `classify_node` + `plan` + `travel` with dependency injection) and `monsters.py` (template-match dragon detection + counting). Their logic is testable offline. `recovery.py` gains a thin `farming_loop_nav()` that wires the graph's edges to its existing live-verified movement primitives (`drop_to_fallen`, `go_to_bottom`, `recover_to_farming`, `rest_to_bottom`, `farm`, `farm_bottom`) and the detector. The existing `farming_loop_split` is left untouched as a fallback.

**Tech Stack:** Python 3.11, OpenCV (`cv2`), NumPy, pytest 9.1.1, pynput (via `keyboard.py` as `kb`), mss/pyautogui (via `recovery.py`).

## Global Constraints

- **Interpreter:** `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe` (all Run commands use it).
- **No live game input in tests.** Tests must not import `recovery.py`'s live-capture path or send keys. `navmap.py` and `monsters.py` must be importable and unit-testable without a running game. (Matches `HUMANIZE_HANDOFF.md §9`.)
- **Reuse, do not rewrite** `recovery.py`'s tuned primitives. New code calls them; it does not reimplement movement.
- **Single keyboard system:** all key input goes through `keyboard.py` (`import keyboard as kb`) and respects `kb.pause` (F8). Never mix in the notebook cell-1 helpers.
- **Node y-bands (minimap frame, `get_character_color` coords):** `TOP_FARM` y≤95 (x 60–140), `REST` 96–110 (x 60–100), `MID` 111–131 (x 80–140), `BOTTOM_FARM` 132–156 (x 55–100), `LOWER_LEDGE` 157–200. These mirror `recovery.py` constants (`FARMING_Y_MAX=95`, `FALLEN_Y_MIN=96`, `BOTTOM_Y_MIN=132`, `LOWER_LEDGE_Y=147`, `RECOVER_Y_MAX=156`) and are re-confirmed in the live pass.
- **Ropes are edge metadata:** `R_L`(x~90 TOP↔MID), `R_R`(x~125 TOP↔MID), `R_C`(x~91 MID↔BOTTOM). Executable edges initially reuse proven composites; per-rope single-level moves are a live-tuning follow-up (spec §3).
- **Dragon templates:** `C:\jerry\toy_work\maple\MapleStoryAutoLevelUp\monster\blue_wing_dragon\blue_wing_dragon_*.png`, 49 green-screen frames, chroma key **pure green `(0,255,0)` BGR**, sizes 140–302 px wide.
- **Depletion threshold:** `< 2` dragons, judged by the **median** count over a few sampled frames.

---

## File Structure

- **Create `navmap.py`** — map graph, `classify_node`, `plan` (BFS), `next_farm_target`, `travel` (DI). Pure; imports only stdlib. Owns the map model.
- **Create `monsters.py`** — `load_templates`, `build_mask`, `detect_dragons`, `count_from_frames`, `is_depleted`, `count_dragons`, CLI `test`. Imports `cv2`, `numpy`, and `apply_nms` from `detection.py`.
- **Modify `recovery.py`** — add `_nav_locate`, `execute_edge`, `EDGE_ACTIONS`, `farming_loop_nav`, and CLI verbs `runnav` / `nav`. Imports `navmap` and `monsters`.
- **Create `tests/test_navmap.py`** — pure unit tests for the graph.
- **Create `tests/test_monsters.py`** — offline detection tests on synthetic frames.

---

## Task 1: navmap node classification

**Files:**
- Create: `navmap.py`
- Test: `tests/test_navmap.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `NODES = ["TOP_FARM", "REST", "MID", "BOTTOM_FARM", "LOWER_LEDGE"]`
  - `FARM_NODES = ["TOP_FARM", "BOTTOM_FARM"]`
  - `classify_node(x: int, y: int) -> str | None` — node name for a minimap (x,y), or `None` if off-map/unknown (including `x < 0`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_navmap.py
import navmap

def test_classify_each_node_center():
    assert navmap.classify_node(74, 85) == "TOP_FARM"
    assert navmap.classify_node(77, 105) == "REST"
    assert navmap.classify_node(110, 120) == "MID"
    assert navmap.classify_node(63, 145) == "BOTTOM_FARM"
    assert navmap.classify_node(63, 165) == "LOWER_LEDGE"

def test_classify_band_boundaries():
    assert navmap.classify_node(74, 95) == "TOP_FARM"    # y==95 upper band inclusive
    assert navmap.classify_node(77, 96) == "REST"        # y==96 -> REST (no dead zone)
    assert navmap.classify_node(63, 132) == "BOTTOM_FARM"

def test_classify_unknown_returns_none():
    assert navmap.classify_node(-1, -1) is None          # not detected
    assert navmap.classify_node(74, 250) is None         # deep fall, off-map
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_navmap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'navmap'`.

- [ ] **Step 3: Write minimal implementation**

```python
# navmap.py
"""Node-graph map of the blue-dragon-nest map + pathfinding + travel.

Pure module (stdlib only): the graph, node classification, and path planning
are testable with no live game. `travel()` takes injected locate/execute
callables so it is testable too. recovery.py wires the real ones.

Node y-bands are in the minimap frame that recovery.get_character_color()
returns (offset 20,171 -> ROPE_X=91). They mirror recovery.py constants.
"""

NODES = ["TOP_FARM", "REST", "MID", "BOTTOM_FARM", "LOWER_LEDGE"]
FARM_NODES = ["TOP_FARM", "BOTTOM_FARM"]

# (name, y_lo, y_hi, x_lo, x_hi) inclusive bands; ordered so the first match wins.
_BANDS = [
    ("TOP_FARM",     0,  95, 60, 140),
    ("REST",        96, 110, 60, 100),
    ("MID",        111, 131, 80, 140),
    ("BOTTOM_FARM",132, 156, 55, 100),
    ("LOWER_LEDGE",157, 200, 40, 160),
]

def classify_node(x, y):
    if x is None or y is None or x < 0 or y < 0:
        return None
    for name, ylo, yhi, xlo, xhi in _BANDS:
        if ylo <= y <= yhi and xlo <= x <= xhi:
            return name
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_navmap.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add navmap.py tests/test_navmap.py
git commit -m "feat(navmap): node classification from minimap coords"
```

---

## Task 2: navmap graph + BFS pathfinding

**Files:**
- Modify: `navmap.py`
- Test: `tests/test_navmap.py`

**Interfaces:**
- Consumes: `NODES`, `FARM_NODES` from Task 1.
- Produces:
  - `EDGES: list[dict]` — each `{"src": str, "dst": str, "kind": str, "rope": str | None}`, `kind ∈ {"downjump", "rope"}`.
  - `neighbors(node: str) -> list[str]`
  - `plan(src: str, dst: str) -> list[dict] | None` — list of edge dicts from src to dst (BFS, fewest hops); `[]` when `src == dst`; `None` when unreachable or either node invalid.

- [ ] **Step 1: Write the failing test**

```python
def test_plan_same_node_is_empty():
    assert navmap.plan("TOP_FARM", "TOP_FARM") == []

def test_plan_top_to_bottom_exists_and_ends_at_bottom():
    path = navmap.plan("TOP_FARM", "BOTTOM_FARM")
    assert path is not None and len(path) >= 1
    assert path[0]["src"] == "TOP_FARM"
    assert path[-1]["dst"] == "BOTTOM_FARM"

def test_plan_bottom_to_top_uses_rope():
    path = navmap.plan("BOTTOM_FARM", "TOP_FARM")
    assert path is not None
    assert any(e["kind"] == "rope" for e in path)   # climbing up needs a rope

def test_plan_recovery_from_lower_ledge_to_top():
    path = navmap.plan("LOWER_LEDGE", "TOP_FARM")
    assert path is not None and path[-1]["dst"] == "TOP_FARM"

def test_plan_invalid_node_returns_none():
    assert navmap.plan("NOWHERE", "TOP_FARM") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_navmap.py -k plan -v`
Expected: FAIL with `AttributeError: module 'navmap' has no attribute 'plan'`.

- [ ] **Step 3: Write minimal implementation**

```python
# navmap.py  (append)
from collections import deque

# Executable edges. Downjumps go one level DOWN through a gap; ropes climb UP.
# `rope` names the physical rope (metadata; per-rope wiring is a live follow-up).
EDGES = [
    {"src": "TOP_FARM",     "dst": "REST",        "kind": "downjump", "rope": None},
    {"src": "TOP_FARM",     "dst": "BOTTOM_FARM", "kind": "downjump", "rope": None},
    {"src": "REST",         "dst": "BOTTOM_FARM", "kind": "downjump", "rope": None},
    {"src": "REST",         "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_L"},
    {"src": "MID",          "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_L"},
    {"src": "BOTTOM_FARM",  "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_C"},
    {"src": "LOWER_LEDGE",  "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_C"},
]

def neighbors(node):
    return [e for e in EDGES if e["src"] == node]

def plan(src, dst):
    if src not in NODES or dst not in NODES:
        return None
    if src == dst:
        return []
    # BFS over EDGES, fewest hops.
    q = deque([(src, [])])
    seen = {src}
    while q:
        node, path = q.popleft()
        for e in neighbors(node):
            if e["dst"] in seen:
                continue
            new_path = path + [e]
            if e["dst"] == dst:
                return new_path
            seen.add(e["dst"])
            q.append((e["dst"], new_path))
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_navmap.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add navmap.py tests/test_navmap.py
git commit -m "feat(navmap): graph edges + BFS plan()"
```

---

## Task 3: navmap `next_farm_target` + `travel` (with DI)

**Files:**
- Modify: `navmap.py`
- Test: `tests/test_navmap.py`

**Interfaces:**
- Consumes: `plan`, `classify_node`, `FARM_NODES` from Tasks 1–2.
- Produces:
  - `next_farm_target(current: str, dragon_count: int, threshold: int = 2) -> str | None` — the OTHER farm node when depleted (`dragon_count < threshold`), else `None`.
  - `travel(dst, locate_fn, execute_fn, max_rounds: int = 8) -> bool` — `locate_fn() -> str | None` returns the current node; `execute_fn(edge: dict) -> bool` performs one edge and returns success. Re-localizes and re-plans after each edge; returns True on arrival, False if it cannot get there within `max_rounds`.

- [ ] **Step 1: Write the failing test**

```python
def test_next_farm_target_rotates_when_depleted():
    assert navmap.next_farm_target("TOP_FARM", 1) == "BOTTOM_FARM"
    assert navmap.next_farm_target("BOTTOM_FARM", 0) == "TOP_FARM"

def test_next_farm_target_stays_when_populated():
    assert navmap.next_farm_target("TOP_FARM", 2) is None
    assert navmap.next_farm_target("TOP_FARM", 5) is None

def test_travel_already_there():
    calls = []
    ok = navmap.travel("TOP_FARM",
                        locate_fn=lambda: "TOP_FARM",
                        execute_fn=lambda e: calls.append(e) or True)
    assert ok is True and calls == []

def test_travel_executes_planned_edges_until_arrival():
    # locate reports the dst of the last executed edge (as if moves succeed)
    state = {"node": "BOTTOM_FARM"}
    executed = []
    def execute(e):
        executed.append((e["src"], e["dst"]))
        state["node"] = e["dst"]
        return True
    ok = navmap.travel("TOP_FARM",
                       locate_fn=lambda: state["node"],
                       execute_fn=execute)
    assert ok is True and state["node"] == "TOP_FARM"
    assert executed[-1][1] == "TOP_FARM"

def test_travel_gives_up_when_edge_never_advances():
    # execute "succeeds" but locate never moves -> must bail within max_rounds, not loop forever
    ok = navmap.travel("TOP_FARM",
                       locate_fn=lambda: "BOTTOM_FARM",
                       execute_fn=lambda e: True,
                       max_rounds=3)
    assert ok is False

def test_travel_bails_when_lost():
    ok = navmap.travel("TOP_FARM",
                       locate_fn=lambda: None,      # cannot localize
                       execute_fn=lambda e: True,
                       max_rounds=3)
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_navmap.py -k "travel or next_farm" -v`
Expected: FAIL with `AttributeError: module 'navmap' has no attribute 'next_farm_target'`.

- [ ] **Step 3: Write minimal implementation**

```python
# navmap.py  (append)

def next_farm_target(current, dragon_count, threshold=2):
    if dragon_count >= threshold:
        return None
    if current not in FARM_NODES:
        return None
    return FARM_NODES[1] if current == FARM_NODES[0] else FARM_NODES[0]

def travel(dst, locate_fn, execute_fn, max_rounds=8):
    for _ in range(max_rounds):
        cur = locate_fn()
        if cur == dst:
            return True
        if cur is None:
            return False                 # lost -> caller decides (recover/panic)
        path = plan(cur, dst)
        if not path:                     # unreachable or already there handled above
            return cur == dst
        edge = path[0]                   # execute one hop, then re-localize + re-plan
        execute_fn(edge)                 # success is judged by re-localizing, not by return
    return locate_fn() == dst
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_navmap.py -v`
Expected: PASS (all navmap tests).

- [ ] **Step 5: Commit**

```bash
git add navmap.py tests/test_navmap.py
git commit -m "feat(navmap): next_farm_target + travel() with injected locate/execute"
```

---

## Task 4: monsters template loading + green mask

**Files:**
- Create: `monsters.py`
- Test: `tests/test_monsters.py`

**Interfaces:**
- Consumes: nothing (reads template PNGs from disk).
- Produces:
  - `GREEN_BGR = (0, 255, 0)`
  - `DEFAULT_TEMPLATE_DIR` — the reference `blue_wing_dragon` folder path.
  - `build_mask(img_bgr) -> np.ndarray` — uint8 mask, 255 where NOT chroma-green, 0 on green.
  - `load_templates(folder=DEFAULT_TEMPLATE_DIR, max_templates=12, max_dim=120) -> list[tuple[np.ndarray, np.ndarray]]` — `(img_bgr, mask)` pairs; de-duplicates identical frames, keeps up to `max_templates`, and downscales any template whose larger side exceeds `max_dim` (both img and mask) to bound match cost.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_monsters.py
import numpy as np
import monsters

def test_build_mask_excludes_green():
    img = np.zeros((4, 4, 3), np.uint8)
    img[:, :] = (0, 255, 0)          # all chroma green
    img[1, 1] = (200, 50, 50)        # one dragon-blue pixel
    mask = monsters.build_mask(img)
    assert mask[1, 1] == 255
    assert mask[0, 0] == 0
    assert mask.dtype == np.uint8

def test_load_templates_returns_pairs_and_caps_count():
    tpls = monsters.load_templates(max_templates=8)
    assert 1 <= len(tpls) <= 8
    img, mask = tpls[0]
    assert img.shape[:2] == mask.shape[:2]
    assert max(img.shape[:2]) <= 120     # downscaled

def test_load_templates_missing_folder_returns_empty():
    assert monsters.load_templates(folder="does/not/exist") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_monsters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'monsters'`.

- [ ] **Step 3: Write minimal implementation**

```python
# monsters.py
"""Blue-wing-dragon detection & counting (offline-testable).

Ported from MapleStoryAutoLevelUp get_monsters_in_range: match green-screen
dragon sprites (chroma masked) against a screen-frame ROI, NMS, count.
"""
import os, glob
import cv2
import numpy as np
from detection import apply_nms

GREEN_BGR = (0, 255, 0)
DEFAULT_TEMPLATE_DIR = r"C:\jerry\toy_work\maple\MapleStoryAutoLevelUp\monster\blue_wing_dragon"

def build_mask(img_bgr):
    green = cv2.inRange(img_bgr, np.array(GREEN_BGR), np.array(GREEN_BGR))
    return cv2.bitwise_not(green)

def _downscale(img, mask, max_dim):
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= max_dim:
        return img, mask
    s = max_dim / float(m)
    size = (max(1, int(w * s)), max(1, int(h * s)))
    img2 = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    mask2 = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    return img2, mask2

def load_templates(folder=DEFAULT_TEMPLATE_DIR, max_templates=12, max_dim=120):
    paths = sorted(glob.glob(os.path.join(folder, "blue_wing_dragon*.png")))
    out, seen = [], set()
    for p in paths:
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            continue
        key = (img.shape[1], img.shape[0], int(img.sum()))   # cheap dedup
        if key in seen:
            continue
        seen.add(key)
        mask = build_mask(img)
        out.append(_downscale(img, mask, max_dim))
        if len(out) >= max_templates:
            break
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_monsters.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add monsters.py tests/test_monsters.py
git commit -m "feat(monsters): template loading + green-chroma mask"
```

---

## Task 5: monsters `detect_dragons` (offline, synthetic frame)

**Files:**
- Modify: `monsters.py`
- Test: `tests/test_monsters.py`

**Interfaces:**
- Consumes: `load_templates`, `build_mask` from Task 4; `apply_nms` from `detection.py`.
- Produces:
  - `detect_dragons(frame_bgr, roi, templates, diff_thres=0.35) -> list[tuple[int,int,int,int]]` — boxes `(x1,y1,x2,y2)` in FULL-frame coords. `roi=(x0,y0,x1,y1)`; matching runs inside the ROI, boxes are offset back to full frame, then NMS-merged.

- [ ] **Step 1: Write the failing test**

```python
def _paste(canvas, tile_bgr, x, y):
    mask = monsters.build_mask(tile_bgr)
    h, w = tile_bgr.shape[:2]
    roi = canvas[y:y+h, x:x+w]
    roi[mask > 0] = tile_bgr[mask > 0]     # paste only non-green pixels

def test_detect_counts_two_pasted_dragons():
    tpls = monsters.load_templates(max_templates=1)
    assert tpls, "need at least one template"
    tile = tpls[0][0]                       # the (downscaled) template image, green bg
    h, w = tile.shape[:2]
    canvas = np.zeros((h + 40, w * 3 + 60, 3), np.uint8)   # black playfield
    _paste(canvas, tile, 10, 20)
    _paste(canvas, tile, w + 40, 20)
    roi = (0, 0, canvas.shape[1], canvas.shape[0])
    boxes = monsters.detect_dragons(canvas, roi, tpls, diff_thres=0.30)
    assert len(boxes) == 2

def test_detect_empty_frame_is_zero():
    tpls = monsters.load_templates(max_templates=1)
    canvas = np.zeros((200, 300, 3), np.uint8)
    boxes = monsters.detect_dragons(canvas, (0, 0, 300, 200), tpls, diff_thres=0.30)
    assert boxes == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_monsters.py -k detect -v`
Expected: FAIL with `AttributeError: module 'monsters' has no attribute 'detect_dragons'`.

- [ ] **Step 3: Write minimal implementation**

```python
# monsters.py  (append)

def detect_dragons(frame_bgr, roi, templates, diff_thres=0.35):
    x0, y0, x1, y1 = roi
    sub = frame_bgr[y0:y1, x0:x1]
    if sub.size == 0:
        return []
    boxes = []
    for img, mask in templates:
        th, tw = img.shape[:2]
        if th > sub.shape[0] or tw > sub.shape[1]:
            continue
        res = cv2.matchTemplate(sub, img, cv2.TM_SQDIFF_NORMED, mask=mask)
        res = np.nan_to_num(res, nan=1.0, posinf=1.0, neginf=1.0)  # masked SQDIFF can NaN
        ys, xs = np.where(res <= diff_thres)
        for px, py in zip(xs, ys):
            boxes.append([x0 + int(px), y0 + int(py),
                          x0 + int(px) + tw, y0 + int(py) + th])
    return [tuple(b) for b in apply_nms(boxes, overlapThresh=0.3)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_monsters.py -v`
Expected: PASS (all monsters tests so far).

- [ ] **Step 5: Commit**

```bash
git add monsters.py tests/test_monsters.py
git commit -m "feat(monsters): detect_dragons via masked template match + NMS"
```

---

## Task 6: monsters counting + depletion decision

**Files:**
- Modify: `monsters.py`
- Test: `tests/test_monsters.py`

**Interfaces:**
- Consumes: `detect_dragons` from Task 5.
- Produces:
  - `count_from_frames(frames, roi, templates, diff_thres=0.35) -> int` — median of per-frame `len(detect_dragons(...))`.
  - `is_depleted(count, threshold=2) -> bool` — `count < threshold`.

- [ ] **Step 1: Write the failing test**

```python
def test_is_depleted_threshold():
    assert monsters.is_depleted(1) is True
    assert monsters.is_depleted(0) is True
    assert monsters.is_depleted(2) is False
    assert monsters.is_depleted(3) is False

def test_count_from_frames_is_median(monkeypatch):
    # stub detect_dragons to return controlled per-frame counts 3,3,0 -> median 3
    seq = iter([[( 0,0,1,1)]*3, [(0,0,1,1)]*3, []])
    monkeypatch.setattr(monsters, "detect_dragons", lambda *a, **k: next(seq))
    frames = [object(), object(), object()]
    assert monsters.count_from_frames(frames, (0,0,10,10), templates=[]) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_monsters.py -k "depleted or median" -v`
Expected: FAIL with `AttributeError: module 'monsters' has no attribute 'is_depleted'`.

- [ ] **Step 3: Write minimal implementation**

```python
# monsters.py  (append)

def count_from_frames(frames, roi, templates, diff_thres=0.35):
    counts = [len(detect_dragons(f, roi, templates, diff_thres)) for f in frames]
    if not counts:
        return 0
    counts.sort()
    return counts[len(counts) // 2]

def is_depleted(count, threshold=2):
    return count < threshold
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_monsters.py -v`
Expected: PASS (all monsters tests).

- [ ] **Step 5: Commit**

```bash
git add monsters.py tests/test_monsters.py
git commit -m "feat(monsters): count_from_frames (median) + is_depleted"
```

---

## Task 7: monsters live wrappers + calibration CLI

**Files:**
- Modify: `monsters.py`
- Test: `tests/test_monsters.py`

**Interfaces:**
- Consumes: `count_from_frames`, `load_templates` from Tasks 4–6.
- Produces:
  - `DEFAULT_ROI = (700, 250, 1900, 950)` — playfield ROI in the physical grab (excludes the top-left minimap and the bottom EXP/UI band); **live-tuned**.
  - `count_dragons(capture_fn, templates, roi=DEFAULT_ROI, samples=3, interval=0.3, diff_thres=0.35) -> int` — grabs `samples` frames via `capture_fn()` (spacing `interval` s) and returns `count_from_frames`.
  - CLI: `python monsters.py test` — screenshots the live game once, runs `detect_dragons`, prints the count, and writes an annotated image to `debug_output/dragons_detected.png`.

- [ ] **Step 1: Write the failing test**

```python
def test_count_dragons_uses_capture_fn(monkeypatch):
    grabbed = {"n": 0}
    def fake_capture():
        grabbed["n"] += 1
        return np.zeros((10, 10, 3), np.uint8)
    monkeypatch.setattr(monsters, "count_from_frames", lambda frames, *a, **k: len(frames))
    n = monsters.count_dragons(fake_capture, templates=[], samples=3, interval=0)
    assert n == 3 and grabbed["n"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_monsters.py -k count_dragons -v`
Expected: FAIL with `AttributeError: module 'monsters' has no attribute 'count_dragons'`.

- [ ] **Step 3: Write minimal implementation**

```python
# monsters.py  (append)
import time

DEFAULT_ROI = (700, 250, 1900, 950)   # LIVE-TUNE: excludes minimap + bottom UI

def count_dragons(capture_fn, templates, roi=DEFAULT_ROI, samples=3, interval=0.3, diff_thres=0.35):
    frames = []
    for i in range(samples):
        f = capture_fn()
        if f is not None:
            frames.append(f)
        if i < samples - 1 and interval:
            time.sleep(interval)
    return count_from_frames(frames, roi, templates, diff_thres)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        import recovery                          # live capture lives here
        tpls = load_templates()
        frame = recovery.capture()
        boxes = detect_dragons(frame, DEFAULT_ROI, tpls)
        print(f"templates={len(tpls)}  dragons detected={len(boxes)}  ROI={DEFAULT_ROI}")
        os.makedirs("debug_output", exist_ok=True)
        vis = frame.copy()
        cv2.rectangle(vis, DEFAULT_ROI[:2], DEFAULT_ROI[2:], (0, 255, 255), 2)
        for (x1, y1, x2, y2) in boxes:
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.imwrite("debug_output/dragons_detected.png", vis)
        print("wrote debug_output/dragons_detected.png")
    else:
        print("usage: python monsters.py test")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_monsters.py -v`
Expected: PASS (all monsters tests).

- [ ] **Step 5: Commit**

```bash
git add monsters.py tests/test_monsters.py
git commit -m "feat(monsters): count_dragons live wrapper + `monsters.py test` calibration CLI"
```

---

## Task 8: recovery — wire graph edges to movement primitives

**Files:**
- Modify: `recovery.py` (add near the other loop helpers, before `farming_loop`)
- Test: `tests/test_navmap.py` (a wiring-completeness test that imports `navmap` only)

**Interfaces:**
- Consumes: `navmap.EDGES`, `navmap.classify_node`; `recovery` primitives `stable_char`, `drop_to_fallen`, `rest_to_bottom`, `go_to_bottom`, `recover_to_farming`.
- Produces (in `recovery.py`):
  - `_nav_locate() -> str | None` — `stable_char(3)` → `navmap.classify_node`.
  - `EDGE_ACTIONS: dict[tuple[str,str], callable]` — `(src,dst) -> callable() -> bool`.
  - `execute_edge(edge: dict) -> bool` — looks up `(edge["src"], edge["dst"])` and calls it; unknown pair → `False`.

- [ ] **Step 1: Write the failing test** (completeness: every graph edge has an executor)

```python
# tests/test_navmap.py  (append) -- import recovery lazily so pure navmap tests never need it
def test_every_edge_has_an_executor():
    import importlib.util, os
    # only run if recovery.py's heavy deps are importable in this env
    try:
        import recovery
    except Exception as e:
        import pytest
        pytest.skip(f"recovery not importable here: {e}")
    import navmap
    for e in navmap.EDGES:
        assert (e["src"], e["dst"]) in recovery.EDGE_ACTIONS, (e["src"], e["dst"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_navmap.py -k executor -v`
Expected: FAIL with `AttributeError: module 'recovery' has no attribute 'EDGE_ACTIONS'` (not skipped — `recovery` imports fine in this env).

- [ ] **Step 3: Write minimal implementation**

```python
# recovery.py  (add above farming_loop)
import navmap

def _nav_locate():
    x, y = stable_char(3)
    return navmap.classify_node(x, y)

# (src,dst) -> a callable that performs the move using the tuned primitives.
# Rope/up moves reuse recover_to_farming (it climbs any-below -> top). Downjumps
# reuse the proven composites. Per-rope single-level moves are a live follow-up.
EDGE_ACTIONS = {
    ("TOP_FARM", "REST"):        lambda: drop_to_fallen(),
    ("TOP_FARM", "BOTTOM_FARM"): lambda: go_to_bottom(),
    ("REST", "BOTTOM_FARM"):     lambda: rest_to_bottom(),
    ("REST", "TOP_FARM"):        lambda: recover_to_farming(),
    ("MID", "TOP_FARM"):         lambda: recover_to_farming(),
    ("BOTTOM_FARM", "TOP_FARM"): lambda: recover_to_farming(),
    ("LOWER_LEDGE", "TOP_FARM"): lambda: recover_to_farming(),
}

def execute_edge(edge):
    fn = EDGE_ACTIONS.get((edge["src"], edge["dst"]))
    if fn is None:
        print(f"[nav] no executor for {edge['src']}->{edge['dst']}")
        return False
    return bool(fn())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/ -v`
Expected: PASS (all navmap + monsters tests).

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_navmap.py
git commit -m "feat(recovery): wire navmap edges to tuned movement primitives"
```

---

## Task 9: recovery — `farming_loop_nav` + CLI

**Files:**
- Modify: `recovery.py` (add after `farming_loop_split`; extend the `__main__` CLI dispatch)
- Test: `tests/test_navmap.py` (pure decision test — no live loop)

**Interfaces:**
- Consumes: `navmap.travel`, `navmap.next_farm_target`, `navmap.FARM_NODES`; `_nav_locate`, `execute_edge`; `monsters.load_templates`, `monsters.count_dragons`; primitives `farm`, `farm_bottom`, `drop_to_fallen`, `_idle_on_fallen`, `recover_to_farming`, `focus`; `kb`.
- Produces (in `recovery.py`):
  - `farming_loop_nav(exp_check=None, enemy_check=None, panic=None, farm_secs=(20,40), break_every=(8*60,15*60), rest_range=(30,120), skill_interval=(260,340), deplete_threshold=2, count_samples=3, max_seconds=None) -> None`
  - CLI verbs: `python recovery.py runnav` (full, starts paused, F8 to begin) and `python recovery.py nav <secs>` (bounded test).

- [ ] **Step 1: Write the failing test** (the rotation decision the loop relies on; pure)

```python
# tests/test_navmap.py  (append)
def test_rotation_decision_end_to_end():
    import navmap
    # depleted top -> go bottom; then depleted bottom -> go top; populated -> stay
    assert navmap.next_farm_target("TOP_FARM", 1, threshold=2) == "BOTTOM_FARM"
    assert navmap.next_farm_target("BOTTOM_FARM", 1, threshold=2) == "TOP_FARM"
    assert navmap.next_farm_target("TOP_FARM", 4, threshold=2) is None
    # and travel between the two farm nodes is always planttable
    assert navmap.plan("TOP_FARM", "BOTTOM_FARM") is not None
    assert navmap.plan("BOTTOM_FARM", "TOP_FARM") is not None
```

- [ ] **Step 2: Run test to verify it fails/passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_navmap.py -k rotation -v`
Expected: PASS already (exercises Task 2–3 APIs) — this test guards the loop's contract. If it FAILS, fix navmap before writing the loop.

- [ ] **Step 3: Write the implementation**

```python
# recovery.py  (add after farming_loop_split)
def farming_loop_nav(exp_check=None, enemy_check=None, panic=None,
                     farm_secs=(20, 40), break_every=(8 * 60, 15 * 60),
                     rest_range=(30, 120), skill_interval=(260, 340),
                     deplete_threshold=2, count_samples=3, max_seconds=None):
    """Node-graph farming loop: farm the current platform, count dragons, and when
    depleted (< deplete_threshold) travel() to the other farming platform. Recovery
    and rotation both go through navmap.travel(). Preserves EXP/red-dot safety,
    F8 pause, jittered breaks, and jittered skill/heal cadence. max_seconds bounds it."""
    import random as _r
    import monsters, navmap
    if not focus():
        print("[nav] could not focus"); return
    tpls = monsters.load_templates()
    print(f"[nav] loaded {len(tpls)} dragon templates")
    t_start = time.time()
    next_break = time.time() + _r.uniform(*break_every)
    next_skill = [time.time() + _r.uniform(*skill_interval)]
    current = "TOP_FARM"

    def heal_skill():
        kb.safe_press('h'); time.sleep(0.08); kb.safe_release('h')
        if time.time() >= next_skill[0]:
            kb.safe_press('a'); time.sleep(0.4); kb.safe_release('a')
            next_skill[0] = time.time() + _r.uniform(*skill_interval)

    def go(dst):
        return navmap.travel(dst, locate_fn=_nav_locate, execute_fn=execute_edge)

    while True:
        if max_seconds is not None and time.time() - t_start > max_seconds:
            kb.safe_release_all(); print("[nav] max_seconds -> stop"); return
        if kb.pause:
            kb.safe_release_all(); time.sleep(0.1); continue

        # locate; recover onto a farm node if off-map
        node = _nav_locate()
        if node is None:
            time.sleep(0.2); continue
        if node not in navmap.FARM_NODES:
            print(f"[nav] on {node} -> travel to {current}")
            if not go(current) and panic:
                panic(); time.sleep(1)
            continue

        # safety
        if enemy_check and enemy_check():
            print("[nav] another player -> panic")
            if panic: panic()
            time.sleep(1); continue
        if exp_check and exp_check():
            print("[nav] EXP too low -> panic")
            if panic: panic()
            time.sleep(1); continue

        # scheduled break: drop to REST, rest, climb back
        if time.time() >= next_break:
            print("[nav] break -> rest on fallen platform")
            if drop_to_fallen():
                _idle_on_fallen(_r.uniform(*rest_range))
            recover_to_farming()
            next_break = time.time() + _r.uniform(*break_every)
            current = "TOP_FARM"
            continue

        # farm a stint on the current node
        current = node
        ok = farm(_r.uniform(*farm_secs)) if node == "TOP_FARM" else farm_bottom(_r.uniform(*farm_secs))
        if not ok:
            continue                                 # fell mid-stint -> re-locate/recover next round
        heal_skill()

        # count dragons; rotate if depleted
        n = monsters.count_dragons(capture, tpls, samples=count_samples)
        print(f"[nav] {node} dragons~{n}")
        target = navmap.next_farm_target(node, n, threshold=deplete_threshold)
        if target:
            print(f"[nav] {node} depleted ({n} < {deplete_threshold}) -> travel to {target}")
            if go(target):
                current = target
```

Then extend the CLI dispatch (in the `__main__` `elif` chain, alongside `run`/`runsplit`):

```python
    elif cmd == "runnav":
        _exp_proc = ExpProcessor(); _started = [None]
        def _exp_check():
            if _started[0] is None: _started[0] = time.time()
            eg = get_exp(_exp_proc)
            if eg is not None and eg[0] is not None:
                return eg[0] < 4000 and (time.time() - _started[0]) > 180
            return False
        def _enemy_check(): return len(get_enemy()) > 0
        def _panic():
            kb.safe_release_all()
            print("[safety] trigger -> releasing keys and PAUSING (no Free Market). F8 to resume.")
            kb.pause = True
        lis = Listener(on_press=kb.on_press); lis.start(); kb.pause = True
        print("FULL RUN (runnav) ready. Switch to the game and press F8 to start / pause.")
        try:
            farming_loop_nav(exp_check=_exp_check, enemy_check=_enemy_check, panic=_panic)
        finally:
            kb.safe_release_all(); lis.stop()
    elif cmd == "nav":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        lis = Listener(on_press=kb.on_press); lis.start()
        try:
            farming_loop_nav(break_every=(9999, 9999), farm_secs=(12, 16), max_seconds=secs)
        finally:
            kb.safe_release_all(); lis.stop()
```

- [ ] **Step 4: Run tests + import check**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/ -v`
Expected: PASS (all tests).
Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -c "import recovery; print(hasattr(recovery,'farming_loop_nav'))"`
Expected: prints `True` (module imports cleanly, no syntax error).

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_navmap.py
git commit -m "feat(recovery): farming_loop_nav + runnav/nav CLI (dragon-count rotation)"
```

---

## Task 10: docs — update handoff pointer + node-map doc

**Files:**
- Modify: `HUMANIZE_HANDOFF.md` (append a short addendum pointing at the new modules)
- Create: `docs/superpowers/plans/2026-08-10-node-map-monster-detection.md` (this file, already saved)

**Interfaces:**
- Consumes: nothing.
- Produces: an addendum so the next session finds the new capability.

- [ ] **Step 1: Append an addendum to `HUMANIZE_HANDOFF.md`**

Add under a new `### Live session addendum (2026-08-10): node-map navigation + dragon detection` heading:

```markdown
### Node-map navigation + dragon-count rotation (2026-08-10)
- `navmap.py` — node graph (TOP_FARM/REST/MID/BOTTOM_FARM/LOWER_LEDGE + 3 ropes
  R_L/R_R/R_C), `classify_node`, `plan` (BFS), `travel` (DI), `next_farm_target`.
- `monsters.py` — blue-wing-dragon detection (masked template match on the 49
  green-screen sprites), `count_dragons`, `is_depleted`. Calibrate scale/threshold
  with `python monsters.py test` (writes debug_output/dragons_detected.png).
- `recovery.py: farming_loop_nav()` — farm a platform, count dragons, rotate
  top<->bottom when < 2. CLI `python recovery.py runnav` (full) / `nav <secs>` (test).
  `farming_loop_split` remains the timer-based fallback.
- STILL LIVE-TUNE: node y-bands, the three rope x-columns, monster `diff_thres` /
  template scale (reference sprites vs Artale 150%-DPI grab), and `monsters.DEFAULT_ROI`.
- Design + plan: docs/superpowers/specs/ and docs/superpowers/plans/ (2026-08-10).
```

- [ ] **Step 2: Verify the file reads correctly**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -c "print('addendum present:', 'farming_loop_nav' in open('HUMANIZE_HANDOFF.md',encoding='utf-8').read())"`
Expected: prints `addendum present: True`.

- [ ] **Step 3: Commit**

```bash
git add HUMANIZE_HANDOFF.md
git commit -m "docs: handoff addendum for node-map navigation + dragon detection"
```

---

## Live tuning pass (after the plan — with the user, per-action OK for key input)

Not a coding task; the design's known-risk closeout (spec §7–§8). With the game running:
1. `python recovery.py sense <label>` at each platform → confirm/nudge the node y-bands in `navmap._BANDS` and `EDGE_ACTIONS`.
2. `python monsters.py test` → tune `diff_thres`, `max_dim`/scale, and `DEFAULT_ROI` until the dragon count is stable (annotated debug image).
3. `python recovery.py nav 90` (bounded) → watch one rotation, then `runnav` for the full loop.
4. Optionally wire the three ropes as individual single-level edges once their x-columns are measured (data-only change to `EDGE_ACTIONS`).

---

## Self-Review Notes

- **Spec coverage:** §3 map model → Tasks 1–2, 8 (+live). §4 navmap → Tasks 1–3. §5 monsters → Tasks 4–7. §6 loop → Task 9. §7 testing → per-task unit tests + Task 7/9 live CLIs + Live tuning pass. §8 risks → Task 4 (dedup/downscale), Task 5 (NaN guard, ROI), Task 7 (`test` CLI, DEFAULT_ROI), Task 9 (reuses hardened primitives).
- **Fallback preserved:** `farming_loop_split` and its CLI verbs are never modified (Tasks 8–9 only add).
- **Type consistency:** `classify_node -> str|None`, `plan -> list[dict]|None`, edge dict keys `src/dst/kind/rope`, `travel(dst, locate_fn, execute_fn, max_rounds)`, `detect_dragons -> list[tuple]`, `count_dragons(capture_fn, templates, roi, samples, interval, diff_thres)` — used identically across Tasks 8–9.
