# EXP Recorder Tab + Per-Character Config — Design

Date: 2026-09-04
Status: Approved (pending spec review)

Three features for the farming panel:
- **F1 — EXP Record tab** and **F2 — per-character config** are fully independent.
- **F3 — per-mob-class attack skill** builds on F2 (the mob→skill map lives in the
  character config), so F2 lands first.

---

## Feature 1 — EXP Record tab (human-farm reference)

### Purpose

Record EXP-gain sessions with **no bot movement**, so a human can farm manually
and capture a reference efficiency (exp/min) to compare against bot runs. Sessions
persist and are shown as a history table so human and bot rows sit side by side.

### Backend — `recovery.py`

- **`exp_record_loop(label="human", sleep=time.sleep)`** — modeled on `watch_loop`.
  Passive: it presses no movement keys. Each iteration runs the existing
  `make_exp_tracker` tick (OCR of the EXP bar → updates `STATUS['exp_per_min']`,
  `STATUS['exp_10min']`, `STATUS['exp_total']`) and `lie_check_tick()` for safety,
  then sleeps ~0.5s, until `STOP` is set. `STATUS['state']` = `"exp_record"` while
  running, back to `"idle"` on exit.
  - On start it captures `t_start`. `make_exp_tracker` already maintains the
    cumulative, anomaly-filtered gain and publishes it as `STATUS['exp_total']`, so
    the loop does NOT diff raw OCR numbers itself.
  - On exit (finally) it reads `STATUS['exp_total']` (the session's gained EXP) and
    `STATUS['exp_10min']`, computes duration, and appends a session row (below).
    A session where the tracker never produced a value (`exp_total` is 0/None from
    OCR failing) is still written with `exp_gained: null` so the failure is visible,
    not silently dropped.

- **`append_exp_session(row, path="logs/exp_sessions.jsonl")`** — append one JSON
  object per line (JSON Lines). Never raises (best-effort logging).

- **`read_exp_sessions(path="logs/exp_sessions.jsonl", limit=50)`** — return the
  last `limit` rows (newest first) as a list of dicts, for the UI. Missing file →
  `[]`.

**Session row schema**
```json
{
  "ts_start": "2026-09-04 16:30:00",
  "ts_end":   "2026-09-04 16:40:00",
  "duration_s": 600,
  "exp_gained": 2443341,
  "exp_per_min": 244334,
  "exp_10min": 2443341,
  "label": "human"
}
```
`exp_per_min` = `exp_gained / (duration_s/60)` computed at stop (independent of the
live tracker's running average, so a short session is still summarized). `exp_10min`
is the tracker's last-window value if available, else null.

### Controller — `panel.py`

- `Controller.exp_record_start(label)` — spawn `exp_record_loop(label)` in a
  background thread (same pattern as `watch`), after `STOP.clear()`.
- `Controller.exp_record_stop()` — set `STOP`; the loop's finally writes the row.
- Reuse existing pause/stop plumbing where it fits; a record session is stopped the
  same cooperative way as `watch`.

### UI — `panel.py` HTML

New **EXP** tab (`showTab('exp')`, button `tab-b-exp`) containing:
- A `label` text input (default `"human"`).
- **Start** / **Stop** buttons → `POST` the `exp_record_start` / `exp_record_stop`
  actions.
- A live readout of `STATUS.exp_per_min` and `STATUS.exp_total` (the panel already
  polls `STATUS`), plus an elapsed timer.
- A **history table** (time, duration, exp gained, exp/min, label), rendered from a
  new `GET` endpoint that returns `read_exp_sessions()`. Newest first.

### Out of scope (YAGNI)

- No auto-logging of bot farm runs into the table (manual record only, for now).
- No editing/deleting rows from the UI.
- No charts — a table is enough for a reference comparison.

---

## Feature 2 — Per-character config (same map, different characters)

### Purpose

The same map may be farmed by different characters whose **attack key and buff keys
differ**. Character settings should layer over the map so one character file is
reused across maps and one map across characters.

### Schema — `chars/<name>.json`

Only the fields a character owns (all optional; missing → fall back to map, then
code default):
```json
{
  "name": "archer1",
  "attack_key": "x",
  "attack_keys": { "fishhouse": "x", "goby": "z" },
  "buff_keys": ["a", "j"],
  "buff_interval_secs": 240,
  "buff_settle_secs": 0.6
}
```
`attack_keys` (optional) is the per-mob-class override used by Feature 3; when
absent, the single `attack_key` is used for every mob. One example file is
scaffolded during implementation.

### Merge — `recovery.py`

- **`load_char(path)`** — load a `chars/*.json` (mirrors `watermap.load_map`).
- **`apply_character(map_cfg, char_cfg)`** — return a shallow copy of `map_cfg` with
  the character's present fields overlaid for exactly this allow-list:
  `attack_key`, `attack_keys`, `buff_keys`, `buff_interval_secs`, `buff_settle_secs`.
  A field absent from `char_cfg` leaves the map's value untouched. Precedence:
  **character → map → code default**.
- Applied once at the top of `farming_loop_water` and `farming_loop_nav`, before
  the existing `map_cfg.get(...)` reads, so no downstream code changes. `char_cfg =
  None` → `map_cfg` returned unchanged (today's behavior).

### Selection — `panel.py`

- `list_chars(chars_dir="chars")` — mirror `list_maps`.
- A **character dropdown** beside the map dropdown (default "(map default)" = none).
- `Controller.start` / the `farm` action gains an optional `char_path`; it calls
  `load_char` + `apply_character` before running the loop.

### Out of scope (YAGNI)

- No in-UI creation/editing of character files (edit JSON by hand).
- Character overrides **keys only** — no movement timing, detector, or map geometry.

---

## Feature 3 — Per-mob-class attack skill

### Purpose

Default: one `attack_key` for every mob. Optionally, fire a **different skill per
mob class** (e.g. `fishhouse → x`, `goby → z`) at whichever mob is being targeted.
Lives in the character config (skills belong to the character).

### Detector — `mob_detect.py`

- `yolo_boxes(model, frame, roi, conf, imgsz, class_conf, with_class=False)` — new
  `with_class` flag. When True each detection is `(score, x, y, w, h, cls_name)`
  where `cls_name = model.names[cls]` (e.g. `"fishhouse"`, `"goby"`); when False it
  stays the current 5-tuple. Default False → every existing caller is unchanged.

### Detection-tuple contract

A detection is `(score, x, y, w, h)` **or** `(score, x, y, w, h, cls_name)`.
Consumers that need only geometry read `d[:5]`; the class (if any) is `d[5]`.
Template detectors (`fish.scan`) keep 5-tuples → their mobs use the fallback
`attack_key`. The four unpack sites in `approach_shoot`
([recovery.py:1718/1724/1731](recovery.py:1718)) and the one at
[recovery.py:2763](recovery.py:2763) are updated to slice `d[:5]` so both arities
work.

### `approach_shoot` — `recovery.py`

- New optional param `attack_keys=None` (the class→key map).
- `same_platform(dets)` carries the class through: returns `[(cx, feet, cls)]`.
- When a target mob is selected, the burst key is
  `attack_keys.get(cls, attack_key)` — the class's skill if mapped, else the default
  `attack_key`. `cls` may be `None` (template detector / class absent) → default.
- The `mm_bounds` "fire in place at edge" burst uses the same resolved key.

### Plumbing — `farming_loop_water`

- After `apply_character`, if `attack_keys` is set **and** the detector is
  `mob_yolo`, build `detect_fn` with `with_class=True`; otherwise keep the current
  5-tuple `detect_fn`. Pass `attack_keys=map_cfg.get("attack_keys")` into
  `approach_shoot`.
- `walk_shoot` / `water_shoot` are untouched (they use one `attack_key`); per-mob
  skill applies to the `approach` mode only, where individual mobs are targeted.

### Out of scope (YAGNI)

- Per-mob skill only in `approach` mode (not walk/water shoot).
- No per-mob attack *timing* / burst-count differences — just the key.

---

## Testing

- **Feature 1:** unit-test `append_exp_session` / `read_exp_sessions` round-trip
  (tmp path), and the session-summary math (`exp_per_min` from gained/duration,
  null-safe when OCR failed). `exp_record_loop` is a thin passive loop (like
  `watch_loop`, which is untested) — covered by the helper tests + manual run.
- **Feature 2:** unit-test `apply_character` — override present fields, leave absent
  ones as the map value, `None` char → unchanged map, and that only the allow-listed
  keys are touched (an unrelated map field is never overwritten).
- **Feature 3:** unit-test `approach_shoot` target-key selection with a fake detector
  returning 6-tuples — the burst key is the class's mapped key, falls back to
  `attack_key` for an unmapped/`None` class, and a 5-tuple detector still works
  (default key). The existing approach tests already cover the 5-tuple path.

## Files touched

- `recovery.py` — `exp_record_loop`, `append_exp_session`, `read_exp_sessions`,
  `load_char`, `apply_character`; merge calls in both farm loops; `approach_shoot`
  target-key selection + `attack_keys` plumbing; `detect_fn` build with
  `with_class`; `d[:5]` slicing at the detection unpack sites.
- `mob_detect.py` — `yolo_boxes(..., with_class=False)`.
- `panel.py` — controller actions, EXP tab + history endpoint, character dropdown +
  `char_path` plumbing.
- `chars/archer1.json` — one scaffolded example (incl. an `attack_keys` sample).
- `tests/` — helper, `apply_character`, and per-mob target-key tests.
