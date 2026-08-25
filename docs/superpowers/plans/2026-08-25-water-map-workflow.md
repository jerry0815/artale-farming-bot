# Water Map Workflow — 水世界 / 深海峽谷2 (deep_sea_2)

How to record and run a **water-world** map. Water maps have direct minimap sensing
(the character dot reads true (x,y), verified no scroll-pin) and free 2D swimming, so
navigation is one primitive: `swim_to(x, y)`. No ropes, no down-jumps, no edges.

Files: [watermap.py](../../../watermap.py) (pure helpers), water functions in
[recovery.py](../../../recovery.py) (`swim_to`, `farming_loop_water`, `set_minimap`),
starter config [maps/deep_sea_2.json](../../../maps/deep_sea_2.json).

## 1. Calibrate the minimap crop (once)

The starter crop `{"x":20,"y":171,"w":210,"h":400}` was measured from the video. Verify
it live: it must contain the whole playable minimap (all platforms), not the UI below it.

```bash
python recovery.py where --minimap 20,171,210,400
```
Stand on the lowest and highest platforms and re-run; the printed `(x,y)` should change
sensibly and never read `(-1,-1)`. If the lowest platform reads off the bottom, increase
`h`. Update `maps/deep_sea_2.json` `minimap` with the values that work.

## 2. Record the platforms

Record in the **water crop frame** so coordinates match. Mark each platform you want to
farm, in the rotation order you want (bottom → top is typical).

```bash
python recovery.py record-route deep_sea_2 --minimap 20,171,210,400
```
- **F9** at each platform → name it (`P_BOT`, `P_MID`, `P_TOP`, …). Pause ~1s on each so
  the band is clean.
- Swim the map so every platform gets a mark. **F10** to stop.

Writes `routes/deep_sea_2.capture.jsonl`.

## 3. Build the map config

One command turns the capture into a ready water map config (nodes + minimap + swim):

```bash
python build_route.py routes/deep_sea_2.capture.jsonl maps/deep_sea_2.json deep_sea_2 P_BOT,P_MID,P_TOP --minimap 20,171,210,400 --swim 3,3
```
The last arg before the flags is the comma-separated **farm rotation**. Open
`maps/deep_sea_2.json` and sanity-check each node's `band` center looks right. (Edges in
the file are ignored for water maps.)

## 4. Run it — bounded first

```bash
python recovery.py waternav 120 --map maps/deep_sea_2.json
```
Hand on **F8** to pause. Watch that she swims to each platform, fires, and rotates when a
platform runs dry. Tune in `maps/deep_sea_2.json`:
- `swim.tol_x/tol_y` — larger = stops sooner / less jitter; smaller = tighter parking.
- node `band` centers — nudge if she parks off-platform.

Full run (F8 to start/pause):
```bash
python recovery.py waternav --map maps/deep_sea_2.json
```

## Sweep mode + the stacked P3/P4 pair (deep_sea_2)

deep_sea_2 has 6 platforms, mostly vertical. Two of them (P3, P4) are **stacked** and the
scrolling minimap reads both at the same y≈136 — the minimap can't tell them apart. The
loop sidesteps this by **never sensing which is which**: it runs a one-way **sweep** and
tracks the intended platform by sequence.

Config keys (in `maps/deep_sea_2.json`):
- `"rotation": "sweep"` — farm `farm_nodes` in listed order (BOTTOM→TOP), then reset.
- `farm_nodes` — the sweep order, e.g. `["P6","P5","P4","P3","P2","P1"]` (bottom→top).
  List the stacked pair **consecutively** (whichever order you swim them). Because the bot
  arrives by intent, it knows it's on P3 then P4 without reading the minimap; it separates
  them by their distinct x.
- `"reset_node": "RIGHT"` — a rightmost open-water waypoint. After the top platform the bot
  swims here, then straight down to `farm_nodes[0]` (the natural drop back to the bottom).
- `"beats_per_node": 3` — shooting beats per platform before advancing (or fewer if the
  detector reports it empty).

**Recording for sweep:** mark all 6 platforms **plus** the `RIGHT` waypoint (stand at the
rightmost point you drop from and F9 → name it `RIGHT`). Then:
```bash
python build_route.py routes/deep_sea_2.capture.jsonl maps/deep_sea_2.json deep_sea_2 P6,P5,P4,P3,P2,P1 --minimap 20,171,210,400 --swim 3,3
```
(The farm_nodes CSV = the sweep order; `RIGHT` stays a node but is referenced via
`reset_node`, not the farm list.) Re-add `rotation`/`reset_node`/`beats_per_node`/`detector`
to the file if the rebuild overwrites them, or edit the generated file to include them.

## Fish detection (depletion rotation)

The dragon YOLO model doesn't know these fish, so the water loop can count them by
**template-matching** the sprite set (`bombing_fish_house`, `goby`, `bone_fish` under
`MapleStoryAutoLevelUp/monster`). Config keys (in `maps/deep_sea_2.json`):

- `detector`: `"fish"` (template match), `"yolo"` (only if the fish match the model), or
  `"time"` (rotate every beat, no detection — the reliable fallback).
- `fish_scale`: resize sprites toward on-screen size (start 1.0).
- `fish_threshold`: match score cutoff (TM_CCORR_NORMED; start 0.9).
- `fish_per_species`: templates per species (speed vs recall; start 3).
- `count_roi`: `[x0,y0,x1,y1]` screen region to search (null = whole frame; set a band
  around the platforms for speed + fewer false positives).

**Tune it live** on a populated platform:
```bash
python recovery.py fishcount --map maps/deep_sea_2.json
```
It prints the count + best match score per species and writes `fishcount_debug.png`
(yellow=ROI, red=matches). Adjust `fish_scale`/`fish_threshold`/`count_roi` until the
count matches what you see. On the sample video frame the best scores were only
~0.75–0.88, so **template matching here is marginal** — if you can't get clean counts,
set `"detector": "time"` and rotate on `stand_secs` instead.

## Notes / knobs

- Attack key is `c` (held); buffs `a`/`j`; heal `h` — same as the land loop.
- Dragon counting reuses YOLO (`monsters.load_dragon_model`); if the fish here differ
  from the trained dragons, counts may be off → depletion rotation won't trigger. If so,
  either retrain (see the YOLO memory) or run with a time-based rotation (raise
  `stand_secs`, ignore counts).
- `swim_to` aborts on F8 and always releases the arrow keys on exit.
- This reuses lie-check, breaks, skills, panic, and the `STATUS`/`STOP` surface, so the
  panel's Watch-only and status still apply.
