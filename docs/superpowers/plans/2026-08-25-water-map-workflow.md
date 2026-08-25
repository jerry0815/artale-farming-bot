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

## Notes / knobs

- Attack key is `c` (held); buffs `a`/`j`; heal `h` — same as the land loop.
- Dragon counting reuses YOLO (`monsters.load_dragon_model`); if the fish here differ
  from the trained dragons, counts may be off → depletion rotation won't trigger. If so,
  either retrain (see the YOLO memory) or run with a time-based rotation (raise
  `stand_secs`, ignore counts).
- `swim_to` aborts on F8 and always releases the arrow keys on exit.
- This reuses lie-check, breaks, skills, panic, and the `STATUS`/`STOP` surface, so the
  panel's Watch-only and status still apply.
