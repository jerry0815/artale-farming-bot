# YOLO Lie-Check Detector — Design

Date: 2026-09-06
Status: Approved (pending spec review)

## Purpose

Detect the three "needs a human" lie-check screens — **name-the-monster**, **curse**,
and **find-transparent-shape** — with a YOLO object detector, so recognition is robust
to map background, popup transparency, scale, and window size. Template matching keeps
missing the monster popup (semi-transparent text over a varying background scores in the
same band as normal farming frames), and a real miss got the account flagged in-game.

A miss = ban risk; a false alarm = only a beep (no auto-pause). So the whole design is
**recall-first**, and the detector is **additive**: it runs *alongside* the existing
template checks and either one firing raises the alarm.

## Non-goals

- Not replacing the template pipeline. Curse and transparent-shape already detect well
  on templates; those stay. YOLO is a second, independent detector OR'd in.
- Not changing the alarm/notify/pause behavior, the fast-tick transparent path, or the
  another-player check.

## Constraints (from the existing codebase)

- YOLO models live at `models/*.pt`, **gitignored/local** (see `dragon_yolo.pt`,
  `mob_yolo.pt`). Loaded via a cached `YOLO(path)` (mirror `mob_detect.load_yolo`),
  called as `model.predict(img, imgsz=, conf=, verbose=False)[0]`.
- Training scripts write `datasets/<name>/{images,labels}` + a `<name>.yaml` and train
  YOLO11n (see `train_dragons.py`).
- The full lie-check tick runs in the background safety-monitor thread at ~1.5 s,
  `work_width=1000`; the fast transparent tick runs inline. YOLO11n inference is
  ~10–30 ms — fine for the monitor thread.

## Architecture

Four units, each usable and testable on its own.

### 1. Data builder — `make_lie_check_data.py`

Produces `datasets/lie_check_yolo/{images,labels}/` in YOLO format. Three classes:

```
0: monster_check
1: curse
2: transparent_shape
```

Each label file holds one box: the popup region, normalized `cls cx cy w h`.

**Sources:**

- **Real frames** already on disk:
  - `transparent_shape`: 54+ `datasets/lie_check/captures/*_transparent_title*.png`.
  - `monster_check`: `datasets/lie_check/monster_box_test.png`,
    `monster_temple_test.png` (+ the OneDrive originals if present).
  - `curse`: `datasets/lie_check/rune_check_test.png`, `rune_check_live_test.png`,
    and any `*_full_auto_curse_*` capture.
  - **Future** `near_miss` / `full_auto` dumps auto-feed this on the next build.
- **Synthetic** for the data-poor classes (monster/curse): alpha-blend a popup-region
  crop onto popup-free backgrounds (normal frames / captures) at varied position,
  scale, and opacity; the paste rectangle *is* the label box. Bootstraps monster/curse
  until real frames accumulate.

Real positives are split into train/val deterministically (reuse `train_dragons.assign_split`
style, every 5th → val) so the held-out real frames measure real generalization, not
synthetic memorization. Synthetic images go to **train only**.

**Bounding boxes** come from two sources, to keep manual work small:

- **Auto-located** — for any frame a template *does* detect (all 54 transparent
  captures, and curse frames the curse templates hit), the builder runs the matching
  template, takes the match rectangle, and pads it to the popup panel. No hand-labeling
  for the data-rich transparent class.
- **Manifest** — `datasets/lie_check_yolo/boxes.json` (filename → `[class, x0,y0,x1,y1]`
  in that frame's pixels) for the handful of frames templates can't locate reliably
  (the monster popups, and any synthetic bases). A few entries, checked in, deterministic.

The builder warns and skips any real frame that gets neither an auto-box nor a manifest
entry, rather than mislabeling.

### 2. Trainer — `train_lie_check.py`

Adapts `train_dragons.py`: write `train.txt`/`val.txt`/`lie_check.yaml` (3 names),
train YOLO11n → `models/lie_check_yolo.pt`. `python train_lie_check.py [epochs]`.

### 3. Inference — `detect_lie_check_yolo(frame, conf=…)` in `lie_check_yolo.py`

- Lazy, cached model load from `models/lie_check_yolo.pt` (mirror `mob_detect`).
- **Returns `[]` if the model file is absent** (and caches that fact) — so with no
  trained model, the whole system behaves exactly as today. No regression is possible.
- Returns `[(class_name, conf, (x0,y0,x1,y1)), …]` above `conf`.
- Never raises into the safety loop (wrap predict in try/except → `[]` on error).

### 4. Integration — `recovery.py`

In `lie_check_full_tick`, after the template pass, also run
`detect_lie_check_yolo(f)`. Treat a YOLO detection of **any** class as a lie-check hit
and feed it into the **same** `_full_alert` OR the template `hits`, so the alarm/notify/
dump path is unchanged and shared. A YOLO-driven alarm names the class(es) it saw. The
near-miss dump also considers YOLO's top sub-threshold confidence.

`conf` threshold tuned recall-first against the negative frames (the 54 captures +
normal/debug frames) exactly as the template thresholds were: as low as possible while
zero of the known non-lie-check frames fire.

## Data flow

```
capture() ─▶ lie_check_full_tick (monitor thread, ~1.5s)
                ├─ detect_lie_check     (templates)   ─┐
                └─ detect_lie_check_yolo (model, all 3)─┴─▶ any hit ─▶ _full_alert
                                                                         └▶ alarm + notify + dump
```

## Error handling

- Model file missing or corrupt, or predict throws → `detect_lie_check_yolo` returns
  `[]`; templates carry the check. Logged once, not per tick.
- Data builder skips frames with no manifest box (warns) rather than mislabeling.

## Testing

- **Data builder:** synthetic composite lands a label box in-bounds and normalized;
  real-frame manifest boxes convert to valid YOLO labels; deterministic split.
- **Inference wrapper:** returns `[]` when `models/lie_check_yolo.pt` is absent;
  parses a stubbed YOLO result into `(class, conf, box)`; returns `[]` on a predict
  exception.
- **Integration:** a stubbed `detect_lie_check_yolo` hit drives `_full_alert` to alarm
  even when templates return `[]`; nothing fires when both return empty.
- **Validation (manual, not CI):** held-out real monster/curse frames are detected by
  the trained model above the chosen `conf`; the 54 negative frames stay clear.

## Rollout / risk

- Monster/curse have only 2–3 real frames, so the first model leans on synthetic
  compositing (imperfect for semi-transparent popups). Because the model is additive and
  optional, a weak first model cannot regress today's behavior — worst case it adds
  nothing until retrained.
- The near-miss capture (shipped in `f8eb9c6`) accumulates real frames continuously;
  `make_lie_check_data.py` + `train_lie_check.py` are built for cheap incremental
  retraining as data grows.

## Related

`[[lie-check-monster-detection]]` (memory), `2026-08-12-yolo-dragon-detection-design.md`
(YOLO pattern this follows).
