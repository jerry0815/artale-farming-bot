# Enemy-Dots Color Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace template matching in the production another-player (red-dot) safety check with an HSV color detector, mirroring the yellow-dot approach already used for the character.

**Architecture:** Add a standalone `detect_red_dots_color(minimap_bgr, ...)` to `detection.py` that HSV-thresholds a minimap crop, runs connected-components, size-filters blobs, and returns `[cx, cy]` centers — the same output shape as the existing `detect_red_dots`. Rewire only the production path (`recovery._enemy_dots`) to call it. The template `detect_red_dots` and its assets stay in place (a diagnostic tool, `live_check.py`, still uses them).

**Tech Stack:** Python, OpenCV (`cv2`), NumPy, pytest. No new dependencies.

## Global Constraints

- **Safety is presence-only.** The another-player check reports "is any red dot in the minimap band" — a boolean of `len(...) > 0`. It needs a size floor to reject stray red pixels but no temporal tracking.
- **Output shape is fixed:** `detect_red_dots_color` returns `list[[int, int]]` (blob centers `[cx, cy]`), identical to `detect_red_dots`, so `_enemy_dots` / `get_enemy` / `panel.py` are unchanged downstream.
- **No regression path:** the color detector must never raise into the safety loop — `_enemy_dots` already wraps the call in try/except returning `[]`; keep that.
- **No new dependencies** — OpenCV + NumPy only.
- **HSV values are derived from the real red-dot crops** in `assets/minimap_other_character/*.png`, not guessed, then verified.
- Keep `recovery.ENEMY_THRESHOLD` / `set_enemy_threshold` defined (unused by the color path) so `recovery.py:2383` and `tests/test_character.py` keep working; do not delete the template `detect_red_dots` (still used by `live_check.py`).

---

### Task 1: `detect_red_dots_color` in `detection.py`

**Files:**
- Modify: `detection.py` (add function + module constants; `cv2`, `numpy as np` already imported at top)
- Test: `tests/test_detection_red_color.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `detect_red_dots_color(minimap_bgr, hsv_ranges=RED_HSV_RANGES, min_area=RED_MIN_AREA, max_area=RED_MAX_AREA) -> list[[int, int]]` — list of blob centers `[cx, cy]` in minimap-crop pixel coords. Also exports module constants `RED_HSV_RANGES` (list of `(lo, hi)` `np.uint8` triples, two entries to cover the hue-0 wrap), `RED_MIN_AREA=8`, `RED_MAX_AREA=250`.

- [ ] **Step 1: Derive the HSV signature from the real crops**

Run this one-off to see the actual red-dot HSV distribution (informs the constants; not committed):

```bash
python -c "
import cv2, numpy as np, glob
for p in glob.glob('assets/minimap_other_character/*.png'):
    img = cv2.imread(p)                      # BGR, alpha dropped -> transparent corners read black
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = (hsv[:,:,1] > 100) & (hsv[:,:,2] > 100)   # ignore the dark/black corners
    px = hsv[m]
    if len(px):
        print(p, 'H', px[:,0].min(), px[:,0].max(), 'S', px[:,1].min(), 'V', px[:,2].min(), 'n', len(px))
"
```

Expected: hues clustered near 0 (and possibly wrapping to ~179), high S and V. Confirm the two default ranges below cover the observed spread; widen only if a crop falls outside.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_detection_red_color.py
"""HSV red-dot detector for the another-player minimap safety check."""
import cv2
import numpy as np
import detection


def _dark_minimap(h=120, w=200):
    """A dark minimap-like background (no red)."""
    return np.full((h, w, 3), 30, dtype=np.uint8)   # BGR near-black


def _paste(bg, tpl, x, y):
    h, w = tpl.shape[:2]
    out = bg.copy()
    out[y:y + h, x:x + w] = tpl
    return out


def test_detects_a_pasted_red_dot():
    bg = _dark_minimap()
    tpl = cv2.imread("assets/minimap_other_character/red_dot.png")   # real crop
    assert tpl is not None
    h, w = tpl.shape[:2]
    frame = _paste(bg, tpl, 100, 60)
    centers = detection.detect_red_dots_color(frame)
    assert len(centers) == 1
    cx, cy = centers[0]
    assert abs(cx - (100 + w // 2)) <= 4
    assert abs(cy - (60 + h // 2)) <= 4


def test_solo_background_is_empty():
    assert detection.detect_red_dots_color(_dark_minimap()) == []


def test_rejects_a_two_pixel_speck():
    bg = _dark_minimap()
    bg[10:12, 10:12] = (60, 60, 240)   # 4px of red -> below min_area
    assert detection.detect_red_dots_color(bg) == []


def test_two_players_two_centers():
    bg = _dark_minimap()
    tpl = cv2.imread("assets/minimap_other_character/red_dot.png")
    frame = _paste(_paste(bg, tpl, 40, 30), tpl, 140, 80)
    assert len(detection.detect_red_dots_color(frame)) == 2
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_detection_red_color.py -v`
Expected: FAIL with `AttributeError: module 'detection' has no attribute 'detect_red_dots_color'`

- [ ] **Step 4: Implement the detector**

Add to `detection.py` (near the other minimap detectors; mirrors `recovery._char_color_from_mm`):

```python
# Red minimap dots (other players). Bright, saturated red; hue sits at the 0 end of the
# OpenCV H wheel (0-179) and can wrap to the top, so two ranges are OR'd. Derived from
# assets/minimap_other_character/*.png. Size band rejects 1-2px specks and oversized blobs;
# the red dot is the same marker size class as the yellow character dot (~15-120px area),
# widened here to 8-250 to stay tolerant across window sizes -- verified on real frames.
RED_HSV_RANGES = [
    (np.array([0, 120, 120], np.uint8),   np.array([10, 255, 255], np.uint8)),
    (np.array([170, 120, 120], np.uint8), np.array([179, 255, 255], np.uint8)),
]
RED_MIN_AREA = 8
RED_MAX_AREA = 250


def detect_red_dots_color(minimap_bgr, hsv_ranges=RED_HSV_RANGES,
                          min_area=RED_MIN_AREA, max_area=RED_MAX_AREA):
    """Red dots (other players) on a MINIMAP crop via HSV color -- the color analogue of
    detect_red_dots (template). Presence-only: returns each qualifying blob's center
    [cx, cy]; the caller only checks whether the list is non-empty. Robust and ~1ms vs
    the multi-template match. Mirrors recovery._char_color_from_mm."""
    if minimap_bgr is None or minimap_bgr.size == 0:
        return []
    hsv = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2HSV)
    mask = None
    for lo, hi in hsv_ranges:
        m = cv2.inRange(hsv, lo, hi)
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    num, _labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    centers = []
    for i in range(1, num):                       # 0 is the background component
        if min_area <= stats[i, cv2.CC_STAT_AREA] <= max_area:
            centers.append([int(cents[i][0]), int(cents[i][1])])
    return centers
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_detection_red_color.py -v`
Expected: PASS (4 tests). If `test_detects_a_pasted_red_dot` finds 0 blobs, the real crop's hue/area fell outside the defaults — use the Step 1 output to widen `RED_HSV_RANGES` / lower `RED_MIN_AREA`, then re-run.

- [ ] **Step 6: Commit**

```bash
git add detection.py tests/test_detection_red_color.py
git commit -m "feat(detect): HSV red-dot detector for the another-player check

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Rewire `recovery._enemy_dots` to the color detector

**Files:**
- Modify: `recovery.py:20` (import), `recovery.py:514-524` (`_enemy_dots`)
- Test: `tests/test_enemy_dots.py` (create)

**Interfaces:**
- Consumes: `detection.detect_red_dots_color(minimap_bgr) -> list[[int, int]]` (Task 1).
- Produces: `_enemy_dots(frame)` unchanged signature/return (`list[[int, int]]`); `get_enemy()` unchanged. No new public API.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_enemy_dots.py
"""Production another-player check reads red dots via HSV color, crop-clamped, crash-safe."""
import numpy as np
import recovery


def test_enemy_dots_crops_minimap_and_returns_color_centers(monkeypatch):
    seen = {}

    def fake_color(mm, *a, **k):
        seen["shape"] = mm.shape          # confirm it received the minimap crop, not the full frame
        return [[5, 6]]

    monkeypatch.setattr(recovery, "detect_red_dots_color", fake_color)
    frame = np.zeros((recovery.MM_Y + recovery.MM_H + 50,
                      recovery.MM_X + recovery.MM_W + 50, 3), dtype=np.uint8)
    dots = recovery._enemy_dots(frame)
    assert dots == [[5, 6]]
    assert seen["shape"][0] <= recovery.MM_H and seen["shape"][1] <= recovery.MM_W


def test_enemy_dots_none_frame_is_empty():
    assert recovery._enemy_dots(None) == []


def test_enemy_dots_swallows_detector_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cv2 blew up")

    monkeypatch.setattr(recovery, "detect_red_dots_color", boom)
    frame = np.zeros((recovery.MM_Y + recovery.MM_H + 50,
                      recovery.MM_X + recovery.MM_W + 50, 3), dtype=np.uint8)
    assert recovery._enemy_dots(frame) == []      # never propagates into the safety loop
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_enemy_dots.py -v`
Expected: FAIL — `test_...crops...` fails with `AttributeError: ... has no attribute 'detect_red_dots_color'` (the name isn't imported into `recovery` yet).

- [ ] **Step 3: Update the import**

In `recovery.py:20`, add `detect_red_dots_color` to the existing import:

```python
from detection import (detect_character_on_minimap, detect_red_dots,
                       detect_red_dots_color, detect_lie_check)
```

(`detect_red_dots` stays imported — it is still referenced elsewhere and by `live_check.py`.)

- [ ] **Step 4: Swap the detector call in `_enemy_dots`**

Replace the body of `_enemy_dots` (`recovery.py:514-524`) — keep the crop and the try/except exactly; only change the detector call:

```python
def _enemy_dots(frame):
    """Red dots (other players) in the minimap band of a captured BGR frame, via HSV color
    (detect_red_dots_color). Presence-only -> non-empty means escape."""
    if frame is None:
        return []
    ey = min(MM_Y + MM_H, frame.shape[0]); ex = min(MM_X + MM_W, frame.shape[1])
    mm = frame[MM_Y:ey, MM_X:ex]                  # FULL configured minimap band (lower platforms too)
    try:
        return detect_red_dots_color(mm)
    except Exception:
        return []
```

(`ENEMY_THRESHOLD` and `set_enemy_threshold` remain defined and are simply no longer consulted by this path — kept for `recovery.py:2383` and `tests/test_character.py`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_enemy_dots.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the regression guard (nothing else broke)**

Run: `python -m pytest tests/test_character.py tests/test_detection_red_color.py tests/test_enemy_dots.py -v`
Expected: PASS. (`test_character.py` still monkeypatches `set_enemy_threshold`, which still exists.)

- [ ] **Step 7: Commit**

```bash
git add recovery.py tests/test_enemy_dots.py
git commit -m "feat(water): another-player check uses HSV color, not template

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Manual live validation (gate — not CI)

**Files:** none (verification only).

This is the spec's tuning gate: confirm parity against real frames before trusting the swap in production.

- [ ] **Step 1: Gather frames**

Use existing saved frames that contain the minimap. If none show another player's red dot, capture one live (F10 dump path in `recovery.py` saves to `datasets/lie_check/captures/`) with a second player on the map, and one solo frame.

- [ ] **Step 2: Compare color vs template on each frame**

```bash
python -c "
import cv2, glob, recovery
from detection import detect_red_dots, detect_red_dots_color
for p in sorted(glob.glob('datasets/lie_check/captures/*.png'))[:20]:
    f = cv2.imread(p)
    mm = f[recovery.MM_Y:recovery.MM_Y+recovery.MM_H, recovery.MM_X:recovery.MM_X+recovery.MM_W]
    t = detect_red_dots(mm, templates_folder='assets/minimap_other_character/', threshold=0.75)
    c = detect_red_dots_color(mm)
    print(p.split(chr(92))[-1], 'template', len(t), 'color', len(c))
"
```

- [ ] **Step 3: Confirm the gate**

Expected: on frames with another player, color finds ≥ the template's dot(s); on solo frames, color reports 0. If a solo frame yields a false blob, raise `RED_MIN_AREA` or tighten `S`/`V` floors; if a real dot is missed, widen the hue range or lower `RED_MIN_AREA`. Re-run Task 1's tests after any constant change.

- [ ] **Step 4: Record the outcome**

Note the final constants and the frame counts in the commit message of any tuning change, so the gate result is captured in history.

---

## Notes for the next plans (not in scope here)

- **Lie-check → YOLO primary:** recall-measurement script over `datasets/lie_check/captures/` + gated retirement of the monster template pairs.
- **Nametag → YOLO class:** add class 3 to `mob_yolo`, fuse into `YoloPlayerAnchor` with nearest-to-prior disambiguation; carries the dataset re-label + retrain cost. Keep the template `NametagAnchor` fallback until measured.
