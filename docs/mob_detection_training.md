# Mob detection: training & improving the YOLO detector

How the water-map mob detector (`models/mob_yolo.pt`, classes **fishhouse**, **goby**, and
optionally **player**) is built and improved. Two phases:

1. **Bootstrap** — a synthetic dataset trains a first model with *zero* hand-labeling.
2. **Improve (hybrid)** — hand-label a few real frames (the model pre-labels them) and mix
   them in to kill false positives / recover missed mobs.

Rerun the improve loop any time detection drifts (new mob, new area, FPs on scenery).

---

## Files

| File | Role |
|------|------|
| `capture_mob_template.py` | grab tight LIVE mob crops (used as synthetic foregrounds) |
| `synth_data.py` | composite crops onto backgrounds → auto-labeled YOLO dataset |
| `train_mobs.py` | train YOLO11n (`--real` = hybrid), copies best → `models/mob_yolo.pt` |
| `label_mobs.py` | grab real frames + pre-label + multi-class browser labeler |
| `mob_detect.py` | inference used by the farming loop (`detector: "mob_yolo"`) |

Datasets: `datasets/mobs` (synthetic), `datasets/mobs_real` (hand-labeled real).
Both the model and datasets are gitignored (local only).

---

## Phase 1 — Bootstrap (first model, no hand-labeling)

### 1a. Capture live mob crops (foregrounds)
Stand where the mobs are on screen, tight-box each one:
```bash
python capture_mob_template.py deep_sea_2 fishhouse
python capture_mob_template.py deep_sea_2 goby
```
→ `assets/mob/deep_sea_2/<class>_<n>.png`. Aim for ~8–15 per class across poses. Goby
swims both ways, so include both facings (or let compositing flip them).

### 1b. Backgrounds — MUST be mob-free
Critical: backgrounds must NOT contain the mobs, or the model learns to *ignore* them.
Use frames from a **different** map (e.g. the dragon-nest clip):
```bash
python synth_data.py --extract "C:/path/to/dragon_nest_clip.mp4" assets/bg
```
(But water-map backgrounds ARE wanted as hard negatives if they're truly mob-free — that's
what teaches "coral is not a mob". In practice we relied on Phase 2 for that instead.)

### 1c. Generate the synthetic dataset
Composites crops onto backgrounds at random scale/flip/color-jitter with exact labels:
```bash
python synth_data.py --bg assets/bg --out datasets/mobs --n 3000 --live assets/mob/deep_sea_2
```
(`--live` uses the real crops; omit to use the green-screen KenYu sprites.)

### 1d. Train
```bash
python train_mobs.py 25 640     # quick sanity (~15 min) -- check it detects at all
python train_mobs.py 80 640     # full run (early-stops at patience 20)
```
Writes `models/mob_yolo.pt`. Verify on REAL frames (synthetic val mAP is not the real test):
preview in the panel (below) on actual gameplay.

---

## Phase 2 — Improve with hand-labeled real frames (hybrid)

This is the high-leverage loop. It fixes exactly what synthetic data misses (e.g. coral
false-positives, missed goby poses).

### 2a. Record a clip aimed at the FAILURES
30–60s, including the spots the model gets wrong:
- the scenery it false-positives on (coral / anemones),
- mob poses/angles it misses,
- mixed clusters (fishhouse + bone-fish stacked).

### 2b. Grab + pre-label
```bash
python label_mobs.py --grab "C:/path/to/clip.mp4" 40
```
Pulls ~40 frames and **pre-boxes them with the current model** (so you correct, not label
from scratch). `--grab-dir <folder>` imports existing PNGs; `--no-prelabel` starts blank.

### 2c. Correct in the browser
```bash
python label_mobs.py            # http://localhost:8001
```
- **drag** = new box in the active class
- **1** / **2** / **3** = active class fishhouse / goby / player (or, with a box selected, reclassify it)
- click box → **Del** = remove
- **← / →** = prev / next (auto-saves)

Mostly: delete FPs (coral), add missed mobs, fix wrong classes. Saved to
`datasets/mobs_real/{images,labels}`. ~40 frames ≈ 10–15 min.

### 2d. Retrain hybrid
```bash
python train_mobs.py 80 640 --real
```
`--real` writes `datasets/mobs/data_hybrid.yaml` (synthetic + real in both train and val)
and trains on both, then copies best → `models/mob_yolo.pt`.

### 2e. Verify on held-out real frames
Compare against earlier behavior on frames NOT in training (e.g. old video frames):
```bash
python - <<'PY'
from ultralytics import YOLO; import cv2
m=YOLO("models/mob_yolo.pt")
for src in ["frameA.png","frameB.png"]:
    r=m.predict(cv2.imread(src), imgsz=640, conf=0.4, verbose=False)[0]
    by=[(m.names[int(b.cls[0])], round(float(b.conf[0]),2)) for b in r.boxes]
    print(src, by)
PY
```
Result of the first pass (40 frames): coral goby-FPs went from 0.55–0.67 to **none even at
conf 0.4**, real goby held at 0.88–0.92.

---

## Optional — unified player anchor (class **player**)

The same YOLO can also locate the *player*, so ONE model finds both mobs and the character
(no separate name-tag / HP-bar anchor). Label the player's box **including the red HP bar
above the head** — that HP bar is the feature YOLO learns to track through attack VFX.

Because the HP bar is the anchor feature, **record the label clip with the HP bar visible
on-screen** (take a hit at the start so it shows). Then:

1. Grab + prelabel real frames as usual (`python label_mobs.py --grab "<clip>" 40`).
2. In the browser, press **3** and drag a box around the character **from the HP bar down
   to the feet**, one per frame. Fix any mob boxes too.
3. Retrain hybrid: `python train_mobs.py 80 640 --real` (now writes `nc: 3`,
   names `[fishhouse, goby, player]`).
4. Switch the map to the YOLO anchor in `maps/<name>.json`:
   ```json
   "anchor": "yolo_player"
   ```
   (drop `nametag_template` / `title_template`; keep `detector: "mob_yolo"`). Feet =
   bottom-center of the player box; add `"player": {"foot_offset": N}` to nudge if needed.
5. Snap-verify in the panel — the player box draws GREEN, labeled `player`.

`mob_detect.yolo_detect()` returns `(mobs, player)` from a single inference; the farming
loop's `YoloPlayerAnchor` exposes the player box as `.locate(frame) -> (x, feet_y)`. Until
the 3-class model is trained, keep `anchor: "nametag"` (a 2-class model has no player class).

## Tuning `mob_conf` (the runtime threshold)

In `maps/<name>.json`: `mob_conf` filters detections in the farming loop.
- After a clean hybrid retrain (few FPs), **lower it** to raise recall — deep_sea_2 went
  0.72 → 0.5.
- If FPs reappear, nudge up; if mobs are missed, nudge down.
- Check separation live: real mobs vs FP confidences should have a clear gap (real goby
  ~0.88+, FPs were ~0.55). Set `mob_conf` in the gap.

## Live preview (see exactly what the loop sees)
```bash
python panel.py     # http://localhost:8080 -> Detection preview
```
Pick the map, **Snap** (or live 2s). It runs the SAME detector the loop uses (`mob_yolo`)
and draws boxes + the player anchor. Best way to confirm a retrain helped.

---

## Quick reference

```bash
# bootstrap
python capture_mob_template.py <map> <mob>          # live crops
python synth_data.py --bg assets/bg --out datasets/mobs --n 3000 --live assets/mob/<map>
python train_mobs.py 80 640

# improve (repeat whenever detection drifts)
python label_mobs.py --grab "<clip.mp4>" 40         # grab + prelabel
python label_mobs.py                                # correct in browser
python train_mobs.py 80 640 --real                  # hybrid retrain
# then tune mob_conf in maps/<map>.json and Snap-verify in panel.py
```
