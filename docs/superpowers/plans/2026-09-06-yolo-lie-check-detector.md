# YOLO Lie-Check Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a YOLO11n detector for the three lie-check screens (monster/curse/transparent-shape) that runs alongside the existing template checks and OR's into the same alarm, so a missed monster popup can't slip through.

**Architecture:** Four independent units — an inference wrapper that no-ops when the model file is absent (zero regression), integration that OR's YOLO detections into `lie_check_full_tick`, an offline data builder (auto-boxes template-detectable frames + a small manifest + synthetic compositing for the data-poor monster/curse classes), and a trainer adapted from `train_dragons.py`. Then train and tune the confidence threshold recall-first.

**Tech Stack:** Python, OpenCV (`cv2`), Ultralytics YOLO11n, pytest. Mirrors the existing `mob_detect.py` / `train_dragons.py` patterns.

## Global Constraints

- Models live at `models/*.pt`, **gitignored/local** (like `dragon_yolo.pt`); the model must never be committed.
- The detector is **additive & optional**: with no `models/lie_check_yolo.pt`, every entry point returns `[]` and behavior equals today's template-only path.
- Inference must **never raise** into the safety loop — wrap and return `[]` on any error.
- Classes (fixed order): `0: monster_check`, `1: curse`, `2: transparent_shape`.
- Recall-first: a miss = ban risk; a false alarm = a beep. Tune thresholds as low as the negative frames allow.
- Follow existing patterns: cached `YOLO(path)` load (`mob_detect.load_yolo`), `model.predict(img, imgsz=, conf=, verbose=False)[0]`.

---

### Task 1: Inference wrapper — `lie_check_yolo.py`

**Files:**
- Create: `lie_check_yolo.py`
- Test: `tests/test_lie_check_yolo.py`

**Interfaces:**
- Produces: `detect_lie_check_yolo(frame, conf=0.35, model_path="models/lie_check_yolo.pt", imgsz=1280) -> list[tuple[str, float, tuple[float,float,float,float]]]` — `(class_name, confidence, (x0,y0,x1,y1))`, `[]` when model absent / frame bad / predict fails. `_CLASS_NAMES` dict `{0:"monster_check",1:"curse",2:"transparent_shape"}`. `_model_cache` dict for lazy caching. `reset_cache()` for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lie_check_yolo.py
import numpy as np
import lie_check_yolo as ly


def test_returns_empty_when_model_absent(tmp_path):
    ly.reset_cache()
    frame = np.zeros((100, 100, 3), np.uint8)
    assert ly.detect_lie_check_yolo(frame, model_path=str(tmp_path / "nope.pt")) == []


def test_returns_empty_on_bad_frame():
    ly.reset_cache()
    assert ly.detect_lie_check_yolo(None) == []


def test_parses_model_output(monkeypatch):
    ly.reset_cache()

    class _B:
        def __init__(self, cls, conf, xyxy):
            self.cls = [cls]; self.conf = [conf]; self.xyxy = [xyxy]

    class _R:
        boxes = [_B(0, 0.9, (10.0, 20.0, 110.0, 80.0))]

    class _M:
        def predict(self, *a, **k): return [_R()]

    monkeypatch.setattr(ly, "_load", lambda path=ly._MODEL_PATH: _M())
    frame = np.zeros((100, 100, 3), np.uint8)
    out = ly.detect_lie_check_yolo(frame)
    assert out == [("monster_check", 0.9, (10.0, 20.0, 110.0, 80.0))]


def test_returns_empty_on_predict_exception(monkeypatch):
    ly.reset_cache()

    class _M:
        def predict(self, *a, **k): raise RuntimeError("boom")

    monkeypatch.setattr(ly, "_load", lambda path=ly._MODEL_PATH: _M())
    assert ly.detect_lie_check_yolo(np.zeros((10, 10, 3), np.uint8)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lie_check_yolo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lie_check_yolo'`

- [ ] **Step 3: Write minimal implementation**

```python
# lie_check_yolo.py
"""YOLO detector for the lie-check screens (monster / curse / transparent-shape).

Additive backstop to the template checks in detection.detect_lie_check: returns [] when
models/lie_check_yolo.pt is absent, so with no model the system behaves exactly as the
template-only path. Never raises -- it rides the safety loop."""
import os

_MODEL_PATH = os.path.join("models", "lie_check_yolo.pt")
_CLASS_NAMES = {0: "monster_check", 1: "curse", 2: "transparent_shape"}
_model_cache = {}          # path -> model, or False for known-absent/failed


def reset_cache():
    _model_cache.clear()


def _load(path=_MODEL_PATH):
    if path in _model_cache:
        return _model_cache[path]
    if not os.path.exists(path):
        _model_cache[path] = False
        return False
    try:
        from ultralytics import YOLO
        m = YOLO(path)
    except Exception as e:
        print(f"[lie-yolo] load failed: {e}")
        m = False
    _model_cache[path] = m
    return m


def detect_lie_check_yolo(frame, conf=0.35, model_path=_MODEL_PATH, imgsz=1280):
    """[(class_name, conf, (x0,y0,x1,y1)), ...] for lie-check popups, or [] if the model is
    absent / the frame is bad / inference fails. Never raises."""
    if frame is None or not hasattr(frame, "shape"):
        return []
    m = _load(model_path)
    if not m:
        return []
    try:
        r = m.predict(frame, imgsz=imgsz, conf=conf, verbose=False)[0]
    except Exception as e:
        print(f"[lie-yolo] predict failed: {e}")
        return []
    out = []
    for b in r.boxes:
        cls = int(b.cls[0]); c = float(b.conf[0])
        x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
        out.append((_CLASS_NAMES.get(cls, str(cls)), c, (x0, y0, x1, y1)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_lie_check_yolo.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add lie_check_yolo.py tests/test_lie_check_yolo.py
git commit -m "feat(lie-yolo): inference wrapper (no-op when model absent)"
```

---

### Task 2: Integration — OR YOLO into `lie_check_full_tick`

**Files:**
- Modify: `recovery.py` (imports near the top with the other `from detection import ...`; body of `lie_check_full_tick`)
- Test: `tests/test_status_watch.py` (add one test)

**Interfaces:**
- Consumes: `detect_lie_check_yolo` from Task 1.
- Produces: `lie_check_full_tick` fires `_full_alert` when EITHER templates OR YOLO detect. YOLO hits appear in the alarm as `("yolo:<class>", conf)`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_status_watch.py
def test_full_tick_alarms_on_yolo_only(monkeypatch):
    frame = np.zeros((10, 10, 3), np.uint8)
    monkeypatch.setattr(recovery, "capture", lambda: frame)
    monkeypatch.setattr(recovery, "_lie_enabled", True)
    monkeypatch.setattr(recovery, "is_lie_check_active", lambda: False)
    monkeypatch.setattr(recovery, "detect_lie_check", lambda *a, **k: [])   # templates: nothing
    monkeypatch.setattr(recovery, "detect_lie_check_yolo",
                        lambda f, **k: [("monster_check", 0.9, (0, 0, 5, 5))])
    monkeypatch.setattr(recovery, "dump_frame", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(recovery.notify, "send", lambda *a, **k: sent.append(a))
    recovery._full_alert.update(False); recovery._full_alert.update(False)   # reset cleared
    recovery._last_full_tick[0] = 0.0
    monkeypatch.setattr(recovery.time, "time", lambda: 200000.0)
    recovery.lie_check_full_tick()   # 1st hit
    recovery._last_full_tick[0] = 0.0
    recovery.lie_check_full_tick()   # 2nd consecutive -> alarm
    assert recovery._full_alert.alarm.active is True
    assert any("monster_check" in str(a) for a in sent)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_status_watch.py::test_full_tick_alarms_on_yolo_only -q`
Expected: FAIL — `AttributeError: module 'recovery' has no attribute 'detect_lie_check_yolo'`

- [ ] **Step 3: Add the import**

In `recovery.py`, next to the existing `from detection import ... detect_lie_check`, add:

```python
from lie_check_yolo import detect_lie_check_yolo
```

- [ ] **Step 4: Merge YOLO hits in `lie_check_full_tick`**

Replace the detection + alarm block. Find:

```python
    scores = {}
    hits = detect_lie_check(f, templates_folder=_LIE_DIR, template_filter=_FULL_TEMPLATES,
                            work_width=1000, scores=scores)
    if _full_alert.update(bool(hits)) and hits:
```

Replace with:

```python
    scores = {}
    hits = detect_lie_check(f, templates_folder=_LIE_DIR, template_filter=_FULL_TEMPLATES,
                            work_width=1000, scores=scores)
    yolo = detect_lie_check_yolo(f)                       # [] when the model file is absent
    hits = hits + [(f"yolo:{cls}", cf) for cls, cf, _ in yolo]
    if _full_alert.update(bool(hits)) and hits:
```

(The existing `_hit_tag(hits)` handles the `yolo:` names — `.replace('.png','')` is a no-op on them.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_status_watch.py -q`
Expected: PASS (both the new test and `test_full_tick_dumps_near_miss_and_throttles`)

- [ ] **Step 6: Commit**

```bash
git add recovery.py tests/test_status_watch.py
git commit -m "feat(lie-yolo): OR YOLO detections into the full-tick alarm"
```

---

### Task 3: Data builder — `make_lie_check_data.py`

**Files:**
- Create: `make_lie_check_data.py`
- Create: `datasets/lie_check_yolo/boxes.json` (manifest, checked in)
- Test: `tests/test_make_lie_check_data.py`

**Interfaces:**
- Produces:
  - `locate_popup(frame, template_name, templates_folder="assets/lie_check/") -> tuple[int,int,int,int] | None` — pixel box `(x0,y0,x1,y1)` of the best template match (for auto-boxing template-detectable frames).
  - `to_yolo_label(box, img_w, img_h, cls) -> str` — `"cls cx cy w h\n"` normalized.
  - `composite(bg, popup_crop, alpha, pos, scale) -> (image, box)` — alpha-blend `popup_crop` onto `bg`; returns the new image and the paste box in pixels.
  - `build(out_root="datasets/lie_check_yolo", n_synth=200)` — writes `images/*.png` + `labels/*.txt`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_make_lie_check_data.py
import numpy as np
import make_lie_check_data as mk


def test_to_yolo_label_normalizes():
    # box (10,20)-(110,80) in a 200x100 image -> center (60,50), size (100,60)
    assert mk.to_yolo_label((10, 20, 110, 80), 200, 100, 0) == "0 0.300000 0.500000 0.500000 0.600000\n"


def test_composite_pastes_in_bounds_and_reports_box():
    bg = np.zeros((100, 200, 3), np.uint8)
    popup = np.full((20, 40, 3), 255, np.uint8)
    img, box = mk.composite(bg, popup, alpha=0.8, pos=(50, 30), scale=1.0)
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= img.shape[1] and 0 <= y0 < y1 <= img.shape[0]
    assert img[y0 + 1, x0 + 1].max() > 0            # something was blended in


def test_locate_popup_finds_template(tmp_path):
    # a frame that IS one of our real transparent captures should locate a box inside it
    import glob, cv2
    caps = glob.glob("datasets/lie_check/captures/*transparent_title*.png")
    if not caps:
        return
    frame = cv2.imread(caps[0])
    box = mk.locate_popup(frame, "transparent_title.png")
    assert box is not None
    x0, y0, x1, y1 = box
    assert x1 > x0 and y1 > y0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_make_lie_check_data.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'make_lie_check_data'`

- [ ] **Step 3: Write the implementation**

```python
# make_lie_check_data.py
"""Build the YOLO lie-check dataset: datasets/lie_check_yolo/{images,labels}.

Positives come from (a) real frames whose popup we can AUTO-LOCATE by re-running the
template that already detects them (all transparent captures, curse frames), (b) a small
checked-in manifest boxes.json for frames templates can't locate (the monster popups and
synthetic bases), and (c) SYNTHETIC composites of a popup crop alpha-blended onto
popup-free backgrounds for the data-poor monster/curse classes. Run:

    python make_lie_check_data.py [n_synth]
"""
import os, sys, glob, json, random
import cv2
import numpy as np
from detection import LIE_CHECK_FRACTIONS

CLASSES = {"monster_check": 0, "curse": 1, "transparent_shape": 2}
_TPL_CLASS = {                       # which template locates which class
    "transparent_title.png": "transparent_shape",
    "curse_banner.png": "curse", "curse_lock.png": "curse",
    "monster_instr_box.png": "monster_check", "monster_warn_box.png": "monster_check",
    "monster_instr_temple.png": "monster_check", "monster_warn_temple.png": "monster_check",
}


def locate_popup(frame, template_name, templates_folder="assets/lie_check/"):
    """Best-match pixel box of template_name in frame (fraction-scaled like detect_lie_check),
    padded to the popup panel, or None."""
    tpl = cv2.imread(os.path.join(templates_folder, template_name))
    if tpl is None:
        return None
    ih, iw = frame.shape[:2]
    frac = LIE_CHECK_FRACTIONS.get(template_name)
    if frac is None:
        return None
    h0, w0 = tpl.shape[:2]
    bw = max(1, int(frac * iw)); bh = max(1, int(bw * h0 / w0))
    best = None
    for s in [round(0.85 + i * 0.05, 3) for i in range(8)]:
        tw, th = int(bw * s), int(bh * s)
        if tw < 8 or th < 8 or tw > iw or th > ih:
            continue
        scaled = cv2.resize(tpl, (tw, th))
        res = cv2.matchTemplate(frame, scaled, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if best is None or mv > best[0]:
            best = (mv, ml[0], ml[1], tw, th)
    if best is None:
        return None
    _, x, y, tw, th = best
    # pad the located text strip out to a rough popup panel (2x wider, 6x taller, clamped)
    px, py = int(tw * 0.5), int(th * 2.5)
    x0 = max(0, x - px); y0 = max(0, y - py)
    x1 = min(iw, x + tw + px); y1 = min(ih, y + th + py * 2)
    return (x0, y0, x1, y1)


def to_yolo_label(box, img_w, img_h, cls):
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2 / img_w; cy = (y0 + y1) / 2 / img_h
    w = (x1 - x0) / img_w; h = (y1 - y0) / img_h
    return f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n"


def composite(bg, popup_crop, alpha, pos, scale):
    """Alpha-blend a (possibly scaled) popup_crop onto a copy of bg at top-left `pos`.
    Returns (image, (x0,y0,x1,y1)). Clamps the crop to fit."""
    img = bg.copy()
    ph, pw = popup_crop.shape[:2]
    tw, th = max(1, int(pw * scale)), max(1, int(ph * scale))
    crop = cv2.resize(popup_crop, (tw, th))
    x0, y0 = pos
    x0 = max(0, min(x0, img.shape[1] - 1)); y0 = max(0, min(y0, img.shape[0] - 1))
    x1 = min(img.shape[1], x0 + tw); y1 = min(img.shape[0], y0 + th)
    crop = crop[: y1 - y0, : x1 - x0]
    roi = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (roi * (1 - alpha) + crop.astype(np.float32) * alpha).astype(np.uint8)
    return img, (x0, y0, x1, y1)


def _write(out_root, name, img, label_line):
    cv2.imwrite(os.path.join(out_root, "images", name + ".png"), img)
    with open(os.path.join(out_root, "labels", name + ".txt"), "w") as f:
        f.write(label_line)


def build(out_root="datasets/lie_check_yolo", n_synth=200, seed=0):
    random.seed(seed)
    os.makedirs(os.path.join(out_root, "images"), exist_ok=True)
    os.makedirs(os.path.join(out_root, "labels"), exist_ok=True)
    manifest_path = os.path.join(out_root, "boxes.json")
    manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else {}
    n = 0

    # (a) auto-boxed real frames: transparent captures + curse frames
    real = (glob.glob("datasets/lie_check/captures/*transparent_title*.png"))
    for p in real:
        frame = cv2.imread(p)
        if frame is None:
            continue
        box = locate_popup(frame, "transparent_title.png")
        if box is None:
            print(f"[data] skip (no locate): {p}"); continue
        cls = CLASSES["transparent_shape"]
        _write(out_root, f"real_{n:05d}", frame, to_yolo_label(box, frame.shape[1], frame.shape[0], cls))
        n += 1

    # (b) manifest frames (monster popups, curse, synthetic bases): filename -> [class, x0,y0,x1,y1]
    popup_crops = {"monster_check": [], "curse": []}
    for fname, entry in manifest.items():
        frame = cv2.imread(fname)
        if frame is None:
            print(f"[data] manifest file missing: {fname}"); continue
        cls_name, x0, y0, x1, y1 = entry[0], *entry[1:]
        cls = CLASSES[cls_name]
        _write(out_root, f"real_{n:05d}", frame, to_yolo_label((x0, y0, x1, y1), frame.shape[1], frame.shape[0], cls))
        n += 1
        if cls_name in popup_crops:
            popup_crops[cls_name].append(frame[y0:y1, x0:x1].copy())

    # (c) synthetic monster/curse: paste a popup crop onto popup-free backgrounds
    bgs = [cv2.imread(p) for p in (glob.glob("datasets/mobs_real/**/*.png", recursive=True)
           + glob.glob("debug_output/*.png") + ["datasets/lie_check/normal_test.png"])]
    bgs = [b for b in bgs if b is not None]
    if bgs:
        for cls_name, crops in popup_crops.items():
            if not crops:
                continue
            for _ in range(n_synth):
                bg = random.choice(bgs); crop = random.choice(crops)
                scale = random.uniform(0.8, 1.15); alpha = random.uniform(0.7, 0.95)
                pos = (random.randint(0, max(1, bg.shape[1] - crop.shape[1])),
                       random.randint(0, max(1, bg.shape[0] - crop.shape[0])))
                img, box = composite(bg, crop, alpha, pos, scale)
                _write(out_root, f"synth_{cls_name}_{n:05d}", img,
                       to_yolo_label(box, img.shape[1], img.shape[0], CLASSES[cls_name]))
                n += 1
    print(f"[data] wrote {n} labeled frames to {out_root}")
    return n


if __name__ == "__main__":
    build(n_synth=int(sys.argv[1]) if len(sys.argv) > 1 else 200)
```

- [ ] **Step 4: Create the manifest with the monster/curse boxes**

Create `datasets/lie_check_yolo/boxes.json`. Boxes are `[class, x0, y0, x1, y1]` in that file's pixels. Fill the monster boxes from the known template crops (the popup panel around them) and the curse frames. Example (adjust coordinates to the actual popup panel in each file):

```json
{
  "datasets/lie_check/monster_box_test.png":    ["monster_check", 648, 250, 1290, 780],
  "datasets/lie_check/monster_temple_test.png": ["monster_check", 780, 300, 1230, 800],
  "datasets/lie_check/rune_check_test.png":     ["curse", 0, 0, 0, 0],
  "datasets/lie_check/rune_check_live_test.png":["curse", 0, 0, 0, 0]
}
```

To get real curse boxes, run `locate_popup(frame, "curse_banner.png")` on each curse file and paste the returned coordinates (replace the `0,0,0,0` placeholders). Do NOT leave zero boxes in the committed manifest.

- [ ] **Step 5: Run tests + a smoke build**

Run: `python -m pytest tests/test_make_lie_check_data.py -q` → Expected: PASS
Run: `python make_lie_check_data.py 50` → Expected: prints `[data] wrote N labeled frames`, and `datasets/lie_check_yolo/images` / `labels` are populated. Spot-check 3 labels by drawing the box on the image.

- [ ] **Step 6: Ensure the dataset images are gitignored, commit the code + manifest**

Add to `.gitignore` if not already covered: `datasets/lie_check_yolo/images/` and `datasets/lie_check_yolo/labels/` (generated). Commit the builder + manifest only.

```bash
git add make_lie_check_data.py tests/test_make_lie_check_data.py datasets/lie_check_yolo/boxes.json .gitignore
git commit -m "feat(lie-yolo): dataset builder (auto-box + manifest + synthetic)"
```

---

### Task 4: Trainer — `train_lie_check.py`

**Files:**
- Create: `train_lie_check.py`

**Interfaces:**
- Consumes: `datasets/lie_check_yolo/{images,labels}` from Task 3.
- Produces: `models/lie_check_yolo.pt`.

- [ ] **Step 1: Write the trainer (adapt `train_dragons.py`)**

```python
# train_lie_check.py
"""Train YOLO11n on the lie-check dataset -> models/lie_check_yolo.pt.

Usage:  python train_lie_check.py [epochs]   # default 120
Prereqs: run `python make_lie_check_data.py` first (datasets/lie_check_yolo/{images,labels}).
"""
import os, sys, glob, shutil

DATA_ROOT = os.path.join("datasets", "lie_check_yolo")


def assign_split(paths, val_every=5):
    train, val = [], []
    for i, p in enumerate(sorted(paths)):
        (val if i % val_every == 0 else train).append(p)
    return train, val


def _write_list(path, items):
    with open(path, "w", encoding="utf-8") as f:
        for p in items:
            f.write(os.path.abspath(p) + "\n")


def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    # Keep synthetic frames out of val (they'd measure memorization, not generalization).
    real = sorted(glob.glob(os.path.join(DATA_ROOT, "images", "real_*.png")))
    synth = sorted(glob.glob(os.path.join(DATA_ROOT, "images", "synth_*.png")))
    if not real and not synth:
        print(f"[train] no images in {DATA_ROOT}/images -- run make_lie_check_data.py first"); return
    train_real, val = assign_split(real)
    train = train_real + synth
    _write_list(os.path.join(DATA_ROOT, "train.txt"), train)
    _write_list(os.path.join(DATA_ROOT, "val.txt"), val or train[:1])

    yaml_path = os.path.join(DATA_ROOT, "lie_check.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"train: {os.path.abspath(os.path.join(DATA_ROOT, 'train.txt'))}\n")
        f.write(f"val: {os.path.abspath(os.path.join(DATA_ROOT, 'val.txt'))}\n")
        f.write("names:\n  0: monster_check\n  1: curse\n  2: transparent_shape\n")

    from ultralytics import YOLO
    model = YOLO("yolo11n.pt")
    model.train(data=yaml_path, epochs=epochs, imgsz=1280, batch=8,
                project="runs_lie_check", name="train", exist_ok=True)
    best = os.path.join("runs_lie_check", "train", "weights", "best.pt")
    os.makedirs("models", exist_ok=True)
    shutil.copy(best, os.path.join("models", "lie_check_yolo.pt"))
    print("[train] wrote models/lie_check_yolo.pt")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the build + training (GPU, ~15-30 min)**

Run: `python make_lie_check_data.py 300`
Run: `python train_lie_check.py 120`
Expected: ends with `[train] wrote models/lie_check_yolo.pt`. Watch mAP for the `transparent_shape` (data-rich) and `monster_check`/`curse` (synthetic) classes.

- [ ] **Step 3: Ensure the model + run artifacts are gitignored, commit the trainer**

Confirm `.gitignore` covers `models/*.pt` and add `runs_lie_check/`. Commit the trainer only (never the `.pt`).

```bash
git add train_lie_check.py .gitignore
git commit -m "feat(lie-yolo): trainer -> models/lie_check_yolo.pt"
```

---

### Task 5: Tune confidence + validate

**Files:**
- Modify: `recovery.py` (set the `conf` passed to `detect_lie_check_yolo`, if not the default)

**Interfaces:**
- Consumes: trained `models/lie_check_yolo.pt`, `detect_lie_check_yolo` from Task 1.

- [ ] **Step 1: Measure YOLO confidence on positives vs the negative frames**

Write a throwaway script (`tmp_tune.py`, not committed) that runs `detect_lie_check_yolo` at `conf=0.05` on:
- positives: the real monster/curse frames + a sample of transparent captures,
- negatives: `datasets/lie_check/captures/*` (as non-target for a given class is fine), `normal_test.png`, `debug_output/*`.

Print, per class, the max confidence on negatives and the min on positives.

- [ ] **Step 2: Choose `conf`**

Pick the lowest `conf` that is above every negative's confidence (recall-first, but no alarm fatigue), mirroring how the template thresholds were set. If the default 0.35 already separates cleanly, keep it; otherwise pass an explicit `conf=` in the `detect_lie_check_yolo(f, conf=…)` call in `lie_check_full_tick`.

- [ ] **Step 3: Verify positives fire end-to-end**

Run a throwaway check feeding each real monster/curse frame through `lie_check_full_tick` (monkeypatch `capture` to return it, as in `test_full_tick_alarms_on_yolo_only`) and confirm the alarm fires with the model present.

- [ ] **Step 4: Run the full suite + commit any conf change**

Run: `python -m pytest tests/ -q` → Expected: all pass.

```bash
git add recovery.py
git commit -m "tune(lie-yolo): set recall-first YOLO confidence threshold"
```

- [ ] **Step 5: Update memory**

Append to `C:\Users\jerry\.claude\projects\C--jerry-toy-work-maple-auto-train\memory\lie-check-monster-detection.md`: the YOLO detector now runs OR'd with templates, model at `models/lie_check_yolo.pt` (gitignored), rebuild via `make_lie_check_data.py` + `train_lie_check.py`, retrain as near-miss dumps accumulate.

---

## Notes for the executor

- **Restart required:** the running bot/panel must be restarted to load the new code and model.
- **No model = no change:** Tasks 1–2 are safe to ship before any model exists; the detector no-ops until `models/lie_check_yolo.pt` is present.
- **The synthetic pipeline is the weak point.** If mAP on the held-out real monster/curse frames is poor, don't lower `conf` into the negative band — instead let the near-miss capture accumulate real frames and retrain (rerun Tasks 3–4). The templates (at 0.78) remain the backstop throughout.
