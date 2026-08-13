# YOLO Blue-Wing-Dragon Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the over-counting motion-based dragon counter with a single-class YOLO11n detector that returns per-dragon boxes (count now, positions later), with the motion method kept as an automatic fallback.

**Architecture:** Three helper scripts collect frames, pre-label them from the existing motion detector, and train a YOLO11n model. New functions in `monsters.py` run the model and count boxes whose centers fall in the per-node ROI band; `recovery.py` calls one dispatcher that uses YOLO when the model file exists and falls back to the motion counter otherwise. `ultralytics`/`torch` are lazy-imported so offline tests never touch the GPU stack.

**Tech Stack:** Python, OpenCV (`cv2`), NumPy, `ultralytics` (YOLO11n) on CUDA `torch`, pytest.

## Global Constraints

- **Single class:** `dragon`, class id `0`. Direction-agnostic (horizontal-flip augmentation covers facing).
- **Lazy-import only:** `ultralytics`/`torch` must NEVER be imported at module top level. Import them *inside* `load_dragon_model` / `run_yolo` / the training script, so `import monsters` and all offline tests work without the GPU stack installed.
- **Clean training frames:** frames saved for training carry NO overlay text/boxes (unlike `record_climb_frames.py`, which stamps text). Overlays would poison the model.
- **Reuse existing ROI bands:** `monsters.MOTION_ROI_BY_NODE` / `monsters.DEFAULT_MOTION_ROI` are reused as the YOLO box-center filter band. Do not invent new ROI constants.
- **Additive:** do NOT modify or delete `detect_dragons`, `count_dragons`, motion functions, or templates. They remain as fallback and pre-labeler.
- **Large binaries gitignored:** captured images and `*.pt` weights are never committed.
- **Model path:** `models/dragon_yolo.pt` (constant `monsters.DRAGON_MODEL_PATH`).
- **YOLO label format:** one box per line, `0 cx cy w h`, all normalized to [0,1], 6 decimals.

---

### Task 1: Dependencies & dataset scaffolding

**Files:**
- Create: `datasets/dragons/classes.txt`
- Create: `datasets/dragons/.gitkeep`
- Modify: `.gitignore` (append, create if absent)

**Interfaces:**
- Consumes: nothing.
- Produces: the `datasets/dragons/{images,labels}/` layout, `classes.txt` (used by LabelImg + training), and gitignore rules that later tasks rely on.

- [ ] **Step 1: Install ultralytics (pulls CUDA torch)**

```bash
pip install ultralytics
```

- [ ] **Step 2: Verify the install and GPU are visible**

```bash
python -c "import torch, ultralytics; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
```
Expected: prints a torch version and `cuda True`. If `cuda False`, the model will still train/run on CPU but slower — acceptable, not a blocker.

- [ ] **Step 3: Create the dataset folders and class file**

```bash
mkdir -p datasets/dragons/images datasets/dragons/labels
printf 'dragon\n' > datasets/dragons/classes.txt
: > datasets/dragons/.gitkeep
```

- [ ] **Step 4: Append gitignore rules for large binaries**

Append these lines to `.gitignore` (create the file if it does not exist).
Note: `labels/` and `classes.txt` are intentionally NOT ignored — the
hand-corrected labels are the expensive human artifact and are small text, so
they are version-controlled. Only large binaries and generated files are ignored:

```gitignore
# YOLO dragon detector — large binaries & generated files (labels are tracked)
datasets/dragons/images/
datasets/dragons/train.txt
datasets/dragons/val.txt
datasets/dragons/dragons.yaml
models/*.pt
runs/
```

- [ ] **Step 5: Verify layout**

```bash
ls datasets/dragons && cat datasets/dragons/classes.txt
```
Expected: shows `classes.txt  images  labels` (and `.gitkeep`), and prints `dragon`.

- [ ] **Step 6: Commit**

```bash
git add .gitignore datasets/dragons/classes.txt datasets/dragons/.gitkeep
git commit -m "chore: scaffold YOLO dragon dataset + ignore rules"
```

---

### Task 2: Frame-capture script (`capture_frames.py`)

**Files:**
- Create: `capture_frames.py`
- Test: `tests/test_capture_frames.py`

**Interfaces:**
- Consumes: `recovery.capture` / `recovery.focus` / `recovery.kb` (F8 pause), mirrors `record_climb_frames.py`.
- Produces: `save_frame(outdir, n, img) -> str` (writes `frame_{n:05d}.png`, returns path) and a `main()` that grabs clean frames at a target fps into `datasets/dragons/images/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_capture_frames.py
import os
import numpy as np
import capture_frames

def test_save_frame_writes_clean_png(tmp_path):
    img = np.zeros((8, 8, 3), np.uint8)
    img[2, 2] = (200, 50, 50)
    p = capture_frames.save_frame(str(tmp_path), 7, img)
    assert p.endswith("frame_00007.png")
    assert os.path.exists(p)

def test_save_frame_roundtrips_pixels_unmodified(tmp_path):
    import cv2
    img = np.random.randint(0, 255, (12, 10, 3), np.uint8)
    p = capture_frames.save_frame(str(tmp_path), 1, img)
    back = cv2.imread(p, cv2.IMREAD_COLOR)
    assert back.shape == img.shape
    assert np.array_equal(back, img)   # no overlay drawn on training frames
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_capture_frames.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_frames'`.

- [ ] **Step 3: Write minimal implementation**

```python
# capture_frames.py
"""Clean frame collector for the YOLO dragon dataset. Presses NO keys — you farm
manually while this grabs raw game-window frames. Saved frames carry NO overlay
(unlike record_climb_frames.py) so they are usable as training images.

Usage:
    python capture_frames.py [outdir] [fps]
      outdir : where to save frames (default: datasets/dragons/images)
      fps    : frames per second (default: 8)
Farm across BOTH platforms and varied densities (0/few/many/overlapping);
press F8 to stop. Aim for ~200-400 frames.
"""
import os, sys, time
import cv2
import recovery as R
from record_climb_frames import robust_capture   # reuse the straddle-safe grab
from pynput.keyboard import Listener


def save_frame(outdir, n, img):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"frame_{n:05d}.png")
    cv2.imwrite(path, img)
    return path


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join("datasets", "dragons", "images")
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    cap_secs = 600.0

    if not R.focus():
        print("could not focus game window"); return
    lis = Listener(on_press=R.kb.on_press); lis.start()   # F8 stops
    R.kb.pause = False
    print(f"[cap] farm both platforms; press F8 to stop. Saving clean frames to {outdir}/")

    t0, n, interval, next_t, misses = time.time(), 0, 1.0 / fps, 0.0, 0
    try:
        while time.time() - t0 < cap_secs:
            if R.kb.pause:
                print("[cap] F8 -> stop"); break
            t = time.time() - t0
            if t < next_t:
                time.sleep(0.01); continue
            next_t = t + interval
            img = robust_capture()
            if img is None:
                misses += 1
                if misses in (1, 10, 50):
                    print(f"[cap] capture() None x{misses} (game window not found?)")
                continue
            save_frame(outdir, n, img)
            n += 1
            if n % 25 == 0:
                print(f"[cap] saved {n} frames")
    finally:
        R.kb.safe_release_all(); lis.stop()
    print(f"[cap] wrote {n} frames to {outdir}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_capture_frames.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add capture_frames.py tests/test_capture_frames.py
git commit -m "feat: capture_frames.py — clean frame collector for dragon dataset"
```

---

### Task 3: Motion pre-labeler (`prelabel_dragons.py`)

**Files:**
- Create: `prelabel_dragons.py`
- Test: `tests/test_prelabel_dragons.py`

**Interfaces:**
- Consumes: `monsters.background_plate`, `monsters.foreground_area`, `monsters.DEFAULT_MOTION_ROI`.
- Produces: `to_yolo_line(box, img_w, img_h, cls=0) -> str` (a normalized YOLO label line) and a `main()` that reads `datasets/dragons/images/*.png`, builds one background plate, and writes rough `datasets/dragons/labels/<stem>.txt` per frame.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prelabel_dragons.py
import prelabel_dragons as pl

def test_to_yolo_line_center_and_size_normalized():
    # box (10,20,30,60) in a 100x200 image -> center (20,40), size (20,40)
    line = pl.to_yolo_line((10, 20, 30, 60), 100, 200)
    assert line == "0 0.200000 0.200000 0.200000 0.200000"

def test_to_yolo_line_respects_class_id():
    line = pl.to_yolo_line((0, 0, 50, 100), 100, 100, cls=3)
    assert line.startswith("3 ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prelabel_dragons.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prelabel_dragons'`.

- [ ] **Step 3: Write minimal implementation**

```python
# prelabel_dragons.py
"""Bootstrap YOLO labels from the existing motion detector. Reads the captured
frame sequence, builds ONE background plate over all frames, and writes each
moving blob as a rough dragon box. These labels are deliberately noisy — open
them in LabelImg and CORRECT (drag/delete) rather than draw from scratch.

Usage:
    python prelabel_dragons.py [images_dir] [labels_dir]
      images_dir : default datasets/dragons/images
      labels_dir : default datasets/dragons/labels
"""
import os, sys, glob
import cv2
import monsters


def to_yolo_line(box, img_w, img_h, cls=0):
    x1, y1, x2, y2 = box[:4]
    cx = ((x1 + x2) / 2.0) / img_w
    cy = ((y1 + y2) / 2.0) / img_h
    w = (x2 - x1) / float(img_w)
    h = (y2 - y1) / float(img_h)
    return f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def main():
    images_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join("datasets", "dragons", "images")
    labels_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join("datasets", "dragons", "labels")
    os.makedirs(labels_dir, exist_ok=True)

    paths = sorted(glob.glob(os.path.join(images_dir, "*.png")))
    if not paths:
        print(f"[prelabel] no PNGs in {images_dir}"); return
    frames = [cv2.imread(p, cv2.IMREAD_COLOR) for p in paths]
    frames = [f for f in frames if f is not None]
    bg = monsters.background_plate(frames)
    roi = monsters.DEFAULT_MOTION_ROI

    total = 0
    for p, f in zip(paths, frames):
        h, w = f.shape[:2]
        _area, boxes = monsters.foreground_area(f, bg, roi)
        stem = os.path.splitext(os.path.basename(p))[0]
        with open(os.path.join(labels_dir, stem + ".txt"), "w", encoding="utf-8") as out:
            for b in boxes:
                out.write(to_yolo_line(b, w, h) + "\n")
        total += len(boxes)
    print(f"[prelabel] wrote labels for {len(paths)} frames, {total} rough boxes -> {labels_dir}/")
    print("[prelabel] now CORRECT them: labelImg", images_dir, os.path.join("datasets", "dragons", "classes.txt"))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_prelabel_dragons.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prelabel_dragons.py tests/test_prelabel_dragons.py
git commit -m "feat: prelabel_dragons.py — motion-blob bootstrap of YOLO labels"
```

---

### Task 4: YOLO inference + ROI filter (`detect_dragons_yolo`)

**Files:**
- Modify: `monsters.py` (append new section; do not touch existing functions)
- Test: `tests/test_monsters.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks (model handle is passed in by the caller).
- Produces:
  - `DRAGON_MODEL_PATH = os.path.join("models", "dragon_yolo.pt")`
  - `box_center_in_roi(box, roi) -> bool`
  - `run_yolo(model, frame, conf=0.35) -> list[tuple[int,int,int,int,float]]` (raw boxes `(x1,y1,x2,y2,score)`; thin ultralytics wrapper, not unit-tested directly)
  - `detect_dragons_yolo(frame, model, roi, conf=0.35) -> list[tuple]` (boxes whose center is inside `roi`)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_monsters.py
def test_box_center_in_roi_inside_and_outside():
    assert monsters.box_center_in_roi((0, 0, 10, 10), (0, 0, 50, 50)) is True
    assert monsters.box_center_in_roi((100, 100, 110, 110), (0, 0, 50, 50)) is False

def test_detect_dragons_yolo_keeps_only_in_band(monkeypatch):
    raw = [(0, 0, 10, 10, 0.9), (100, 100, 110, 110, 0.8)]
    monkeypatch.setattr(monsters, "run_yolo", lambda model, frame, conf=0.35: raw)
    out = monsters.detect_dragons_yolo(frame=object(), model=object(), roi=(0, 0, 50, 50))
    assert out == [(0, 0, 10, 10, 0.9)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_monsters.py -k "in_roi or in_band" -v`
Expected: FAIL with `AttributeError: module 'monsters' has no attribute 'box_center_in_roi'`.

- [ ] **Step 3: Write minimal implementation**

Append to `monsters.py` (after the motion section; keep the `if __name__` block last — insert this above it):

```python
# ---------------------------------------------------------------------------
# YOLO detection (primary once a model exists). Boxes -> count now, positions
# later. ultralytics/torch are imported LAZILY so offline tests never need them.
# ---------------------------------------------------------------------------
DRAGON_MODEL_PATH = os.path.join("models", "dragon_yolo.pt")

def box_center_in_roi(box, roi):
    x1, y1, x2, y2 = box[:4]
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx0, ry0, rx1, ry1 = roi
    return rx0 <= cx <= rx1 and ry0 <= cy <= ry1

def run_yolo(model, frame, conf=0.35):
    """Raw dragon boxes from the YOLO model: [(x1,y1,x2,y2,score), ...]."""
    res = model.predict(frame, conf=conf, verbose=False)[0]
    out = []
    for b in res.boxes:
        x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
        out.append((x1, y1, x2, y2, float(b.conf[0])))
    return out

def detect_dragons_yolo(frame, model, roi, conf=0.35):
    """YOLO boxes whose CENTER falls inside `roi` — the center filter replaces the
    old ROI mask and drops the other platform's dragons that leak into frame."""
    return [b for b in run_yolo(model, frame, conf) if box_center_in_roi(b, roi)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_monsters.py -k "in_roi or in_band" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monsters.py tests/test_monsters.py
git commit -m "feat: detect_dragons_yolo + ROI-center filter"
```

---

### Task 5: Frame-median box count (`count_dragons_yolo`)

**Files:**
- Modify: `monsters.py` (append)
- Test: `tests/test_monsters.py` (append)

**Interfaces:**
- Consumes: `detect_dragons_yolo` (Task 4).
- Produces: `count_dragons_yolo(capture_fn, model, roi, samples=3, interval=0.06, conf=0.35) -> int` (median box count over `samples` frames; `0` if no frames captured). Same `int` return contract as `count_dragons_motion`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_monsters.py
def test_count_dragons_yolo_is_median_of_box_counts(monkeypatch):
    # per-frame detections of length 3, 3, 0 -> sorted [0,3,3] -> median 3
    seq = iter([[(0,0,1,1,0.9)]*3, [(0,0,1,1,0.9)]*3, []])
    monkeypatch.setattr(monsters, "detect_dragons_yolo", lambda *a, **k: next(seq))
    grabbed = {"n": 0}
    def fake_capture():
        grabbed["n"] += 1
        return object()
    n = monsters.count_dragons_yolo(fake_capture, model=object(), roi=(0,0,10,10),
                                    samples=3, interval=0)
    assert n == 3 and grabbed["n"] == 3

def test_count_dragons_yolo_zero_when_no_frames():
    n = monsters.count_dragons_yolo(lambda: None, model=object(), roi=(0,0,10,10),
                                    samples=3, interval=0)
    assert n == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_monsters.py -k count_dragons_yolo -v`
Expected: FAIL with `AttributeError: module 'monsters' has no attribute 'count_dragons_yolo'`.

- [ ] **Step 3: Write minimal implementation**

Append to `monsters.py` (below `detect_dragons_yolo`):

```python
def count_dragons_yolo(capture_fn, model, roi, samples=3, interval=0.06, conf=0.35):
    """Grab a few frames, count in-band dragon boxes per frame, return the median.
    Returns 0 if no frames could be captured."""
    counts = []
    for i in range(samples):
        f = capture_fn()
        if f is not None:
            counts.append(len(detect_dragons_yolo(f, model, roi, conf)))
        if i < samples - 1 and interval:
            time.sleep(interval)
    if not counts:
        return 0
    counts.sort()
    return counts[len(counts) // 2]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_monsters.py -k count_dragons_yolo -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add monsters.py tests/test_monsters.py
git commit -m "feat: count_dragons_yolo — median box count over frames"
```

---

### Task 6: Model loader + auto-dispatch with motion fallback

**Files:**
- Modify: `monsters.py` (append)
- Test: `tests/test_monsters.py` (append)

**Interfaces:**
- Consumes: `load_dragon_model`, `count_dragons_yolo` (Task 5), `count_dragons_motion` (existing), `MOTION_ROI_BY_NODE`/`DEFAULT_MOTION_ROI` (existing), `DRAGON_MODEL_PATH` (Task 4).
- Produces:
  - `load_dragon_model(path=DRAGON_MODEL_PATH) -> model|None` (lazy-imports ultralytics; returns `None` if the file is missing or import/load fails; caches per path)
  - `count_dragons_best(capture_fn, node, model_path=DRAGON_MODEL_PATH) -> int` — the single entry point `recovery.py` will call: YOLO when a model loads, else motion, using the per-node ROI band.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_monsters.py
def test_load_dragon_model_missing_file_returns_none():
    monsters._MODEL_CACHE.clear()
    assert monsters.load_dragon_model(path="does/not/exist.pt") is None

def test_count_dragons_best_falls_back_to_motion_when_no_model(monkeypatch):
    monkeypatch.setattr(monsters, "load_dragon_model", lambda *a, **k: None)
    monkeypatch.setattr(monsters, "count_dragons_motion", lambda cap, roi=None, **k: 42)
    assert monsters.count_dragons_best(lambda: None, "TOP_FARM") == 42

def test_count_dragons_best_uses_yolo_when_model_present(monkeypatch):
    monkeypatch.setattr(monsters, "load_dragon_model", lambda *a, **k: object())
    monkeypatch.setattr(monsters, "count_dragons_yolo", lambda cap, model, roi, **k: 7)
    assert monsters.count_dragons_best(lambda: None, "BOTTOM_FARM") == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_monsters.py -k "dragon_model or count_dragons_best" -v`
Expected: FAIL with `AttributeError: module 'monsters' has no attribute '_MODEL_CACHE'` (or `load_dragon_model`).

- [ ] **Step 3: Write minimal implementation**

Append to `monsters.py` (below `count_dragons_yolo`):

```python
_MODEL_CACHE = {}

def load_dragon_model(path=DRAGON_MODEL_PATH):
    """Lazy-load the YOLO model once and cache it. Returns None (never raises) if
    the weights are missing or ultralytics/torch cannot load them, so callers can
    fall back to the motion counter."""
    if path in _MODEL_CACHE:
        return _MODEL_CACHE[path]
    model = None
    if os.path.exists(path):
        try:
            from ultralytics import YOLO       # lazy: keeps offline import light
            model = YOLO(path)
        except Exception as e:
            print(f"[yolo] could not load {path}: {e}")
            model = None
    else:
        print(f"[yolo] model {path} not found -> motion fallback")
    _MODEL_CACHE[path] = model
    return model

def count_dragons_best(capture_fn, node, model_path=DRAGON_MODEL_PATH):
    """Preferred dragon count: YOLO when a model is available, else the motion
    counter. Uses the per-node ROI band either way."""
    roi = MOTION_ROI_BY_NODE.get(node, DEFAULT_MOTION_ROI)
    model = load_dragon_model(model_path)
    if model is None:
        return count_dragons_motion(capture_fn, roi=roi)
    return count_dragons_yolo(capture_fn, model, roi)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_monsters.py -k "dragon_model or count_dragons_best" -v`
Expected: PASS (all three).

- [ ] **Step 5: Run the full monsters suite (no regressions)**

Run: `pytest tests/test_monsters.py -v`
Expected: all pass (existing motion/template tests untouched).

- [ ] **Step 6: Commit**

```bash
git add monsters.py tests/test_monsters.py
git commit -m "feat: load_dragon_model + count_dragons_best (YOLO w/ motion fallback)"
```

---

### Task 7: Wire the farming loop to the dispatcher

**Files:**
- Modify: `recovery.py:1040-1041`

**Interfaces:**
- Consumes: `monsters.count_dragons_best` (Task 6).
- Produces: no new symbols — the live loop now counts via the dispatcher. Behavior is unchanged until a model exists (motion fallback), so this lands safely before Task 8.

- [ ] **Step 1: Replace the count call**

In `recovery.py`, replace:

```python
        n = monsters.count_dragons_motion(
            capture, roi=monsters.MOTION_ROI_BY_NODE.get(node, monsters.DEFAULT_MOTION_ROI))
```

with:

```python
        n = monsters.count_dragons_best(capture, node)
```

- [ ] **Step 2: Verify recovery still imports and the symbol resolves**

Run: `python -c "import recovery, monsters; assert hasattr(monsters, 'count_dragons_best'); print('ok')"`
Expected: prints `ok` (no import error).

- [ ] **Step 3: Run the recovery test suite (no regressions)**

Run: `pytest tests/test_recovery.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add recovery.py
git commit -m "feat: farming loop counts dragons via count_dragons_best (YOLO+fallback)"
```

---

### Task 8: Training script (`train_dragons.py`) + produce the model

**Files:**
- Create: `train_dragons.py`
- Test: `tests/test_train_dragons.py`

**Interfaces:**
- Consumes: labeled `datasets/dragons/{images,labels}/`, `classes.txt` (Task 1), corrected labels (manual, from Task 3 output).
- Produces: `assign_split(paths, val_every=5) -> (train, val)` (deterministic 80/20 split) plus a `main()` that writes `train.txt`/`val.txt`/`dragons.yaml` and trains YOLO11n to `models/dragon_yolo.pt`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_train_dragons.py
import train_dragons as td

def test_assign_split_is_deterministic_80_20():
    paths = [f"img_{i}.png" for i in range(10)]
    train, val = td.assign_split(paths, val_every=5)
    # sorted indices 0 and 5 go to val -> 2 val, 8 train, no overlap
    assert len(val) == 2 and len(train) == 8
    assert set(train).isdisjoint(val)
    assert sorted(train + val) == sorted(paths)

def test_assign_split_val_every_controls_ratio():
    paths = [f"img_{i}.png" for i in range(20)]
    _, val = td.assign_split(paths, val_every=4)
    assert len(val) == 5   # indices 0,4,8,12,16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_train_dragons.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'train_dragons'`.

- [ ] **Step 3: Write minimal implementation**

```python
# train_dragons.py
"""Train YOLO11n on the corrected dragon dataset. Writes deterministic train/val
list files + dragons.yaml, then trains to models/dragon_yolo.pt.

Usage:
    python train_dragons.py [epochs]     # default 90
Prereqs: datasets/dragons/images/*.png with corrected datasets/dragons/labels/*.txt
"""
import os, sys, glob

DATA_ROOT = os.path.join("datasets", "dragons")

def assign_split(paths, val_every=5):
    """Deterministic split: every `val_every`-th sorted path -> val, rest -> train.
    val_every=5 gives ~20% val."""
    train, val = [], []
    for i, p in enumerate(sorted(paths)):
        (val if i % val_every == 0 else train).append(p)
    return train, val

def _write_list(path, items):
    with open(path, "w", encoding="utf-8") as f:
        for p in items:
            f.write(os.path.abspath(p) + "\n")

def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    images = sorted(glob.glob(os.path.join(DATA_ROOT, "images", "*.png")))
    if not images:
        print(f"[train] no images in {DATA_ROOT}/images"); return
    train, val = assign_split(images)
    train_txt = os.path.join(DATA_ROOT, "train.txt")
    val_txt = os.path.join(DATA_ROOT, "val.txt")
    _write_list(train_txt, train)
    _write_list(val_txt, val)

    yaml_path = os.path.join(DATA_ROOT, "dragons.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"train: {os.path.abspath(train_txt)}\n")
        f.write(f"val: {os.path.abspath(val_txt)}\n")
        f.write("names:\n  0: dragon\n")
    print(f"[train] {len(train)} train / {len(val)} val -> {yaml_path}")

    from ultralytics import YOLO               # lazy import
    model = YOLO("yolo11n.pt")                  # pretrained nano, auto-downloads
    model.train(data=yaml_path, epochs=epochs, imgsz=960, batch=-1,
                device=0, fliplr=0.5, mosaic=1.0, name="dragon_yolo")
    best = os.path.join("runs", "detect", "dragon_yolo", "weights", "best.pt")
    os.makedirs("models", exist_ok=True)
    import shutil
    shutil.copy(best, os.path.join("models", "dragon_yolo.pt"))
    print(f"[train] copied {best} -> models/dragon_yolo.pt")
    print("[train] review runs/detect/dragon_yolo/results.png before trusting it")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_train_dragons.py -v`
Expected: PASS (both). The `import ultralytics` lives inside `main()`, so these tests do not need the GPU stack.

- [ ] **Step 5: Commit the script**

```bash
git add train_dragons.py tests/test_train_dragons.py
git commit -m "feat: train_dragons.py — YOLO11n training + deterministic split"
```

- [ ] **Step 6: MANUAL — collect, label, train (produces the model)**

This step is human-driven and has no automated test. In order:

```bash
# 1. Farm and collect ~200-400 clean frames (F8 to stop)
python capture_frames.py
# 2. Bootstrap rough labels from the motion detector
python prelabel_dragons.py
# 3. Correct the boxes (draw/delete so ONLY dragons are boxed)
labelImg datasets/dragons/images datasets/dragons/classes.txt
# 4. Train (writes models/dragon_yolo.pt)
python train_dragons.py
```
Expected: `models/dragon_yolo.pt` exists and `runs/detect/dragon_yolo/results.png` shows mAP climbing. Once the file exists, `count_dragons_best` (already wired in Task 7) auto-switches from motion to YOLO — no code change needed.

- [ ] **Step 7: MANUAL — live acceptance check**

Spot-check detection on live frames and confirm the over-count is gone:

```bash
python -c "import recovery as R, monsters; R.focus(); print('dragons:', monsters.count_dragons_best(R.capture, 'TOP_FARM'))"
```
Expected: the printed count tracks what's actually on the top platform (not inflated). Repeat for `BOTTOM_FARM`. If a platform's band is mistuned, adjust `monsters.MOTION_ROI_BY_NODE[<node>]`.

---

## Notes for the implementer

- **Do not** add `import ultralytics`/`import torch` at the top of `monsters.py` or `recovery.py`. Only inside `load_dragon_model`, `run_yolo` (via the model call), and `train_dragons.main`.
- Tasks 1–7 are fully implementable and testable **without** a trained model — the loop runs on the motion fallback until Task 8 Step 6 produces `models/dragon_yolo.pt`.
- `labelImg` is installed with `pip install labelImg` if not present; any YOLO-format labeler works.
