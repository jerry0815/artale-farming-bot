# Panel buff editor

**Date:** 2026-09-14
**Status:** Design approved, ready for implementation plan
**Scope:** A small in-panel UI to edit a config's buff schedule (keys + interval).
Buffs only — no other tunables, no live-apply.

## Goal

Add a "Buffs" card to `panel.py`'s control UI so buffs can be edited without hand-
editing JSON. Edits save to the config file and take effect on the next farm Start
(the running loop is untouched). The editor targets whatever config actually runs:
the selected character (`chars/*.json`) when one is chosen, else the selected map
(`maps/*.json`).

## Key context / constraints

- **Buff resolution is character-first.** `recovery.py` merges map + character config;
  a character's `buff_keys` REPLACES the map's `buff_groups` (recovery.py ~2657). So the
  buffs that fire depend on whether a character is selected. The editor must edit the file
  that actually applies — hence "whichever is selected".
- **Two on-disk schemas.** The loop reads `buff_groups` (list of `{keys, interval_secs}`,
  per-key interval) OR the simple `buff_keys` + `buff_interval_secs` (all keys, one
  interval). The map uses the first; the character uses the second. The loop and the
  char-merge both PREFER `buff_groups` when present.
- **Save-to-file, next-start** (chosen). No live plumbing: the loop captures its buff
  schedule at start, so writing the file mid-run is harmless and applies on the next Start.
- **Reuse the existing panel.** Same stdlib `http.server`, same `PAGE` string + JSON
  endpoints pattern (`/maps`, `/chars`, `/cmd`). No new dependencies.

## Architecture

```
panel PAGE (browser)
  Buffs card  ── GET /buffs?map=&char= ──▶ resolve target file, read + NORMALIZE buffs
     │                                      -> {file, groups:[{keys:[...],interval_secs}]}
     └── Save ── POST /buffs {file,groups} ─▶ validate -> read-modify-write that file
                                              (replace buff_groups, drop buff_keys/
                                               buff_interval_secs, preserve all else)
```

Two pure-ish helpers make the logic testable without the server:

- `normalize_buffs(cfg) -> [ {keys:[...], interval_secs:int}, ... ]` — collapses either
  schema into a group list (empty list if a config has no buffs).
- `apply_buffs(cfg, groups) -> new_cfg` — returns the config dict with `buff_groups` set
  from `groups` and `buff_keys` / `buff_interval_secs` removed, every other key preserved.

The endpoints do file I/O + target resolution around these; the browser does the form.

## Components

### 1. Target resolution (endpoint helper)
`resolve_buff_target(map_name, char_name) -> path`: `chars/<char>.json` when `char_name`
is non-empty, else `maps/<map>.json`. 404 if the file is missing.

### 2. `GET /buffs?map=&char=`
Resolve target → load JSON → `normalize_buffs` → return `{file: <relpath>, groups: [...]}`.

### 3. `POST /buffs`  (body `{file, groups}`)
- Validate: `file` is one of the known maps/chars paths (no arbitrary write); each group
  has ≥1 non-empty key and `interval_secs` an int > 0.
- Load the file, `apply_buffs`, write back with `json.dump(indent=2)`.
- Return `{ok, msg}`; on any error `{ok:false, msg}` (never a 500 that kills the panel).

### 4. UI card in `PAGE`
- Header: `Editing buffs -> <file>` reflecting the current map/char dropdowns; refreshes on
  change and on load via `GET /buffs`.
- One row per group: `keys` text (comma-separated, e.g. `a,j`) · `every` number (MINUTES)
  · remove (✕). `+ Add buff` appends a blank row. `Save buffs` POSTs.
- Minutes in the UI, `interval_secs` on disk (`minutes*60`, rounded to int); display is
  `interval_secs/60`.

## Data flow

1. Panel load / dropdown change → `GET /buffs?map=&char=` → render rows (minutes).
2. User edits rows / adds / removes → `Save buffs` → `POST /buffs`.
3. Server writes the target file. Next farm Start reads the new buffs.

## Error handling / safety

- Whitelist the writable `file` to existing `maps/*.json` + `chars/*.json` (reject anything
  else) so the endpoint can't write outside config.
- Reject empty key sets and non-positive intervals with a clear message; no partial writes.
- Read-modify-write preserves all unrelated fields; files are git-tracked (no backup).
- Writing `buff_groups` to a character that used `buff_keys` is safe — both loop and merge
  prefer `buff_groups`; dropping `buff_keys` keeps one source of truth.

## Testing

- Unit (pure, no server): `normalize_buffs` for the `buff_groups` schema, the
  `buff_keys`+`buff_interval_secs` schema, and the no-buffs case; `apply_buffs` sets
  `buff_groups` and removes the simple keys while preserving other fields;
  `resolve_buff_target` map-vs-char selection.
- Manual: edit archer1's buffs in the panel, Save, confirm the JSON and that the next
  Start logs `[water] buff -> press [...]` with the new schedule.

## Out of scope (YAGNI)

- Live-apply to a running loop.
- Editing `buff_settle_secs` / `buff_gap_secs` or any non-buff tunable.
- Creating new map/char files (edits existing only).
- Per-key (vs per-group) interval UI beyond what `buff_groups` already expresses.
