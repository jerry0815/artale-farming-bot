# YOLO Blue-Wing-Dragon Detection — Design

**Date:** 2026-08-12
**Status:** Approved, pending implementation plan

## Problem

The live farming loop needs to know how many blue-wing dragons are on the
current platform so it can decide when the platform is depleted and rotate to
the other one ([recovery.py:1040](../../../recovery.py)). Two detectors exist
today in [monsters.py](../../../monsters.py):

- **Template matching** (`detect_dragons`) — abandoned: false positives on the
  blue ice cave, and each reference sprite only matches its exact animation pose.
- **Motion subtraction** (`count_dragons_motion`, current primary) — builds a
  background plate from a burst of frames and counts moving-foreground area ÷
  `DRAGON_AREA`.

The motion method **over-counts**: background/arrow/other-player motion inflates
the foreground area, so the platform rarely reads "depleted" and rotation
stalls. Only a count is consumed today, but **per-dragon positions will be
needed in future** (aiming/targeting).

## Goal

Replace the count source with a lightweight, single-class YOLO detector that
returns per-dragon bounding boxes. `count = len(boxes)` today; box centers give
positions later. Keep the motion method as a fallback and as the pre-labeler.

## Scope

- **One class:** `dragon` (blue-wing). Direction-agnostic — a dragon facing
  either way is the same class; horizontal-flip augmentation covers pose.
- **Detect dragons only** — the character, other players, damage numbers, and
  skill/arrow effects must NOT get boxes. This is what removes the over-count.
- Non-goals: multi-monster classification, tracking across frames, retraining
  automation. Additive change — nothing existing is deleted.

## Environment

- GPU: NVIDIA RTX 4060, 8 GB VRAM, CUDA 13.2 driver. Ample for YOLO11n training
  (minutes) and single-frame inference (single-digit ms).
- New dependency: `ultralytics` (pulls a CUDA `torch`, ~2–3 GB). Accepted.
  `cv2`/`numpy` already present.

## Data flow

```
capture_frames.py   -> datasets/dragons/images/*.png   (farm live; ~8 fps grab)
prelabel_dragons.py -> datasets/dragons/labels/*.txt    (motion blobs -> rough YOLO boxes)
   [correct in LabelImg]  -> fixed *.txt
train_dragons.py    -> models/dragon_yolo.pt            (YOLO11n, GPU)
monsters.detect_dragons_yolo() -> recovery.py live loop
```

## Components

### 1. `capture_frames.py` (throwaway data-collection script)

- Loops `recovery.capture()` at ~8 fps, writes `frame_00001.png …` to
  `datasets/dragons/images/`. Stops on F8 (`kb.pause`).
- Frames are byte-identical to the live capture path (same resolution/colorspace)
  — chosen over recorded video specifically to avoid a train/inference mismatch.
- Operator runs it while manually farming across **both platforms** and varied
  dragon densities (0 / few / many / overlapping). Target ~200–400 frames;
  sub-sample ~200 spread-out frames for labeling. Variety > volume.

### 2. `prelabel_dragons.py` (throwaway bootstrap)

- Builds one background plate over the whole captured sequence, then per frame
  runs the existing `foreground_area` blob detector and writes each blob as a
  normalized YOLO box `0 cx cy w h` to `labels/*.txt`.
- Boxes are intentionally rough. The workflow is **correct, not draw** — the
  over-counts/merges here are exactly what the operator fixes.

### 3. Correction (LabelImg, local, offline)

- Provide `classes.txt` (`dragon`), YOLO-format folder layout, and a one-line
  launch command. Operator steps through frames, drags/deletes boxes so every
  dragon — and only dragons — has a tight box. ~1–2 hr manual step.

### 4. `train_dragons.py`

- `ultralytics` YOLO11n, `imgsz=960`, ~80–100 epochs, batch auto, `device=0`.
- Auto 80/20 train/val split. Augmentation includes horizontal flip + mosaic.
- Outputs `models/dragon_yolo.pt` and `results.png` (precision/recall/mAP) so
  quality is reviewed before the model is trusted. Retrain is a one-liner if
  more labeled frames are added later.

### 5. Live integration (`monsters.py` + `recovery.py`)

New functions in `monsters.py`; motion code left untouched as fallback:

- `load_dragon_model(path)` — lazy-load once, cache the handle.
- `detect_dragons_yolo(frame, model, roi, conf=0.35)` — returns
  `[(x1, y1, x2, y2, score), …]`, filtered so **only boxes whose center is
  inside the per-node ROI band are kept**. This replaces the ROI mask and is the
  mechanism that removes the other-platform over-count.
- `count_dragons_yolo(capture_fn, model, roi, samples=3, …)` — grabs ~3 frames,
  returns the **median** box count as an `int` (same return contract as
  `count_dragons_motion`).

[recovery.py:1040](../../../recovery.py) swaps `count_dragons_motion(...)` →
`count_dragons_yolo(...)`. Because boxes are available, the future "need
position" case is already covered (read box centers). **If `models/dragon_yolo.pt`
is missing, it falls back to `count_dragons_motion`** so the bot never
hard-breaks.

Per-node ROI bands (`MOTION_ROI_BY_NODE` / `DEFAULT_MOTION_ROI`) are reused as
the center-in-band filter for YOLO boxes.

## Error handling / fallback

- Missing model file → fall back to `count_dragons_motion`, log once.
- Motion method and template method remain in the codebase unchanged.
- If YOLO underperforms live, revert by flipping the single call back.

## Testing

- **Unit (offline, no GPU), in `tests/test_monsters.py` style:**
  - `detect_dragons_yolo` ROI-center filtering with a stubbed model returning
    known boxes (in-band kept, out-of-band dropped).
  - `count_dragons_yolo` median-of-frames with a fake capture fn.
  - Missing-model fallback path selects `count_dragons_motion`.
- **Acceptance (live):** run detection on a held-out farming stretch; confirm
  counts track reality, over-counting is gone, and rotate decisions match or
  beat the motion method.

## File layout

```
capture_frames.py            # new, throwaway
prelabel_dragons.py          # new, throwaway
train_dragons.py             # new
datasets/dragons/
  images/                    # captured frames (gitignored)
  labels/                    # YOLO txt labels
  classes.txt                # "dragon"
  dragons.yaml               # ultralytics dataset config
models/dragon_yolo.pt        # trained weights (gitignored)
monsters.py                  # + load_dragon_model / detect_dragons_yolo / count_dragons_yolo
recovery.py                  # swap count call (with fallback)
```

`datasets/dragons/images/` and `models/*.pt` are gitignored (large binaries).
