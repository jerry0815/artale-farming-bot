# Template → YOLO Migration — Design

Date: 2026-09-07
Status: Approved (pending spec review)

## Purpose

Reduce the project's reliance on template matching where it is **fragile or slow**, and
codify **when** a detector should be YOLO, color/geometry, or stay a template. Motivated
by three observations: specific templates keep failing (the lie-check monster popup, which
got the account flagged), the multi-scale lie-check match is slow (~0.5 s), and the
detection stack currently mixes three paradigms with no written rule for choosing between
them.

The key finding from the design discussion: **"refactor all templates to YOLO" is the
wrong goal.** YOLO wins for large, textured, variable targets on the game screen; color/
geometry wins for small fixed-color minimap markers; and a few templates are *deliberate
independent backstops* to YOLO and must stay templates. This spec converts only the pieces
that genuinely benefit, and records the rule so the mix stops feeling accidental.

## The decision rule (the real deliverable)

Every detector is classified by **(input region + target appearance + cadence)**:

- **YOLO** — large, textured, variable-appearance targets on the full game screen
  (mobs, player, popups). Robust to background/pose/scale; ~10–30 ms on the local GPU.
- **Color / geometry** — small, fixed-color UI markers, especially on the minimap crop
  (~230 px). ~1 ms, deterministic; YOLO is *worse* here (weak sub-16 px, per-call
  overhead exceeds the whole color op, and the reads run in the tightest control loops).
- **Template** — appropriate for offline crop-alignment tooling, and for **backstops that
  must fail independently of YOLO** (see nametag below).

Model grouping: **one YOLO model per (region + cadence + co-detected group)**, never one
unified model (scale/cadence/retrain-blast-radius all fight it), never one-per-class.
`mob_yolo` (mobs + player, one inference) and `lie_check_yolo` (separate, different clock)
are already the correct grain.

## Scope — keep / convert / leave

Full inventory of `cv2.matchTemplate` usage, classified:

| Template usage | Decision | Target |
|---|---|---|
| `detection.detect_lie_check` — safety popups | **CONVERT** | → YOLO primary (evidence-gated) |
| `detection.detect_red_dots` — enemy minimap dots | **CONVERT** | → HSV color |
| `player.NametagAnchor` — player anchor | **CONVERT/AUGMENT** | → YOLO `nametag` class; template kept as transition fallback |
| `detection.detect_character_on_minimap` — minimap char fallback | **KEEP** | template (cheap insurance; primary is color) |
| `fish.scan` — template mob detector | **LEAVE** | selectable alternative; migrate maps to `mob_yolo` opportunistically |
| `monsters.detect_dragons` — template dragon counter | **LEAVE** | dead in the live loop (uses `detect_dragons_yolo`); optional separate cleanup |
| `calibrate_live` / `full_sense` / `map_platforms` / `live_check` | **LEAVE** | offline diagnostic tools |
| `make_lie_check_data` / `capture_*` | **LEAVE** | data-gen / labeling tooling (template is the right tool) |
| `detect_rune` / `detect_task_arrow` / `detect_platforms_on_minimap` | **LEAVE** | legacy `auto_train.py` path, confirmed out of scope |

Three conversions. Everything else is intentionally untouched.

## Conversion 1 — lie-check popups → YOLO primary

Today `detect_lie_check_yolo` runs as an **additive backstop** beside the templates in
`lie_check_full_tick` (see `2026-09-06-yolo-lie-check-detector-design.md`). This conversion
promotes YOLO to the decision-maker and retires the brittle, per-map template pairs
(`monster_instr_box`, `monster_warn_box`, `monster_instr_temple`, `monster_warn_temple`)
plus their `LIE_CHECK_THRESHOLDS` / `LIE_CHECK_FRACTIONS` entries.

**Recall gate (mandatory, safety-first).** A miss = ban risk. The templates are NOT
deleted until YOLO recall is *measured* on the accumulated positives in
`datasets/lie_check/captures/` and shown to meet or beat the current template OR-coverage,
with the negative frames staying clear. Until that evidence exists, both run (as today).

- Retire order: monster templates first (the fragile, failing ones), because that is where
  YOLO's advantage is proven and the template floor is lowest.
- `curse` and `transparent_shape` templates may stay longer — they detect well on templates
  and are cheap; retire them only if the same recall evidence supports it.
- The alarm / notify / dump / near-miss paths are unchanged — YOLO already feeds the same
  `_full_alert`.

**Result:** removes the ~0.5 s multi-scale monster match from the safety tick and the
per-map template maintenance, with no recall regression.

## Conversion 2 — enemy minimap dots → HSV color

`detection.detect_red_dots` (used by `recovery._enemy_dots`, the another-player safety
check) becomes an HSV color detector mirroring `_char_color_from_mm`:

- New `detect_red_dots_color(minimap_bgr)` → list of `(cx, cy)` centers: threshold the
  minimap crop to the red-dot HSV signature, connected-components, size-filter, return
  centers. No templates, no `assets/minimap_other_character/`.
- Same output shape as `detect_red_dots`, so `_enemy_dots` and `get_enemy` are unchanged.
- Reuse the phantom-rejection posture already proven for the yellow dot: the another-player
  check is presence-only (any red dot in the minimap band), so it needs a size floor to
  reject stray red pixels but no temporal tracking.

**Gate:** tune the HSV bounds against saved frames with and without other players; confirm
it fires on real red dots and stays clear on solo frames, matching current behavior.

## Conversion 3 — nametag → YOLO class (augmenting the player anchor)

This is the largest of the three and carries a re-labeling cost; sequence it last.

**Why.** The YOLO player anchor keys on the red HP bar above the head; recall is ~0.7
because attack VFX occludes the bar (`mob_detect.YoloPlayerAnchor` docstring). The nametag
sits *below* the feet and survives exactly those frames. Fusing a learned nametag signal
into the anchor raises effective recall against the dominant (occlusion) failure. YOLO
learns the nametag **chrome** (label box + text style below the sprite), not the specific
name — so it is name-agnostic, unlike the current per-character template that
`capture_nametag.py` must re-capture when the name/title changes.

**Design.**

- Add a 4th class `nametag` (id 3) to `mob_yolo` alongside `fishhouse`/`goby`/`player`.
- `mob_detect.yolo_detect` returns nametag boxes in addition to mobs + player from the
  **same single inference**.
- `YoloPlayerAnchor` fuses the two signals: **prefer the HP-bar box** (precise head → known
  `foot_offset`); when it is absent, **fall back to the nametag box** (its own
  bottom→feet offset), still within one predict() per frame.

**Catch A — disambiguation ("which nametag is mine?").** The template is name-*specific*,
so it only ever matched this character. A generic `nametag` class detects *every* player's
nametag, so the anchor must pick the right one: nearest to the HP-bar box when both exist,
else nearest to the last known position — the same `_pick_player` pattern already used for
the player class, with the same `max_jump` phantom guard.

- *Bonus (noted, not scoped):* the other-player nametags this surfaces give an on-screen
  another-player signal complementing the minimap red-dot check. Out of scope here.

**Catch B — re-labeling cost.** Adding a class means every existing `mob_yolo` training
frame that shows a nametag must gain that box, or those frames teach "nametag = background"
and depress recall. The character is in most frames, so this is re-annotating most of the
dataset. This is the bulk of the effort; training itself is the usual ~25 min GPU.

- Include a few **other players'** nametags in the labeled set so the model learns the
  generic chrome, not just this character's name.

**Gate + fallback.** Keep the template `NametagAnchor` path (and the `nametag_fallback`
map config) as the transition fallback until the YOLO nametag recall is measured. Do not
delete the working anchor before the replacement is proven — same evidence-gate as
Conversion 1.

## Data flow (after all three)

```
capture() ─▶ mob_yolo.predict  ──▶ mobs
                                 ├▶ player (HP bar)  ─┐
                                 └▶ nametag ──────────┴▶ YoloPlayerAnchor (HP-bar preferred,
                                                          nametag fallback, nearest-to-prior)
                                                          └ template NametagAnchor (transition fallback)

capture() ─▶ lie_check_full_tick ─▶ detect_lie_check_yolo (primary)
                                    └ templates (curse/transparent retained; monster retired post-gate)

capture_minimap() ─▶ detect_red_dots_color ─▶ another-player presence
```

## Error handling

- All YOLO paths return `[]`/`None` when the model file is absent or predict throws (mirror
  the existing `detect_lie_check_yolo` / `mob_detect` posture); the safety loop never sees an
  exception.
- Until each recall gate passes, the corresponding template path stays live, so no
  conversion can regress current behavior even if its model is weak or missing.

## Testing

- **Conversion 1:** existing lie-check YOLO tests cover the wrapper + integration. Add a
  recall-measurement script (not CI) over `datasets/lie_check/captures/` that reports YOLO
  recall vs the retired monster templates before deletion.
- **Conversion 2:** `detect_red_dots_color` returns centers for a synthesized red-dot crop
  and `[]` for a solo minimap; size floor rejects a 1–2 px speck. Parity check against
  `detect_red_dots` on saved frames.
- **Conversion 3:** `yolo_detect` splits a stubbed 4-class result into mobs/player/nametag;
  `YoloPlayerAnchor` returns the HP-bar position when both present, the nametag position when
  the HP bar is absent, and `None` when neither; disambiguation picks the nametag nearest the
  prior position and rejects a `max_jump` leap. Validation (manual): held-out frames show the
  nametag class detected above the chosen conf and the fused anchor's recall beats HP-bar-only.

## Rollout / risk

- Each conversion is independently shippable and independently gated; do them in order
  (1 → 2 → 3), lightest first.
- Conversion 3's re-labeling is the schedule risk. It is optional to the other two — the
  bot keeps its current template nametag fallback until the YOLO nametag is proven, so a
  partial migration is a safe resting state.
- No unified-model refactor and no interface rewrite: consistency is served by the written
  decision rule above, not by forcing one algorithm across regions.

## Related

`[[lie-check-monster-detection]]`, `[[yolo-dragon-labeling-state]]`,
`[[water-map-support]]` (memory); `2026-09-06-yolo-lie-check-detector-design.md`,
`2026-08-12-yolo-dragon-detection-design.md` (YOLO patterns this follows).
