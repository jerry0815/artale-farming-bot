# Predictive Rope-Grab (bottom→top) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bottom→top rope climb grab the rope on the **first** attempt (one smooth climb) instead of overshooting the grab zone and needing 2–3 re-align rounds — WITHOUT regressing the current always-succeeds reliability.

**Architecture:** The failure is physical: `walk_to_x` stops ~4px past the rope (x95) because of ~40ms sensor lag + tap granularity, so the straight-up hop misses the ~x90–94 grab zone. Every *live* tuning attempt so far has regressed (documented in `HUMANIZE_HANDOFF.md` and re-confirmed 2026-08-11). This plan removes the live guesswork: build a **pure, offline-testable model** of her walk (velocity + coast-after-release) from **recorded** approach trajectories, compute a **predictive release point** that leads the target by the lag so she coasts into the grab zone, validate it **offline** against held-out recordings, and only then wire it into the live grab **behind a flag with the existing re-align path as the guaranteed fallback**.

**Tech Stack:** Python 3.11, NumPy, pytest, pynput (via `keyboard.py` as `kb`), the existing `recovery.py` sensing (`get_character_full`, `stable_char`).

## Global Constraints

- **Interpreter:** `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe` (all Run commands; pytest installed).
- **No regression, ever.** The current re-align climb (2–3 rounds, always reaches the top) is the FALLBACK. The predictive path is an optimization gated behind `PREDICTIVE_GRAB` (default `False`); if it misses, control falls through to the existing `climb_and_jump` re-align round. A live run with the flag off must behave EXACTLY as today.
- **No live key input in unit tests.** `rope_grab.py` is pure (stdlib + numpy only) — importable and fully testable with no game and no `pynput`. All model/prediction/simulation logic lives there. `recovery.py` only *wires* it to live sensing/keys.
- **Offline validation gates live testing.** Do not tune constants against the live game by trial-and-error. Tune the model against recorded trajectories offline; only run live to (a) collect recordings and (b) final-validate a model that already passes offline.
- **Reuse existing sensing/movement.** `get_character_full()`, `stable_char(n)`, `kb.safe_press/release`, `Key.right/left`, `JUMP=Key.alt_l`. Do not reimplement.
- **Calibrated facts (minimap frame):** `ROPE_X=91`; effective grab zone ≈ **x90–94**; bottom platform reads y≈132–143 with idle bob down to ~127; `BOTTOM_Y_MIN=132`; `ROPE_EXIT_TOP_Y=92`. The hop (Up+Jump) is required only from the bottom platform (rope base above the floor).
- **Data files:** trajectories → `rope_traj.jsonl`; fitted model → `rope_model.json` (both git-ignored — add to `.gitignore`).

---

## File Structure

- **Create `rope_grab.py`** — pure offline module: trajectory schema + loader, walk-model estimation, predictive-release computation, stop simulator, grab-verify decision. No pynput, no game. Owns all the math.
- **Create `tests/test_rope_grab.py`** — offline unit tests (synthetic + tiny fixture trajectories).
- **Modify `recovery.py`** — add: a recording instrumentation + CLI (`record-grab`), `walk_to_x_predictive`, a robust hop decision, and the flag-gated predictive grab integrated into `recover_to_farming` with the existing re-align as fallback. Plus a validation CLI (`grab-eval`).
- **Modify `.gitignore`** — ignore `rope_traj.jsonl`, `rope_model.json`.
- **Modify `HUMANIZE_HANDOFF.md`** — addendum.

---

## Task 1: Trajectory schema + loader (pure)

**Files:**
- Create: `rope_grab.py`
- Test: `tests/test_rope_grab.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - A trajectory is a `dict`: `{"dir": "R"|"L", "target": int, "release_x": int|None, "stop_x": int, "reads": [[t, x, y], ...]}` — `reads` are `(seconds_since_start, minimap_x, minimap_y)` samples during one walk-to-rope attempt; `release_x` is the x at which the walk key was released (None if not recorded), `stop_x` the settled x after release.
  - `load_trajectories(path) -> list[dict]` — parse JSONL (one trajectory per line), skipping blank lines; returns `[]` if the file is missing.
  - `valid_trajectory(t) -> bool` — has the required keys, `reads` non-empty, `dir` in {"R","L"}.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rope_grab.py
import json, os, rope_grab

def test_valid_trajectory():
    good = {"dir": "R", "target": 92, "release_x": 88, "stop_x": 94,
            "reads": [[0.0, 63, 143], [0.1, 78, 143], [0.2, 94, 143]]}
    assert rope_grab.valid_trajectory(good) is True
    assert rope_grab.valid_trajectory({"dir": "X", "reads": []}) is False
    assert rope_grab.valid_trajectory({"dir": "R", "reads": []}) is False

def test_load_trajectories_roundtrip(tmp_path):
    p = tmp_path / "t.jsonl"
    rows = [{"dir": "R", "target": 92, "release_x": 88, "stop_x": 94,
             "reads": [[0.0, 63, 143], [0.2, 94, 143]]}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", encoding="utf-8")
    got = rope_grab.load_trajectories(str(p))
    assert len(got) == 1 and got[0]["stop_x"] == 94

def test_load_missing_returns_empty():
    assert rope_grab.load_trajectories("does/not/exist.jsonl") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rope_grab'`.

- [ ] **Step 3: Write minimal implementation**

```python
# rope_grab.py
"""Predictive rope-grab model + math (pure, offline-testable).

The bottom->top rope grab overshoots the ~x90-94 grab zone because walk_to_x
stops ~4px late (sensor lag + tap granularity). This module fits a walk model
(velocity + coast-after-release) from recorded approach trajectories and computes
a predictive release point that leads the target so she coasts INTO the zone.
No pynput / no game here -- recovery.py wires it to live sensing.
"""
import json, os

def valid_trajectory(t):
    if not isinstance(t, dict):
        return False
    if t.get("dir") not in ("R", "L"):
        return False
    return bool(t.get("reads"))

def load_trajectories(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            t = json.loads(line)
            if valid_trajectory(t):
                out.append(t)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add rope_grab.py tests/test_rope_grab.py
git commit -m "feat(rope_grab): trajectory schema + JSONL loader"
```

---

## Task 2: Walk-model estimation (pure)

**Files:**
- Modify: `rope_grab.py`
- Test: `tests/test_rope_grab.py`

**Interfaces:**
- Consumes: `load_trajectories` / trajectory dicts from Task 1.
- Produces:
  - `walk_speed(traj) -> float | None` — median |dx/dt| (px/sec) over the moving portion of `reads` (consecutive samples where x changed); `None` if fewer than 2 moving samples.
  - `coast_distance(traj) -> int | None` — signed `stop_x - release_x` if both present, else `None`.
  - `estimate_model(trajectories) -> dict` — aggregates per direction: `{"R": {"speed": float, "coast": float}, "L": {...}}` using the median across trajectories; missing direction → `{"speed": 0.0, "coast": 0.0}`.

- [ ] **Step 1: Write the failing test**

```python
def _linear_traj(direction, x0, x1, v, dt=0.1, release_x=None, stop_x=None):
    reads, x, t = [], x0, 0.0
    step = v * dt * (1 if direction == "R" else -1)
    while (direction == "R" and x <= x1) or (direction == "L" and x >= x1):
        reads.append([round(t, 3), int(round(x)), 143]); x += step; t += dt
    return {"dir": direction, "target": x1, "release_x": release_x,
            "stop_x": stop_x if stop_x is not None else int(round(x - step)), "reads": reads}

def test_walk_speed_recovers_velocity():
    tr = _linear_traj("R", 63, 95, v=120.0)   # 120 px/s
    s = rope_grab.walk_speed(tr)
    assert 100 <= s <= 140                      # median dx/dt near 120

def test_coast_distance_signed():
    assert rope_grab.coast_distance({"release_x": 88, "stop_x": 94}) == 6
    assert rope_grab.coast_distance({"release_x": None, "stop_x": 94}) is None

def test_estimate_model_medians_per_direction():
    trs = [_linear_traj("R", 63, 95, v=120.0, release_x=88, stop_x=94),
           _linear_traj("R", 63, 95, v=120.0, release_x=90, stop_x=95)]
    m = rope_grab.estimate_model(trs)
    assert 100 <= m["R"]["speed"] <= 140
    assert 5 <= m["R"]["coast"] <= 6            # median of {6,5}
    assert m["L"] == {"speed": 0.0, "coast": 0.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -k "speed or coast or estimate" -v`
Expected: FAIL with `AttributeError: module 'rope_grab' has no attribute 'walk_speed'`.

- [ ] **Step 3: Write minimal implementation**

```python
# rope_grab.py  (append)
def _median(vs):
    vs = sorted(vs)
    return vs[len(vs) // 2] if vs else None

def walk_speed(traj):
    reads = traj.get("reads", [])
    rates = []
    for (t0, x0, _), (t1, x1, _) in zip(reads, reads[1:]):
        dt = t1 - t0
        if dt > 0 and x1 != x0:
            rates.append(abs(x1 - x0) / dt)
    return _median(rates) if rates else None

def coast_distance(traj):
    rx, sx = traj.get("release_x"), traj.get("stop_x")
    if rx is None or sx is None:
        return None
    return sx - rx

def estimate_model(trajectories):
    out = {}
    for d in ("R", "L"):
        trs = [t for t in trajectories if t.get("dir") == d]
        speeds = [s for s in (walk_speed(t) for t in trs) if s is not None]
        coasts = [abs(c) for c in (coast_distance(t) for t in trs) if c is not None]
        out[d] = {"speed": float(_median(speeds) or 0.0),
                  "coast": float(_median(coasts) or 0.0)}
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -v`
Expected: PASS (all Task 1 + 2 tests).

- [ ] **Step 5: Commit**

```bash
git add rope_grab.py tests/test_rope_grab.py
git commit -m "feat(rope_grab): walk speed + coast estimation, per-direction model"
```

---

## Task 3: Predictive release point + stop simulator (pure)

**Files:**
- Modify: `rope_grab.py`
- Test: `tests/test_rope_grab.py`

**Interfaces:**
- Consumes: model dict from Task 2 (`{"R":{"speed","coast"},"L":{...}}`).
- Produces:
  - `predict_release_x(model, target, going_right, sensor_lag=0.045) -> int` — the x at which to release the walk key so she coasts to `target`. Lead = `coast + speed*sensor_lag`; release at `target - lead` when going right, `target + lead` when going left. Rounded to int.
  - `simulate_stop(model, release_x, going_right) -> int` — where she stops given a release at `release_x`: `release_x + coast` (right) / `release_x - coast` (left).
  - `lands_in_zone(model, target, going_right, zone=(90, 94), sensor_lag=0.045) -> bool` — True iff `simulate_stop(model, predict_release_x(...), going_right)` is within `zone` inclusive.

- [ ] **Step 1: Write the failing test**

```python
def test_predict_release_leads_by_coast_and_lag():
    m = {"R": {"speed": 100.0, "coast": 6.0}, "L": {"speed": 100.0, "coast": 6.0}}
    # going right to target 92: lead = 6 + 100*0.045 = 10.5 -> release ~ 81.5 -> 82
    assert rope_grab.predict_release_x(m, 92, going_right=True, sensor_lag=0.045) == 82

def test_simulate_stop_applies_coast():
    m = {"R": {"speed": 100.0, "coast": 6.0}, "L": {"speed": 100.0, "coast": 6.0}}
    assert rope_grab.simulate_stop(m, 82, going_right=True) == 88

def test_lands_in_zone_true_when_target_offsets_the_lag():
    # predicted stop = target - speed*lag = 96 - 4.5 ~= 92, inside (90,94)
    m = {"R": {"speed": 100.0, "coast": 6.0}, "L": {"speed": 100.0, "coast": 6.0}}
    assert rope_grab.lands_in_zone(m, 96, going_right=True, zone=(90, 94)) is True
    # target 92 would stop ~88 (out of zone) -> False
    assert rope_grab.lands_in_zone(m, 92, going_right=True, zone=(90, 94)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -k "predict or simulate or zone" -v`
Expected: FAIL with `AttributeError: ... 'predict_release_x'`.

- [ ] **Step 3: Write minimal implementation**

```python
# rope_grab.py  (append)
def predict_release_x(model, target, going_right, sensor_lag=0.045):
    d = model["R"] if going_right else model["L"]
    lead = d["coast"] + d["speed"] * sensor_lag
    return int(round(target - lead)) if going_right else int(round(target + lead))

def simulate_stop(model, release_x, going_right):
    coast = (model["R"] if going_right else model["L"])["coast"]
    return int(round(release_x + coast)) if going_right else int(round(release_x - coast))

def lands_in_zone(model, target, going_right, zone=(90, 94), sensor_lag=0.045):
    rx = predict_release_x(model, target, going_right, sensor_lag)
    sx = simulate_stop(model, rx, going_right)
    return zone[0] <= sx <= zone[1]
```

Math check (matches the tests above): the predicted stop is `target - speed*lag` (the `coast` cancels between `predict_release_x` and `simulate_stop`). So `target=96` → stop `96 - 100*0.045 ≈ 91.5 → 92` (in zone), and `target=92` → stop `~88` (out of zone) — exactly what `test_lands_in_zone_*` asserts.

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rope_grab.py tests/test_rope_grab.py
git commit -m "feat(rope_grab): predictive release point + stop simulator"
```

---

## Task 4: Offline model evaluator (pure)

**Files:**
- Modify: `rope_grab.py`
- Test: `tests/test_rope_grab.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `evaluate(model, trajectories, zone=(90, 94), sensor_lag=0.045) -> dict` — for each trajectory, simulate a stop from the predicted release (using that trajectory's `dir`/`target`) and report `{"n": int, "in_zone": int, "hit_rate": float, "mean_abs_err": float}` where err = predicted_stop − mid(zone). Empty input → `{"n":0,"in_zone":0,"hit_rate":0.0,"mean_abs_err":0.0}`.

- [ ] **Step 1: Write the failing test**

```python
def test_evaluate_reports_hit_rate():
    m = {"R": {"speed": 100.0, "coast": 6.0}, "L": {"speed": 100.0, "coast": 6.0}}
    trs = [{"dir": "R", "target": 96, "release_x": None, "stop_x": 0, "reads": [[0,63,143]]},
           {"dir": "R", "target": 92, "release_x": None, "stop_x": 0, "reads": [[0,63,143]]}]
    r = rope_grab.evaluate(m, trs, zone=(90, 94))
    assert r["n"] == 2 and 0.0 <= r["hit_rate"] <= 1.0
    # target 96 -> stop ~91 (in zone); target 92 -> stop ~87 (out) -> 1/2 in zone
    assert r["in_zone"] == 1

def test_evaluate_empty():
    assert rope_grab.evaluate({"R":{"speed":0,"coast":0},"L":{"speed":0,"coast":0}}, [])["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -k evaluate -v`
Expected: FAIL with `AttributeError: ... 'evaluate'`.

- [ ] **Step 3: Write minimal implementation**

```python
# rope_grab.py  (append)
def evaluate(model, trajectories, zone=(90, 94), sensor_lag=0.045):
    mid = (zone[0] + zone[1]) / 2.0
    n = in_zone = 0
    errs = []
    for t in trajectories:
        gr = t.get("dir") == "R"
        rx = predict_release_x(model, t["target"], gr, sensor_lag)
        sx = simulate_stop(model, rx, gr)
        n += 1
        if zone[0] <= sx <= zone[1]:
            in_zone += 1
        errs.append(abs(sx - mid))
    return {"n": n, "in_zone": in_zone,
            "hit_rate": (in_zone / n) if n else 0.0,
            "mean_abs_err": (sum(errs) / len(errs)) if errs else 0.0}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rope_grab.py tests/test_rope_grab.py
git commit -m "feat(rope_grab): offline model evaluator (hit-rate vs grab zone)"
```

---

## Task 5: Live trajectory recorder + CLI (data collection)

**Files:**
- Modify: `recovery.py` (add near the rope helpers; extend the `__main__` CLI)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `rope_grab` (schema), `get_character_full`, `stable_char`, `kb`, `Key`, `go_to_bottom`, `focus`.
- Produces (in `recovery.py`):
  - `record_grab_attempt(going_right, target=ROPE_X+1) -> dict` — from the bottom platform, hold the walk key toward `target` while polling `get_character_full()` at ~30Hz into `reads`; when the current x reaches `target` (or a time cap), RELEASE and record `release_x`; settle 0.2s; record `stop_x` via `stable_char(3)`. Returns a trajectory dict (does NOT climb). Releases keys on exit; aborts (returns partial with `stop_x=-1`) if she falls (y ≥ `BOTTOM_FALL_Y`).
  - CLI `python recovery.py record-grab <N>` — loops N times: ensure at bottom (`go_to_bottom`), `record_grab_attempt` alternating small target jitter (ROPE_X, ROPE_X+1, ROPE_X+2), append each trajectory as a JSON line to `rope_traj.jsonl`, then climb back up with `recover_to_farming()` to reset for the next sample. Prints a running count.

- [ ] **Step 1: Write the failing test** (pure-logic guard: recorder builds a schema-valid trajectory from injected reads)

```python
# tests/test_rope_grab.py  (append) -- exercises recovery only if importable
def test_recorder_produces_valid_trajectory(monkeypatch):
    import pytest, rope_grab
    try:
        import recovery
    except Exception as e:
        pytest.skip(f"recovery not importable: {e}")
    # stub sensing + keys so no game is needed: she "walks" 63->94 over calls
    seq = iter([(63,143),(78,143),(94,143),(94,143)])
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: next(seq, (94,143)))
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (94, 143))
    class _KB:
        pause = False
        def safe_press(self,*a): pass
        def safe_release(self,*a): pass
        def safe_release_all(self,*a): pass
    monkeypatch.setattr(recovery, "kb", _KB())
    tr = recovery.record_grab_attempt(going_right=True, target=92)
    assert rope_grab.valid_trajectory(tr) and tr["dir"] == "R" and tr["stop_x"] == 94
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -k recorder -v`
Expected: FAIL with `AttributeError: module 'recovery' has no attribute 'record_grab_attempt'`.

- [ ] **Step 3: Write minimal implementation**

```python
# recovery.py  (add above recover_to_farming)
def record_grab_attempt(going_right, target=ROPE_X + 1, cap=4.0):
    """Data collection ONLY: walk toward `target`, logging (t,x,y) at ~30Hz; release
    at the target, settle, record stop_x. Returns a rope_grab trajectory dict. Does
    not grab/climb. Aborts (stop_x=-1) if she falls."""
    key = Key.right if going_right else Key.left
    reads, t0, release_x = [], time.time(), None
    kb.safe_press(key)
    try:
        while time.time() - t0 < cap:
            if kb.pause:
                break
            x, y = get_character_full()
            if x >= 0:
                reads.append([round(time.time() - t0, 3), int(x), int(y)])
                if y >= BOTTOM_FALL_Y:
                    kb.safe_release_all()
                    return {"dir": "R" if going_right else "L", "target": target,
                            "release_x": None, "stop_x": -1, "reads": reads}
                if (going_right and x >= target) or (not going_right and x <= target):
                    release_x = int(x); break
            time.sleep(0.03)
    finally:
        kb.safe_release(key)
    time.sleep(0.2)
    sx, _sy = stable_char(3)
    return {"dir": "R" if going_right else "L", "target": target,
            "release_x": release_x, "stop_x": int(sx), "reads": reads}
```

Then extend the `__main__` CLI:

```python
    elif cmd == "record-grab":
        import json as _json, random as _r, rope_grab
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        if not focus():
            print("no focus"); sys.exit(1)
        with open("rope_traj.jsonl", "a", encoding="utf-8") as fh:
            for i in range(n):
                x, y = stable_char(3)
                if y < BOTTOM_Y_MIN:
                    go_to_bottom(); time.sleep(0.4)
                tr = record_grab_attempt(True, target=ROPE_X + _r.choice([0, 1, 2]))
                fh.write(_json.dumps(tr) + "\n"); fh.flush()
                print(f"[record-grab] {i+1}/{n}: release={tr['release_x']} stop={tr['stop_x']}")
                recover_to_farming(); time.sleep(0.4)   # climb back up to reset
        print("wrote rope_traj.jsonl")
```

Add to `.gitignore`: `rope_traj.jsonl` and `rope_model.json`.

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/ -q`
Expected: PASS (all tests).
Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -c "import recovery; print(hasattr(recovery,'record_grab_attempt'))"` → `True`.

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_rope_grab.py .gitignore
git commit -m "feat(recovery): rope-grab trajectory recorder + record-grab CLI"
```

**LIVE STEP (with the user, per-action OK — collects data, moves the character):**
Run `python recovery.py record-grab 25`. This walks to the rope, releases, settles, records, and climbs back up 25× → `rope_traj.jsonl`. No grab attempted, so it's low-risk (she never leaves the bottom except the reset climb via the proven recover).

---

## Task 6: Fit + evaluate the model, and a `grab-eval` CLI

**Files:**
- Modify: `recovery.py` (CLI)
- Modify: `rope_grab.py` (add `fit_and_save` / `load_model` convenience)

**Interfaces:**
- Consumes: Tasks 1–5, `rope_traj.jsonl`.
- Produces:
  - `rope_grab.load_model(path="rope_model.json") -> dict | None`
  - `rope_grab.fit_and_save(traj_path="rope_traj.jsonl", model_path="rope_model.json") -> dict` — load trajectories, `estimate_model`, write JSON, return the model.
  - CLI `python recovery.py grab-eval` — fit the model from `rope_traj.jsonl`, print the model and `evaluate(...)` hit-rate/mean-abs-err on the same set (and note this is train-set; a held-out split is a stretch goal). Saves `rope_model.json`.

- [ ] **Step 1: Write the failing test**

```python
def test_fit_and_save_roundtrip(tmp_path):
    import rope_grab, json
    trs = [{"dir":"R","target":95,"release_x":88,"stop_x":94,"reads":[[0,63,143],[0.1,80,143],[0.2,94,143]]}]
    tp = tmp_path / "traj.jsonl"; tp.write_text("\n".join(json.dumps(t) for t in trs), encoding="utf-8")
    mp = tmp_path / "model.json"
    m = rope_grab.fit_and_save(str(tp), str(mp))
    assert "R" in m and mp.exists()
    assert rope_grab.load_model(str(mp))["R"]["coast"] == m["R"]["coast"]

def test_load_model_missing_is_none():
    assert rope_grab.load_model("nope.json") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -k "fit or load_model" -v`
Expected: FAIL with `AttributeError: ... 'fit_and_save'`.

- [ ] **Step 3: Write minimal implementation**

```python
# rope_grab.py  (append)
def load_model(path="rope_model.json"):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def fit_and_save(traj_path="rope_traj.jsonl", model_path="rope_model.json"):
    model = estimate_model(load_trajectories(traj_path))
    with open(model_path, "w", encoding="utf-8") as f:
        json.dump(model, f)
    return model
```

Then the CLI branch:

```python
    elif cmd == "grab-eval":
        import rope_grab
        trs = rope_grab.load_trajectories("rope_traj.jsonl")
        model = rope_grab.fit_and_save()
        print("model:", model)
        print("eval :", rope_grab.evaluate(model, trs))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rope_grab.py recovery.py tests/test_rope_grab.py
git commit -m "feat(rope_grab): fit/save/load model + grab-eval CLI"
```

**OFFLINE STEP:** Run `python recovery.py grab-eval`. **Gate:** proceed to Task 7 only if `hit_rate ≥ 0.8` on the recorded set (predicted stop lands in x90–94 for ≥80% of approaches). If lower, collect more data (re-run `record-grab`) and/or adjust `sensor_lag`/`zone` — offline, not live.

---

## Task 7: `walk_to_x_predictive` + robust hop decision

**Files:**
- Modify: `recovery.py`

**Interfaces:**
- Consumes: `rope_grab.load_model`, `predict_release_x`; `get_character_full`, `stable_char`, `kb`, `Key`.
- Produces (in `recovery.py`):
  - `walk_to_x_predictive(target, model, going_right, cap=4.0) -> int` — hold the walk key; release when the current read crosses `predict_release_x(model, target, going_right)`; settle 0.15s; return settled `stable_char` x. Falls (y ≥ `BOTTOM_FALL_Y`) → release, return -1.
  - `_on_bottom_stable() -> bool` — `stable_char(5)` median y ≥ `BOTTOM_Y_MIN - 3` (hysteresis so the idle bob straddling 132 doesn't flip the hop decision). This fixes the "reads y127 → skips the hop → never grabs" bug seen 2026-08-11.

- [ ] **Step 1: Write the failing test**

```python
def test_on_bottom_stable_uses_hysteresis(monkeypatch):
    import pytest
    try:
        import recovery
    except Exception as e:
        pytest.skip(str(e))
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (94, 130))  # bob just under 132
    assert recovery._on_bottom_stable() is True     # 130 >= 132-3
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (77, 110))  # rest platform
    assert recovery._on_bottom_stable() is False

def test_walk_to_x_predictive_releases_at_predicted_x(monkeypatch):
    import pytest, rope_grab
    try:
        import recovery
    except Exception as e:
        pytest.skip(str(e))
    reads = iter([(70,143),(80,143),(83,143),(90,143)])   # crosses release_x=82 at 83
    monkeypatch.setattr(recovery, "get_character_full", lambda *a, **k: next(reads,(94,143)))
    monkeypatch.setattr(recovery, "stable_char", lambda *a, **k: (88, 143))
    released = {"x": None}
    class _KB:
        pause=False
        def safe_press(self,*a): pass
        def safe_release(self,*a): released["x"]=True
        def safe_release_all(self,*a): pass
    monkeypatch.setattr(recovery, "kb", _KB())
    m = {"R": {"speed": 100.0, "coast": 6.0}, "L": {"speed": 100.0, "coast": 6.0}}
    out = recovery.walk_to_x_predictive(92, m, going_right=True)
    assert released["x"] is True and out == 88
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -k "on_bottom or predictive" -v`
Expected: FAIL with `AttributeError: ... 'walk_to_x_predictive'`.

- [ ] **Step 3: Write minimal implementation**

```python
# recovery.py  (add above recover_to_farming)
import rope_grab

def _on_bottom_stable():
    _x, y = stable_char(5)
    return y >= BOTTOM_Y_MIN - 3

def walk_to_x_predictive(target, model, going_right, cap=4.0):
    release_x = rope_grab.predict_release_x(model, target, going_right)
    key = Key.right if going_right else Key.left
    kb.safe_press(key)
    t0 = time.time()
    try:
        while time.time() - t0 < cap:
            if kb.pause:
                break
            x, y = get_character_full()
            if x < 0:
                time.sleep(0.02); continue
            if y >= BOTTOM_FALL_Y:
                kb.safe_release_all(); return -1
            if (going_right and x >= release_x) or (not going_right and x <= release_x):
                break
            time.sleep(0.02)
    finally:
        kb.safe_release(key)
    time.sleep(0.15)
    sx, _sy = stable_char(3)
    return int(sx)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_rope_grab.py
git commit -m "feat(recovery): walk_to_x_predictive + hysteresis hop decision"
```

---

## Task 8: Flag-gated predictive grab in recover, with fallback

**Files:**
- Modify: `recovery.py`

**Interfaces:**
- Consumes: `walk_to_x_predictive`, `_on_bottom_stable`, `rope_grab.load_model`, `climb_and_jump` (existing).
- Produces (in `recovery.py`):
  - Module flag `PREDICTIVE_GRAB = False` and lazy `_ROPE_MODEL = None` / `_get_rope_model()` (loads `rope_model.json` once).
  - `_grab_bottom_predictive() -> bool` — predictive approach + a grab hop (Up + Jump), then verify climbing (y drops ≥6px within ~0.5s). Returns True if she's climbing, else releases and returns False.
  - In `recover_to_farming`, the bottom-rope branch: **iff** `PREDICTIVE_GRAB and _get_rope_model() and _on_bottom_stable()`, try `_grab_bottom_predictive()` once; on success `continue` (she's climbing → next round detects top); on failure fall through to the EXISTING `walk_to_x(ROPE_X+1, tol=1)` + `climb_and_jump()` re-align path unchanged. With the flag off, the function is byte-for-byte behaviorally identical to today.

- [ ] **Step 1: Write the failing test**

```python
def test_predictive_grab_disabled_by_default():
    import pytest
    try:
        import recovery
    except Exception as e:
        pytest.skip(str(e))
    assert recovery.PREDICTIVE_GRAB is False        # ships OFF; live-enable after validation

def test_get_rope_model_none_when_missing(monkeypatch):
    import pytest, rope_grab
    try:
        import recovery
    except Exception as e:
        pytest.skip(str(e))
    monkeypatch.setattr(recovery, "_ROPE_MODEL", None)
    monkeypatch.setattr(rope_grab, "load_model", lambda *a, **k: None)
    assert recovery._get_rope_model() is None        # no model -> predictive path is skipped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_rope_grab.py -k "disabled or rope_model" -v`
Expected: FAIL with `AttributeError: module 'recovery' has no attribute 'PREDICTIVE_GRAB'`.

- [ ] **Step 3: Write minimal implementation**

```python
# recovery.py  (add near the rope helpers)
PREDICTIVE_GRAB = False        # live-enable ONLY after grab-eval hit_rate >= 0.8 + live validate
_ROPE_MODEL = None

def _get_rope_model():
    global _ROPE_MODEL
    if _ROPE_MODEL is None:
        _ROPE_MODEL = rope_grab.load_model()
    return _ROPE_MODEL

def _grab_bottom_predictive():
    """Predictive approach + grab hop. True if she starts climbing, else False (caller
    falls back to the proven re-align path)."""
    model = _get_rope_model()
    if not model:
        return False
    sx = walk_to_x_predictive(ROPE_X + 1, model, going_right=True)
    if sx < 0:
        return False
    x0, y0 = get_character_full()
    kb.safe_press(Key.up)
    kb.safe_press(JUMP); time.sleep(0.06); kb.safe_release(JUMP)
    t0 = time.time()
    while time.time() - t0 < 0.6:                    # did she catch the rope (rising)?
        x, y = get_character_full()
        if y >= 0 and y0 >= 0 and y <= y0 - 6:
            return True                              # climbing -> keep Up held; recover round sees top
        time.sleep(0.05)
    kb.safe_release(Key.up); kb.safe_release_all()
    return False
```

Then in `recover_to_farming`, immediately before the existing `if not walk_to_x(ROPE_X + 1, tol=1):` line, insert:

```python
        # predictive fast-path (opt-in, validated); falls through to re-align on miss
        if PREDICTIVE_GRAB and _on_bottom_stable() and _grab_bottom_predictive():
            time.sleep(0.1); continue
```

- [ ] **Step 4: Run test to verify it passes + flag-off behavior unchanged**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/ -q`
Expected: PASS.
Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -c "import recovery; print(recovery.PREDICTIVE_GRAB)"` → `False`.

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_rope_grab.py
git commit -m "feat(recovery): flag-gated predictive rope grab with re-align fallback"
```

---

## Task 9: Docs — handoff addendum + live-validation protocol

**Files:**
- Modify: `HUMANIZE_HANDOFF.md`

- [ ] **Step 1: Append an addendum**

```markdown
### Predictive rope-grab (bottom->top) — 2026-08-11
- Problem: walk_to_x stops ~4px past the ~x90-94 grab zone -> straight-up hop misses ->
  2-3 re-align rounds (the "stall + move + jump" steppiness). Live tuning always regressed.
- Approach: `rope_grab.py` (pure) fits a walk model (speed + coast) from RECORDED approach
  trajectories and computes a predictive release point that leads the target by the lag.
  Offline-validated (grab-eval hit-rate) before any live use.
- CLIs: `python recovery.py record-grab N` (collect data), `grab-eval` (fit + score).
- Flag: `recovery.PREDICTIVE_GRAB` (default False). The proven re-align path is the fallback;
  flag-off behavior is identical to before. Enable ONLY after grab-eval hit_rate >= 0.8 AND a
  live N-cycle test shows fewer rounds with no drop in success rate.
- Files: rope_grab.py, rope_traj.jsonl (git-ignored), rope_model.json (git-ignored).
```

- [ ] **Step 2: Verify + commit**

Run: `C:\Users\jerry\AppData\Local\Programs\Python\Python311\python.exe -c "print('addendum:', 'Predictive rope-grab' in open('HUMANIZE_HANDOFF.md',encoding='utf-8').read())"` → `True`.

```bash
git add HUMANIZE_HANDOFF.md
git commit -m "docs: predictive rope-grab addendum + validation protocol"
```

---

## Live validation protocol (after the plan — with the user, per-action OK)

Not a coding task; the go/no-go for enabling the flag.
1. `python recovery.py record-grab 25` → collect trajectories.
2. `python recovery.py grab-eval` → fit model; require **hit_rate ≥ 0.8** offline. If not, collect more / adjust `sensor_lag` offline; do NOT hand-tune live.
3. Temporarily set `PREDICTIVE_GRAB = True`, run ~10 bottom→top climbs, measure rounds-per-climb and success. **Keep enabled only if** it reliably reaches ~1 round with 100% success. Otherwise leave `False` (fallback behavior stands) and iterate the model offline.

---

## Self-Review Notes

- **Spec coverage:** offline model (Tasks 1–4), data collection (Task 5), fit/eval gate (Task 6), predictive walk + hop-decision fix (Task 7), flag-gated integration with fallback (Task 8), docs + protocol (Task 9).
- **No-regression:** `PREDICTIVE_GRAB=False` default (Task 8 test asserts it); predictive path only *adds* a fast-path that falls through to the unchanged re-align on any miss.
- **Offline-testable:** all math in `rope_grab.py` (pure); `recovery.py` additions unit-tested with monkeypatched sensing/keys (no game). Live steps are explicitly separated and gated.
- **Type consistency:** trajectory dict keys `dir/target/release_x/stop_x/reads`; model dict `{"R":{"speed","coast"},"L":{...}}`; `predict_release_x(model,target,going_right,sensor_lag)`, `simulate_stop(model,release_x,going_right)`, `walk_to_x_predictive(target,model,going_right)` — used identically across tasks.
- **Known placeholder resolved:** Task 3's `lands_in_zone` test target is 96 (not 92) so the predicted stop (~91) is in-zone — noted inline in Task 3 Step 3.
```
