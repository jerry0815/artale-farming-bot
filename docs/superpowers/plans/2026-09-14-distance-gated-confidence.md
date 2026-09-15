# Distance-Gated Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate mob targeting so a far, low-confidence fishhouse detection is ignored (needs a high acquire bar) while a close one is kept at the low floor, stopping the bot from locking/firing at weak far phantoms.

**Architecture:** Two pure module-level helpers in `recovery.py` — `same_platform_mobs` (extract the existing `same_platform` closure and add the detection score) and `trustworthy_same` (drop a priority-class detection that is far AND below the acquire confidence). `approach_shoot` calls both; the gate runs once on the same-platform set so count/deplete/lock/nearest all see the trustworthy set. Enabled per-map via config; default is a no-op.

**Tech Stack:** Python (stdlib), pytest.

## Global Constraints

- Gate applies to `priority_class` (fishhouse) detections only; other classes (goby) pass through unfiltered.
- Detector floor stays `fishhouse_conf` (0.3); the new bar is applied in `approach_shoot`, not the detector.
- Default no-op: `priority_acquire_conf` unset (None) → `trustworthy_same` returns its input unchanged.
- Same-platform tuple shape is `(cx, feet, cls, score)`; existing consumers use `m[0]` (x) and `m[2]` (cls).
- A detection with a missing/None score is treated as below the bar (kept only if close).

---

### Task 1: `same_platform_mobs` — extract the closure and carry the score

**Files:**
- Modify: `recovery.py` — add module-level function above `def approach_shoot` (~line 1945); replace the inner `same_platform` closure (2097-2105) and its call (2108).
- Test: `tests/test_approach.py`

**Interfaces:**
- Produces: `same_platform_mobs(dets, pfeet, band) -> list[tuple]`. Each det is `(score, x, y, w, h)` or `(score, x, y, w, h, cls)`. Returns `(cx, feet, cls, score)` for every det whose `feet = y+h` is within `band` of `pfeet`, where `cx = x + w//2`. `cls` is the 6th det field or `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_approach.py  (add near the other approach tests)
import recovery


def test_same_platform_mobs_carries_score_and_class():
    # feet = y+h; band around pfeet. (score,x,y,w,h,cls)
    dets = [(0.9, 100, 700, 40, 60, "fishhouse"),   # feet=760, cx=120
            (0.5, 900, 700, 20, 30, "goby"),         # feet=730, cx=910
            (0.8, 300, 200, 40, 60, "fishhouse")]    # feet=260 -> off-band, dropped
    out = recovery.same_platform_mobs(dets, pfeet=755, band=60)
    assert out == [(120, 760, "fishhouse", 0.9), (910, 730, "goby", 0.5)]


def test_same_platform_mobs_no_class_field():
    out = recovery.same_platform_mobs([(0.7, 100, 700, 40, 60)], pfeet=755, band=60)
    assert out == [(120, 760, None, 0.7)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_approach.py::test_same_platform_mobs_carries_score_and_class -q`
Expected: FAIL (`recovery` has no attribute `same_platform_mobs`)

- [ ] **Step 3: Write the module-level function**

Add above `def approach_shoot(` (~line 1945):

```python
def same_platform_mobs(dets, pfeet, band):
    """Detections whose feet (box bottom) are within `band` px of the player's feet,
    as (cx, feet, cls, score). A det is (score,x,y,w,h) or (score,x,y,w,h,cls); the
    class (or None) and the score are carried through for per-mob skill + confidence gating."""
    out = []
    for d in dets:
        _s, mx, my, mw, mh = d[:5]
        feet = my + mh
        if abs(feet - pfeet) <= band:
            out.append((mx + mw // 2, feet, d[5] if len(d) > 5 else None, _s))
    return out
```

- [ ] **Step 4: Replace the closure and its call in `approach_shoot`**

Delete the closure (2097-2105):

```python
            def same_platform(dets):
                # A detection is (score,x,y,w,h) or (score,x,y,w,h,cls_name) -> carry the class
                # (or None) through so the attack key can be chosen per mob. `d[:5]` handles both.
                out = []
                for d in dets:
                    _s, mx, my, mw, mh = d[:5]
                    if abs((my + mh) - pfeet) <= band:
                        out.append((mx + mw // 2, my + mh, d[5] if len(d) > 5 else None))
                return out
```

Change the call (2108) from:

```python
            same = same_platform(dets)
```
to:
```python
            same = same_platform_mobs(dets, pfeet, band)
```

- [ ] **Step 5: Run tests to verify pass + no regressions**

Run: `python -m pytest tests/test_approach.py -q`
Expected: PASS (new tests + existing approach tests still green — the added `m[3]` score is additive)

- [ ] **Step 6: Commit**

```bash
git add recovery.py tests/test_approach.py
git commit -m "refactor(water): extract same_platform_mobs, carry detection score"
```

---

### Task 2: `trustworthy_same` — distance-gated confidence filter

**Files:**
- Modify: `recovery.py` — add module-level function directly after `same_platform_mobs`.
- Test: `tests/test_approach.py`

**Interfaces:**
- Consumes: same-platform tuples `(cx, feet, cls, score)` from Task 1.
- Produces: `trustworthy_same(same, px, priority_class, low_conf_range, acquire_conf) -> list`. Drops a `priority_class` item that is farther than `low_conf_range` from `px` AND whose score is below `acquire_conf` (or missing). All other items are kept. `acquire_conf is None` → return `same` unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_trustworthy_same_drops_far_weak_priority():
    px = 744
    same = [
        (899, 760, "fishhouse", 0.59),   # 155px away, weak -> FAR + weak -> drop
        (450, 760, "fishhouse", 0.97),   # 294px away, strong -> kept
        (760, 760, "fishhouse", 0.40),   # 16px away -> CLOSE -> kept at low conf
        (1200, 760, "goby", 0.30),       # non-priority -> always kept
    ]
    out = recovery.trustworthy_same(same, px, "fishhouse", low_conf_range=120, acquire_conf=0.6)
    assert (899, 760, "fishhouse", 0.59) not in out
    assert (450, 760, "fishhouse", 0.97) in out
    assert (760, 760, "fishhouse", 0.40) in out
    assert (1200, 760, "goby", 0.30) in out


def test_trustworthy_same_disabled_when_no_acquire_conf():
    same = [(899, 760, "fishhouse", 0.59)]
    assert recovery.trustworthy_same(same, 744, "fishhouse", 120, None) == same


def test_trustworthy_same_missing_score_far_dropped():
    same = [(899, 760, "fishhouse", None)]   # far, no score -> treated as below bar -> drop
    assert recovery.trustworthy_same(same, 744, "fishhouse", 120, 0.6) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_approach.py::test_trustworthy_same_drops_far_weak_priority -q`
Expected: FAIL (`recovery` has no attribute `trustworthy_same`)

- [ ] **Step 3: Write the function**

Add directly after `same_platform_mobs`:

```python
def trustworthy_same(same, px, priority_class, low_conf_range, acquire_conf):
    """Filter same-platform mobs by distance-gated confidence: a priority_class mob that is
    FAR from the player (|cx-px| > low_conf_range) AND below acquire_conf (or scoreless) is
    dropped -- so weak far phantoms aren't targeted -- while a CLOSE one is kept at the low
    detector floor (she's on top of it, killing it). Non-priority mobs pass through. When
    acquire_conf is None the gate is off and `same` is returned unchanged."""
    if acquire_conf is None:
        return same
    out = []
    for m in same:
        cx, cls = m[0], m[2]
        score = m[3] if len(m) > 3 else None
        if cls == priority_class and abs(cx - px) > low_conf_range and not (
                score is not None and score >= acquire_conf):
            continue
        out.append(m)
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_approach.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add recovery.py tests/test_approach.py
git commit -m "feat(water): trustworthy_same distance-gated confidence filter"
```

---

### Task 3: Wire the gate into `approach_shoot` + `farm_node` + config

**Files:**
- Modify: `recovery.py` — `approach_shoot` signature (~1946-1952) and body (after line 2108); `farm_node`'s `approach_shoot(...)` call (~3063-3087).
- Modify: `maps/deep_sea_2.json` — add `low_conf_range` + `priority_acquire_conf` with a note.

**Interfaces:**
- Consumes: `trustworthy_same` (Task 2), `same_platform_mobs` (Task 1).
- Produces: `approach_shoot(...)` gains keyword params `low_conf_range=250, priority_acquire_conf=None`.

- [ ] **Step 1: Add the params to `approach_shoot`**

In the signature (ends ~line 1952 with `lost_limit=12, collect_misses=False, fail_dump_interval=3.0):`), add before `combined=None,`:

```python
                   low_conf_range=250, priority_acquire_conf=None,
```

(Default `priority_acquire_conf=None` keeps the gate off; `low_conf_range=250` is inert while off.)

- [ ] **Step 2: Apply the gate after the same-platform read**

In `approach_shoot`, change (line ~2108-2109):

```python
            same = same_platform_mobs(dets, pfeet, band)
            STATUS["count"] = len(same)                    # live same-platform mob count for the panel
```
to:
```python
            same = same_platform_mobs(dets, pfeet, band)
            same = trustworthy_same(same, px, priority_class, low_conf_range, priority_acquire_conf)
            STATUS["count"] = len(same)                    # live same-platform mob count for the panel
```

(`px` is defined just above at `px, pfeet = p`.)

- [ ] **Step 3: Pass the config through from `farm_node`**

In `farm_node`'s `approach_shoot(...)` call, alongside the other `map_cfg.get(...)` args (near `priority_lock_grace=...`, ~line 3077), add:

```python
                                    low_conf_range=int(map_cfg.get("low_conf_range", 250)),
                                    priority_acquire_conf=map_cfg.get("priority_acquire_conf"),
```

- [ ] **Step 4: Enable it in the map config**

In `maps/deep_sea_2.json`, after the `"same_platform_band"` entry, add:

```json
  "low_conf_range": 120,
  "priority_acquire_conf": 0.6,
  "conf_gate_note": "Distance-gated confidence: a fishhouse FARTHER than low_conf_range(120 screen px) from the player must clear priority_acquire_conf(0.6) or it's ignored; CLOSER ones are kept at the fishhouse_conf(0.3) floor (she's point-blank killing it, VFX dims it). Stops locking/firing at weak far phantoms (the P1 wrong-direction bug: a 0.59 fishhouse 155px away beat the real 0.97 one). Raise low_conf_range or lower priority_acquire_conf if a real far fishhouse gets skipped (real ones read ~0.9+); remove priority_acquire_conf to disable.",
```

- [ ] **Step 5: Verify the gate end-to-end + full suite**

Run:
```bash
python - <<'PY'
import recovery
same = [(899,760,"fishhouse",0.59),(450,760,"fishhouse",0.97),(1200,760,"goby",0.30)]
print("gated:", recovery.trustworthy_same(same, 744, "fishhouse", 120, 0.6))
PY
python -m pytest -q
```
Expected: `gated:` shows the 0.59 fishhouse removed, the 0.97 fishhouse and the goby kept; full suite PASS.

- [ ] **Step 6: Commit**

```bash
git add recovery.py maps/deep_sea_2.json
git commit -m "feat(water): enable distance-gated confidence on deep_sea_2"
```

---

## Self-Review

- **Spec coverage:** score carried through same-platform → Task 1; `trustworthy_same` pure gate with fishhouse-only + acquire_conf=None no-op + missing-score handling → Task 2; single filter point after `same=` so count/deplete/lock/nearest all see the gated set → Task 3 Step 2; params threaded from `farm_node` + config with note → Task 3 Steps 1,3,4; unit tests for all branches → Tasks 1-2. All spec sections covered.
- **Placeholder scan:** none; every code step is concrete.
- **Type consistency:** same-platform tuple is `(cx, feet, cls, score)` in `same_platform_mobs`, `trustworthy_same`, and both test suites; `trustworthy_same(same, px, priority_class, low_conf_range, acquire_conf)` signature matches its call in Task 3 Step 2 (`priority_acquire_conf` passed as `acquire_conf`); param defaults (`low_conf_range=250`, `priority_acquire_conf=None`) match between signature and `farm_node` call.
