# Two-Stage Rope Climb (bottom → mid → top) — Design

## Problem

The bottom→top ascent is modeled as a **single continuous rope**. Both
`climb_and_jump` ([recovery.py:324](../../../recovery.py)) and the dead
`_climb_from_bottom_continuous` just hold `Up` until `y <= ROPE_EXIT_TOP_Y (92)`,
then up-jump. Every live ascent — `farming_loop`, `farming_loop_nav`, and the
`navmap` `EDGE_ACTIONS` — bottoms out in `recover_to_farming`, which uses that
single-rope climb.

Physically the ascent is **two rope segments with a horizontal shift**:

```
top farming    y~85    <- rope R_L (left) tops out here; up-jump onto platform
                        
MID ledge      y~111-131  <- rope R_C (center) tops out here (a DISTINCT ledge,
                             not the REST platform); shift LEFT to rope R_L
                        
bottom farming y~132-156 <- rope R_C (center) base; hop+Up to grab
```

So holding `Up` from the bottom reaches only the **MID ledge**, then stalls —
`recover_to_farming` then burns its retry rounds and never reaches the top from
the bottom platform.

`navmap.py` already half-encodes the truth: it defines a `MID` node
(y 111–131, x 80–140) and two ropes — `R_C` (center, from `BOTTOM_FARM`/`LOWER_LEDGE`)
and `R_L` (left, from `MID`/`REST`). But it wires `R_C` as `BOTTOM_FARM → TOP_FARM`
(one rope to the top), and the code comment at [recovery.py:661](../../../recovery.py)
admits *"Per-rope single-level moves are a live follow-up."* This design is that
follow-up.

## Goal

Make the ascent a **two-segment climb** — center rope `BOTTOM → MID`, shift left,
left rope `MID → TOP` — grounded on **measured** map constants, without regressing
the existing "anywhere-below → top" recovery that the whole farming loop depends on.

## Non-Goals

- No rewrite of `navmap.travel` / `EDGE_ACTIONS` into per-edge executors
  (that is Approach B, a larger follow-up). `recover_to_farming` stays the atomic
  "anywhere-below → top" entry point that all callers already use.
- No change to the descent path (`go_to_bottom`, `drop_to_fallen`, `rest_to_bottom`).
- No change to the horizontal predictive-grab work (separate plan).

## Part 1: Measure the climb geometry

The bot cannot currently perform a full bottom→top climb (that is the bug), so the
recording is **human-driven**: the user manually climbs once while a sense-only
recorder logs the trajectory.

**Recorder.** `record_climb_attempt()` in `recovery.py` polls `get_character_full()`
at ~30 Hz into `reads = [[t, x, y], ...]`, presses **no keys**, and stops on F8
(`kb.pause`) or a time cap. CLI: `python recovery.py record-climb` — appends one
JSON line `{"reads": [...]}` to `climb_traj.jsonl` (git-ignored) and prints live
`(x, y)`. Zero-risk: it never moves the character.

**Constants read from the trace** (become named constants in `recovery.py`, grouped
with the existing rope constants; each cross-checked against the current value):

| Constant | Read from trace | Seed / cross-check |
|---|---|---|
| `R_C_X` (center rope x) | x during the first rising segment (y falling from ~143) | `BOTTOM_ROPE_X ≈ 95` |
| `MID_LEDGE_Y` | y where the first rising segment stalls | navmap `MID` band 111–131 |
| `MID_LAND_X` | x while standing on the ledge before moving left | navmap `MID` x 80–140 |
| `R_L_X` (left rope x) | x during the second rising segment | `ROPE_X ≈ 91` |
| `TOP_EXIT_Y` | y where the second segment ends / up-jump fires | `ROPE_EXIT_TOP_Y = 92` |
| `R_L_HOP` (bool) | does y drop with no jump when Up pressed on the ledge, or is a hop needed to reach the left rope's base? | today's 2nd-grab does not hop |

The `MID_LAND_X → R_L_X` delta is the empirically-measured "slightly left" shift for
stage 2's `walk_to_x` target. If the trace disagrees with the navmap bands, reconcile
the bands — that reconciliation itself validates the model.

## Part 2: Two-stage climb execution

**New single-segment primitive** (factors out today's hold-Up loop, including the
existing stall/no-grab detection):

```
_climb_segment(grab_x, exit_y, hop) -> bool
  walk_to_x(grab_x, tol=1)                  # align onto the rope (as today)
  if hop: press Up + Jump (rope base above the floor); else press Up
  hold Up until y <= exit_y  OR rising stalls off the rope
  return True iff it reached exit_y; on failure release keys and return False
```

**`climb_and_jump` becomes two staged segments** (name + `bool` return contract
preserved, so the `recover_to_farming` call site at [recovery.py:1039](../../../recovery.py)
is unchanged):

```
climb_and_jump():
  x0, y0 = get_character_full()
  if y0 in BOTTOM/LOWER band:                        # start on the center rope
     if not _climb_segment(R_C_X, MID_LEDGE_Y, hop=True):
        return False                                 # keys already released
     xm, ym = get_character_full()
     if ym not in MID band:                          # didn't land on the ledge -> bail
        kb.safe_release_all(); return False
     if not walk_to_x(R_L_X, tol=1):                 # the measured LEFT shift
        return False
  # on MID/REST (or just arrived at MID): climb the left rope to the top
  if not _climb_segment(R_L_X, TOP_EXIT_Y, hop=R_L_HOP):
     return False
  up-jump onto the top platform                      # unchanged from today
  return True
```

**Pure stage decision** (unit-testable without the game):

```
_climb_plan(y0) -> ["R_C->MID", "MID->TOP"]   if y0 in BOTTOM/LOWER band
               -> ["MID->TOP"]                 if y0 in MID/REST band
```

## Error handling / no-regression

- Every early `return False` releases keys first, so `recover_to_farming`'s round
  loop re-localizes and retries — the existing safety net covers a missed stage.
- After stage 1, verify she is actually in the MID band before walking left; if not
  (e.g. she fell or a dragon knocked her), bail to the round loop instead of blindly
  shifting left.
- A climb that starts from `MID`/`REST` runs only stage 2 — behaviorally the same as
  today's single climb from that height.
- `recover_to_farming`'s round count already tolerates a dragon hit mid-climb
  (`max_rounds=8`); the two-stage climb consumes at most one round per attempt.

## Testing (pure, no game — matches the repo's existing test culture)

- `_climb_plan(y0)` banding: bottom/lower → two stages; mid/rest → one stage;
  boundary y values.
- `_climb_segment` loop with monkeypatched `get_character_full` / `kb`
  (as `tests/test_rope_grab.py` already does): a fed y-sequence that stalls in the
  mid band exits stage 1 `True` at `MID_LEDGE_Y`; a sequence that keeps rising exits
  at `exit_y`; a sequence that never grabs returns `False`.
- Recorder builds a schema-valid `{"reads": [...]}` from injected reads (no keys),
  mirroring `test_recorder_produces_valid_trajectory`.
- Full `pytest tests/ -q` stays green (existing `test_navmap.py`, `test_rope_grab.py`).

## navmap consistency (small, optional)

Change the `R_C` edge from `BOTTOM_FARM → TOP_FARM` to `BOTTOM_FARM → MID` (and the
`LOWER_LEDGE` `R_C` edge likewise) so `plan(BOTTOM_FARM, TOP_FARM)` yields the two-hop path
`[BOTTOM→MID (R_C), MID→TOP (R_L)]`. No executor rewrite: `recover_to_farming`
remains the atomic climb, and `EDGE_ACTIONS` keeps mapping the recover-style edges to
it. This is a one-line honesty fix so the graph stops claiming `R_C` reaches the top.

## Files

- **Modify `recovery.py`** — `record_climb_attempt` + `record-climb` CLI; the measured
  climb constants; `_climb_segment`; `_climb_plan`; two-stage `climb_and_jump`.
- **Modify `navmap.py`** — correct the `R_C` edge (optional consistency fix).
- **Modify `tests/test_rope_grab.py`** (or a new `tests/test_climb.py`) — the pure tests above.
- **Modify `.gitignore`** — ignore `climb_traj.jsonl`.

## Live steps (with the user, per-action; separated from coding)

1. `python recovery.py record-climb` → user manually climbs bottom→top once → read
   the six constants off `climb_traj.jsonl`, set them in `recovery.py`.
2. After the code lands: `python recovery.py recover` from the bottom platform a few
   times → confirm it reaches the top in one round with no regression from
   mid/rest starts.
