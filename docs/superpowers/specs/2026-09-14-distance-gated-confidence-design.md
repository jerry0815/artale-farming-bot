# Distance-gated confidence for mob targeting

**Date:** 2026-09-14
**Status:** Design approved, ready for implementation plan
**Scope:** `approach_shoot` (water farming). Stop the bot locking/firing at weak, far
(likely false-positive) fishhouse detections, while still holding a fishhouse it is
point-blank killing through its own attack VFX.

## Goal

A mob's required detection confidence depends on how close it is to the player:

- **Close** (within `low_conf_range` px of the player) → accepted at the existing low
  floor `fishhouse_conf` (0.3). This is the "I'm on top of it, killing it, my VFX is
  dimming it" zone.
- **Far** (beyond `low_conf_range`) → must clear a new high bar `priority_acquire_conf`
  (~0.6), else it is ignored.

This fixes the observed "wrong direction" bug: on P1 a weak fishhouse detection sat ~155px
right of the player at conf 0.59 while the real fishhouse (0.97) sat ~294px left. The lock
grabbed the nearer weak one and fired right at it (never dying) until the grace expired.
Under this rule the 0.59 blob is *far* (>120) and *below 0.6* → dropped; the 0.97 fishhouse
is kept at any distance.

## Key context / constraints

- **The score already exists but is discarded.** The detector returns `(score, x, y, w, h[,
  cls])`; `same_platform()` unpacks it and drops the score. This design carries the score
  through so targeting can use it: same-platform items become `(cx, feet, cls, score)`.
- **Detector floor stays low (`fishhouse_conf` 0.3).** So a point-blank fishhouse dimmed by
  VFX still comes back from the model; the new gate is applied in `approach_shoot`, not the
  detector.
- **Fishhouse (priority_class) only.** Goby detect weakly even when real; a high bar would
  drop real goby. Goby detections pass through unfiltered. The gate keys off
  `priority_class`, so it is inert on maps without one.
- **Default is a no-op.** If `priority_acquire_conf` is unset, the filter keeps every
  detection (current behavior). The feature activates only when the map sets it.
- **One filter point.** The gate is applied to the whole same-platform set right after it is
  built, so the mob **count / depletion**, the **priority lock**, and the **nearest-mob**
  fallback all operate on the trustworthy set. A platform whose only fishhouse read is a far
  weak blob then counts as empty and depletes/rotates instead of firing at the phantom.

## Architecture

```
detect_fn(frame, roi) -> dets [(score,x,y,w,h,cls)]
        │
same_platform(dets)        # NOW carries score: (cx, feet, cls, score)
        │
trustworthy_same(same, px, priority_class, low_conf_range, acquire_conf)   # NEW pure gate
        │   drop a priority-class det that is far (>range) AND below acquire_conf
        ▼
existing: count / deplete · priority lock+grace · nearest-mob   (unchanged, on the gated set)
```

## Components

### 1. `same_platform` carries the score (in `approach_shoot`)
Change the appended tuple from `(cx, feet, cls)` to `(cx, feet, cls, score)`. All existing
consumers use `m[0]` (x) and `m[2]` (cls); adding `m[3]` is additive.

### 2. `trustworthy_same(same, px, priority_class, low_conf_range, acquire_conf)` — new pure helper
For each item: keep it unless it is `priority_class` AND farther than `low_conf_range` from
`px` AND its score is below `acquire_conf` (or missing). Returns the filtered list.
`acquire_conf is None` → return `same` unchanged (feature off). Pure, unit-tested.

### 3. Wire into `approach_shoot`
After `same = same_platform(dets)`, apply
`same = trustworthy_same(same, px, priority_class, low_conf_range, acquire_conf)`.
Pass `low_conf_range` and `priority_acquire_conf` into `approach_shoot` from `farm_node`
(read from `map_cfg`), alongside the existing `priority_*` params.

### 4. Config (`maps/deep_sea_2.json`)
- `low_conf_range` (screen px, default 120): the close zone.
- `priority_acquire_conf` (default unset → feature off; set 0.6 to enable).
Both with a tuning note; `low_conf_range` must be tighter than the distance to a typical
off-platform phantom (the P1 phantom was 155px), so 120 leaves margin.

## Data flow (one attack beat)

1. Detect → `same_platform` (with score) → `trustworthy_same` drops far weak fishhouses.
2. Count / deplete, priority lock+grace, and nearest-mob all run on the gated set exactly
   as today.

## Error handling / edge cases

- A **real** fishhouse that is far and momentarily dips below `acquire_conf` is dropped for
  that frame → treated as "locked one not in frame" → the existing occlusion-hold/grace
  keeps firing its last position; it re-appears when conf recovers or she closes in. Real
  fishhouses read ~0.9+, so this is rare; if a genuinely faint far fishhouse is skipped,
  lower `priority_acquire_conf`.
- Missing score (older det shape) → treated as below the bar (far ones dropped); close ones
  still kept, so no crash.

## Testing

Unit tests (pure, `tests/test_approach.py`) for `trustworthy_same`:
- far + low-conf priority det → dropped.
- far + high-conf priority det → kept.
- close + low-conf priority det → kept.
- goby (non-priority) far + low-conf → kept (unfiltered).
- `acquire_conf=None` → list returned unchanged.
Plus a regression check that `same_platform` includes the score as `m[3]`.

## Out of scope (YAGNI)

- Gating goby / non-priority classes.
- Per-mob or per-platform acquire thresholds.
- Any change to the lock/grace or fire-stall machinery (this only changes which detections
  are eligible).
