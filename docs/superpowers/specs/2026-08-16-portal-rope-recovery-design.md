# Portal-Bottom → Top Recovery (right-side rope chain) — Design

## Problem

The character can be knocked down the **right side** of the map onto platforms
the recovery graph doesn't cover. The mid-right ledge (`~155,131`) was fixed by
the `MID_R` node + the recover-or-panic fallback. But the **deepest** right spot —
the **portal-bottom platform** (`~153,183`, near the map's blue portal) — cannot
reach the central rope at all: walking left toward `ROPE_X (91)` from there drops
into a gap.

Today `classify_node(153,183)` returns `LOWER_LEDGE`, whose recovery
(`recover_to_farming`) bails immediately (`y=183 > RECOVER_Y_MAX=156`), so the
loop **panics to Free Market**. That's safe (no freeze) but ends farming on a
fall that is physically recoverable — via a **two-rope right-side climb** the bot
doesn't yet know about.

## Goal

Make the portal-bottom fall recoverable by encoding the right-side rope chain as
`navmap` nodes/edges and adding one new climb primitive, so `travel()` chains the
hops back to `TOP_FARM`. Do it without regressing the central-rope recovery the
whole farming loop depends on, and without reintroducing any freeze (a failed hop
must still degrade to today's panic-to-safety).

## Non-Goals

- No change to central-rope recovery (`recover_to_farming` / `climb_and_jump` /
  `_climb_to_top`) — it is reused unchanged as the final hop.
- No farming *on* the right-side platforms; these nodes are recovery-only.
- No handling of falls deeper than the portal-bottom (none observed); those keep
  the recover-or-panic fallback.

## Calibrated map (live `record-climb` + `sense`, 2026-08-16, bot minimap frame)

```
PORTAL_BOT (153,183) --portal rope: walk left to x132, climb, dismount RIGHT--> L4_RIGHT (140,149)
L4_RIGHT   (140,149) --right rope:  walk right to x147, climb, step off at top--> MID_R (146,136)
MID_R      (146,136) --[EXISTING] recover_to_farming: walk left to central rope, climb--> TOP_FARM
```

Measured facts:
- `PORTAL_BOT`: start dwell `(153,183)` (also read `(145,184)` earlier).
- Portal rope climb column `x≈132`; climb `y 184→148` (~36px, a real level);
  dismount **right** (column 132 → ledge x140).
- `L4_RIGHT`: dwell `(140,149)` (run-1 end) / `(137,149)` (run-2 start).
- Right rope grab column `x≈147`; climb `y 149→136`; lands **at the rope top**
  (no side dismount) on `(146,136)`.
- `(146,136)` is a genuine standing ledge (6/6 identical `sense` reads) and is
  **central-reachable** (user confirmed: walk left to the central rope, no gap).
- `(146,136)` already falls inside the existing `MID_R` band, whose recovery is
  the central-rope climb — so the right rope's terminus needs **no new node**.

## Design

### New `navmap` nodes (bands: `y_lo,y_hi,x_lo,x_hi`, first match wins)

| Node | band | placement / rationale |
|---|---|---|
| `PORTAL_BOT` | `176,195,140,168` | placed **before** `LOWER_LEDGE` (carved out of it) so the portal-bottom reads as its own node |
| `L4_RIGHT` | `143,156,120,172` | the portal-rope landing ledge; isolated (not central-reachable) |
| `MID_R` (edit) | `111,`**`142`**`,141,172` | narrow y `150→142` so a `L4_RIGHT` read (y149) can't fall into `MID_R` |

**Why the `MID_R` narrowing is load-bearing:** `L4_RIGHT` (y149) and `MID_R`
(y131–136) are different heights. If `MID_R` still spanned to y150, a `L4_RIGHT`
read would classify as `MID_R`, and `MID_R` recovery walks left toward the central
rope — straight into the gap `L4_RIGHT` sits behind. Splitting at y142/143 keeps
the isolated ledge (needs the right rope) apart from the central-reachable one.

### New edges (rope) + executors

| edge | executor |
|---|---|
| `PORTAL_BOT → L4_RIGHT` | `climb_rope_hop(grab_x=132, target="L4_RIGHT", dismount="right")` |
| `L4_RIGHT → MID_R` | `climb_rope_hop(grab_x=147, target="MID_R", dismount=None)` |
| `MID_R → TOP_FARM` | **existing** `recover_to_farming` (already wired) |

`plan(PORTAL_BOT, TOP_FARM)` (BFS over `EDGES`) returns all three hops; `travel()`
executes them in order. A partial fall that lands on `L4_RIGHT` naturally plans
from there — no special-casing.

### New primitive — `climb_rope_hop(grab_x, target, dismount, cap)`

A **bounded, single-rope** sibling of `_climb_to_top` (which always rides the
central column to the top). Contract:

1. `walk_to_x(grab_x)` to reach the rope base (direction is implied: left for the
   portal rope, right for the right rope).
2. Press `Up` + a hop-jump to grab the rope (as `climb_and_jump` does).
3. Hold `Up` while rising. These hops are short (13–36px) and end at/above y136,
   so progress is read from the **true dot-y** (they don't reach the long-climb
   scroll-pin regime); track `y` decreasing toward the target band's `y_lo`.
4. On reaching the target level, if `dismount` is `"left"`/`"right"` tap that
   direction to step onto the ledge; if `None`, the rope tops out on the ledge
   (right rope) so just release `Up`.
5. Verify she settled inside `target`'s band (`classify_node == target`).
   Return `True`; otherwise `safe_release_all()` and return `False`.

Bounded by `cap` seconds / a grab count so it can never hang.

### Error handling — no new failure mode

If any `climb_rope_hop` can't reach its target it returns `False` → its
`EDGE_ACTIONS` entry returns `False` → `travel()` exhausts `max_rounds` and returns
`False` → the nav loop calls `panic()`. That is **exactly today's outcome** for the
portal-bottom, so a flaky new hop degrades to the current safe behavior and cannot
reintroduce a freeze. The central-rope recovery is untouched.

## Testing

**`navmap` unit tests** (pure, no live game — extend `tests/test_navmap.py`):
- `classify_node(153,183) == "PORTAL_BOT"`, `(140,149) == "L4_RIGHT"`,
  `(146,136) == "MID_R"`.
- Regression on the narrowing: `(150,140) == "MID_R"` (still), and a `L4_RIGHT`
  read like `(140,149)` is **not** `MID_R`.
- `plan("PORTAL_BOT","TOP_FARM")` returns 3 hops ending at `TOP_FARM`, all `rope`.
- `test_every_edge_has_an_executor` covers the 2 new edges (already asserts this
  for all `EDGES`).

**`climb_rope_hop` mechanics**: live-tested only (screen/keys), like
`climb_and_jump`. Verify each hop in isolation, then the full
`recover` from portal-bottom → top. Not unit-testable here.

## Rollout

1. `navmap.py`: add `PORTAL_BOT`, `L4_RIGHT` nodes + bands, narrow `MID_R`, add the
   two rope edges. Update `tests/test_navmap.py`. (pure — verify green)
2. `recovery.py`: add `climb_rope_hop`; wire the two new `EDGE_ACTIONS`.
3. Live: calibrate/verify each hop, then end-to-end `recover` from portal-bottom.
   Constants (`grab_x`, `cap`, dismount taps) tuned against live runs.
