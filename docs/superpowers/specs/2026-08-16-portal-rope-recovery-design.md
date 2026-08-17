# Portal-Bottom → Top Recovery (right-side rope chain) — Design

## Problem

The character can be knocked down the **right side** of the map onto ledges the
recovery graph doesn't cover. The mid-right ledge was patched earlier (a `MID_R`
node + the recover-or-panic fallback), but the **deepest** right spot — the
**portal-bottom platform** (`~153,183`, by the map's blue portal) — can't reach the
central rope at all: walking left toward `ROPE_X (91)` from there drops into a gap.

Today `classify_node(153,183)` returns `LOWER_LEDGE`, whose recovery
(`recover_to_farming`) bails immediately (`y=183 > RECOVER_Y_MAX=156`), so the loop
**panics to Free Market**. Safe (no freeze) but it ends farming on a fall that is
physically recoverable — via a **right-side rope chain** the bot doesn't yet model.

## Goal

Recover the portal-bottom fall by encoding the right-side ledges/ropes as `navmap`
nodes + edges and adding one climb primitive, so `travel()` chains the hops back to
`TOP_FARM`. Reuse the central-rope recovery unchanged as the final leg. A failed hop
must still degrade to today's panic-to-safety (no new freeze path).

## The map (left/central stack has right-side counterparts)

The right side mirrors the central stack by height. The `_R` suffix means
"right-side counterpart, reached via the **right ropes**" (recovers differently from
its left twin, so it is a distinct node):

```
   height        central/left        right counterpart
   ------        ------------        -----------------
   y0–95         TOP_FARM            (shared top)
   y111–131      MID                 (shared — BOTH sides climb up to the one MID)
   y132–156      BOTTOM_FARM         BOTTOM_R   (was MID_R)
   y157–200      LOWER_LEDGE         LOWER_R    (was L4_RIGHT)
   (deepest)     —                   PORTAL_BOT (by the portal)
```

There is exactly **one `MID`**. The left climb (`BOTTOM_FARM → MID → TOP`) and the
right climb (`BOTTOM_R → MID → TOP`) both top out onto it. `MID_R` is deleted — the
readings once called `MID_R` (146,136 / 155,131 / 158,136) are the **right end of
`MID`** / the `BOTTOM_R` ledge that climbs onto it.

## Recovery chain

```
PORTAL_BOT --portal rope: walk left to x~132, climb, dismount RIGHT--> LOWER_R
LOWER_R    --right rope:  walk right to x~147, climb onto the ledge--> BOTTOM_R
BOTTOM_R   --[EXISTING] recover_to_farming: walk left to central rope, climb--> TOP_FARM
```

`BOTTOM_R → TOP_FARM` mirrors the existing `BOTTOM_FARM → TOP_FARM` edge exactly —
same `recover_to_farming`, which climbs the central rope up through `MID` to the top
in one pass (live-verified from the right end at `(158,131)/(158,136)`).

## Design

### Nodes (bands `y_lo,y_hi,x_lo,x_hi`, first match wins — **PROVISIONAL**, finalized live)

| Node | provisional band | note |
|---|---|---|
| `PORTAL_BOT` | `178,190,108,162` | placed **before** `LOWER_LEDGE` (carved out of it); wide bottom ledge by the portal |
| `LOWER_R` | `143,154,128,163` | portal-rope landing; right twin of `LOWER_LEDGE`; needs the right rope |
| `BOTTOM_R` | `129,142,141,166` | right rope's top; right twin of `BOTTOM_FARM`; central-reachable. **Replaces** the committed `MID_R` |

Band numbers are **provisional** — four chat-driven guesses mis-sized them from
under-sampling. They're finalized live in implementation **step 1** (`verify_bands.py`,
character standing on each ledge). The `_R` nodes are **distinct** from their left
twins (`LOWER_LEDGE`, `BOTTOM_FARM`) because left/right recover via different ropes;
merging would make one edge try to serve both sides.

**Separation rule (pixel-independent, load-bearing).** `LOWER_R` (y~149) and
`BOTTOM_R` (y~136) are stacked and read close in y (bob overlaps). Bias the seam to
`LOWER_R`: an ambiguous read routes to `LOWER_R` (climb the right rope up — harmless
even if already higher), **never** to `BOTTOM_R` (whose recovery walks left toward
the central rope and would drop into the gap the lower ledge sits behind).

### Edges (rope) + executors

| edge | executor |
|---|---|
| `PORTAL_BOT → LOWER_R` | `climb_rope_hop(grab_x=132, target="LOWER_R", dismount="right")` |
| `LOWER_R → BOTTOM_R` | `climb_rope_hop(grab_x=147, target="BOTTOM_R", dismount=None)` |
| `BOTTOM_R → TOP_FARM` | **existing** `recover_to_farming` (mirrors `BOTTOM_FARM → TOP_FARM`) |

`plan(PORTAL_BOT, TOP_FARM)` (BFS over `EDGES`) returns the three hops; `travel()`
runs them in order. A partial fall onto `LOWER_R` or `BOTTOM_R` naturally plans from
there — no special-casing.

### New primitive — `climb_rope_hop(grab_x, target, dismount, cap)`

A **bounded, single-rope** sibling of `_climb_to_top` (which always rides the central
column to the top). Contract:

1. `walk_to_x(grab_x)` to the rope base (direction implied: left for the portal rope,
   right for the right rope).
2. `Up` + a hop-jump to grab the rope (as `climb_and_jump` does).
3. Hold `Up` while rising. These hops are short (~13–36px) and end at/above y136, so
   progress reads from the **true dot-y** (they don't reach the long-climb scroll-pin
   regime); track y decreasing toward `target`'s `y_lo`.
4. On reaching the target level: if `dismount` is `"left"`/`"right"`, tap it to step
   onto the ledge; if `None`, the rope tops out on the ledge — release `Up`.
5. Verify she settled in `target`'s band (`classify_node == target`); return `True`,
   else `safe_release_all()` and return `False`. Bounded by `cap` / a grab count.

### Error handling — no new failure mode

A hop that can't reach its target returns `False` → its `EDGE_ACTIONS` entry returns
`False` → `travel()` exhausts `max_rounds` → the nav loop calls `panic()`. That is
exactly today's outcome for the portal-bottom, so a flaky new hop degrades to the
current safe behavior and cannot reintroduce a freeze. Central recovery is untouched.

## Testing

**`navmap` unit tests** (pure — extend `tests/test_navmap.py`):
- `classify_node` for `PORTAL_BOT`, `LOWER_R`, `BOTTOM_R` (once bands are final).
- `BOTTOM_R`/`LOWER_R` seam biases to `LOWER_R` at the ambiguous y.
- `plan("PORTAL_BOT","TOP_FARM")` returns 3 hops ending at `TOP_FARM`.
- `test_every_edge_has_an_executor` covers the 2 new edges.
- Regression: the old `MID_R` node/tests are removed; its former reads now classify
  as `BOTTOM_R` (or `MID`) and still recover.

**`climb_rope_hop` mechanics**: live-tested only (screen/keys), like `climb_and_jump`.

## Rollout

1. **Finalize bands live (first).** `verify_bands.py`; stand on each of `PORTAL_BOT`,
   `LOWER_R`, `BOTTOM_R`, walk edge-to-edge, set final bands with the bias-to-`LOWER_R`
   seam. Resolves the provisional numbers before code depends on them.
2. `navmap.py`: add `PORTAL_BOT`, `LOWER_R`, `BOTTOM_R` + finalized bands; **remove**
   `MID_R`; add the two rope edges. Update `tests/test_navmap.py`. (pure — verify green)
3. `recovery.py`: add `climb_rope_hop`; wire the two new `EDGE_ACTIONS`; drop the
   `MID_R` executor.
4. Live: verify each hop, then end-to-end `recover` from portal-bottom. Constants
   (`grab_x≈132`/`147`, `cap`, dismount taps) tuned against live runs.
