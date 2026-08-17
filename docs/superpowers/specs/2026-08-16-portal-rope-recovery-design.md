# Portal-Bottom → Top Recovery (right-side rope chain) — Design

## Problem

The character can be knocked down the **right side** onto the **portal-bottom
platform** (`~153,183`, by the map's blue portal). From there she can't reach the
central rope directly (walking left drops into a gap). Today `classify_node(153,183)`
→ `LOWER_LEDGE`, whose `recover_to_farming` bails (`y=183 > RECOVER_Y_MAX=156`), so the
loop **panics to Free Market** — safe, but ends farming on a physically recoverable
fall (climb the portal rope, then the right rope, then rejoin the central climb).

## Key finding from live calibration (why this design is shaped as it is)

Walking the right/mid ledges live showed two things that **rule out** a per-ledge
node graph up there:

1. **The mid/right ledges overlap in minimap-space.** `MID`, `BOTTOM_FARM`, the
   right-rope top, and an x142 ledge all read around **y136–142** at overlapping x.
   Rectangular bands can't separate them; six revisions each surfaced a new conflict.
2. **The upper climb hits the minimap scroll-pin.** Above ~y136 the dot pins at
   ~(95,136) while terrain scrolls, so position reads are untrustworthy mid-climb —
   the same behavior the central climb already handles via `minimap_scroll`.

**Conclusion:** don't classify or band that zone. Classify only the two clean,
well-separated entry ledges (`PORTAL_BOT`, `LOWER_R`), script two short rope hops to
lift her up to the mid stretch, and hand the pinned upper climb to the existing
`recover_to_farming` (scroll-aware; **proven** to walk left off the right side and
reach the top — `recover` from `(158,131)` → `RESULT: True`).

## Goal

Recover the portal-bottom fall by adding two entry nodes + two scripted rope hops,
reusing `recover_to_farming` for the top. No change to central recovery. A failed hop
degrades to today's panic-to-safety (no new freeze path).

## Design

### Nodes (only the two clean-to-classify entry ledges)

| Node | band `y_lo,y_hi,x_lo,x_hi` | note |
|---|---|---|
| `PORTAL_BOT` | `178,190,108,162` | deep ledge by the portal; y183 — far from any other band. Placed **before** `LOWER_LEDGE` |
| `LOWER_R` | `143,151,128,163` | portal-rope landing; y149, between the y152+ `LOWER_LEDGE` and the y≤142 mid cluster |

Also: extend `LOWER_LEDGE` `y_lo` `157→152` so the rope-transit gap just below `LOWER_R`
reads as ledge, not `UNMAPPED`.

**No `BOTTOM_R`, no `MID` widening, no `MID_R`.** The mid stretch and everything above
it stay unclassified (they're the scroll-pinned / overlapping zone) and are handled by
`recover_to_farming`, not by node bands. The committed `MID_R` node is **removed**;
its former reads recover via `recover_to_farming` like the rest of the right side.

### Edges + executors

| edge | executor |
|---|---|
| `PORTAL_BOT → LOWER_R` | `climb_rope_hop(grab_x=132, land="LOWER_R", dismount="right")` — climb the portal rope, verify she settled on `LOWER_R` |
| `LOWER_R → TOP_FARM` | **composite**: `climb_rope_hop(grab_x=147, rise≈10px onto the mid stretch)` **then** `recover_to_farming()` (walks left to the central rope, scroll-tracked climb to top) |

`plan(PORTAL_BOT, TOP_FARM)` → `[PORTAL_BOT→LOWER_R, LOWER_R→TOP_FARM]`; `travel()`
runs them. A partial fall onto `LOWER_R` plans straight from `LOWER_R → TOP_FARM`.

### New primitive — `climb_rope_hop(grab_x, ..., dismount, cap)`

A **bounded, single-rope** lift, distinct from `_climb_to_top` (which rides the
central column all the way to the top and is scroll-tracked). Contract:

1. `walk_to_x(grab_x)` to the rope base (left for the portal rope, right for the
   right rope).
2. `Up` + hop-jump to grab (as `climb_and_jump` does).
3. Hold `Up` while rising. **Verify progress by rise, never by classifying the cluster:**
   the portal hop is short and below the pin (track true dot-y until she reaches the
   `LOWER_R` band); the right-rope hop only needs to rise ~10px off `LOWER_R` onto the
   mid stretch before `recover_to_farming` takes over.
4. `dismount` (`"right"` for the portal hop; `None` for the right-rope hop — it hands
   off immediately).
5. Return `True`/`False`; on failure `safe_release_all()`. Bounded by `cap`/grab count.

### Error handling — no new failure mode

Any hop that can't make progress returns `False` → its `EDGE_ACTIONS` entry `False` →
`travel()` gives up → the nav loop `panic()`s. Exactly today's outcome for the
portal-bottom, so a flaky hop degrades to panic-to-safety, never a freeze. Central
recovery untouched.

## Testing

**`navmap` unit tests** (pure — `tests/test_navmap.py`):
- `classify_node(153,183)=="PORTAL_BOT"`, `(140,149)=="LOWER_R"`.
- `LOWER_R`/`LOWER_LEDGE` seam: `(132,152)` is `LOWER_LEDGE`, `(140,149)` is `LOWER_R`.
- `plan("PORTAL_BOT","TOP_FARM")` → 2 hops ending at `TOP_FARM`.
- `test_every_edge_has_an_executor` covers the 2 new edges.
- The old `MID_R` node + its tests are removed; former `MID_R` reads no longer need a
  node (they recover via `recover_to_farming`).

**`climb_rope_hop` mechanics** and the full portal-bottom → top recovery: live-tested
(screen/keys), like `climb_and_jump`.

## Rollout

1. **Bands (live, first).** Confirm `PORTAL_BOT` (y178–190, x108–162) and `LOWER_R`
   (y143–151, x128–163) edge-to-edge via `verify_bands.py`. These two are far from the
   overlapping cluster, so they classify cleanly.
2. `navmap.py`: add `PORTAL_BOT`, `LOWER_R` + bands; extend `LOWER_LEDGE` y_lo→152;
   **remove `MID_R`**; add the two edges. Update `tests/test_navmap.py`. (pure — green)
3. `recovery.py`: add `climb_rope_hop`; wire `PORTAL_BOT→LOWER_R` and the composite
   `LOWER_R→TOP_FARM`; drop the `MID_R` executor.
4. Live: verify each hop, then end-to-end `recover` from portal-bottom. Tune
   `grab_x≈132`/`147`, `cap`, dismount taps against live runs.
