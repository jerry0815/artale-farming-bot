# Per-Character Config (F2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the same map be farmed by different characters, each supplying its own attack/buff keys via a `chars/<name>.json` file selected in the panel.

**Architecture:** A character config is a small JSON file. A pure `apply_character(map_cfg, char_cfg)` helper overlays the character's key fields onto the loaded map config (precedence character → map → code default). `farming_loop_water` applies it once at the top, so no downstream code changes. The panel gets a character dropdown that passes the chosen file through to the loop.

**Tech Stack:** Python 3.11, stdlib `json`, pytest. The panel is a stdlib `http.server` + inline HTML string in `panel.py`.

## Global Constraints

- Character override allow-list is exactly: `attack_key`, `attack_keys`, `buff_keys`, `buff_interval_secs`, `buff_settle_secs`. No other map field may be overwritten by a character.
- Precedence is **character → map → code default**. A field absent (or `None`) in the character file leaves the map's value untouched.
- `char = None` / no character selected MUST reproduce today's behavior exactly.
- Scope: the **water loop** (`farming_loop_water`) and the panel only. `farming_loop_nav` (hardcoded keys) is out of scope for this increment.
- Follow existing patterns: `watermap.load_map` is the model for loading; the panel's existing `farm` action / `list_maps` are the models for plumbing.

---

### Task 1: `apply_character` + `load_char` helpers

**Files:**
- Modify: `recovery.py` (add two module-level functions + a `CHAR_FIELDS` constant, near the other water helpers — e.g. just above `farming_loop_water`)
- Create: `chars/archer1.json`
- Test: `tests/test_character.py`

**Interfaces:**
- Produces:
  - `CHAR_FIELDS = ("attack_key", "attack_keys", "buff_keys", "buff_interval_secs", "buff_settle_secs")`
  - `load_char(path)` → dict. Accepts a path string (reads JSON, UTF-8) or a dict (returned as-is).
  - `apply_character(map_cfg: dict, char_cfg: dict | None)` → dict. Returns a shallow copy of `map_cfg` with each present allow-listed field from `char_cfg` overriding it; returns `map_cfg` unchanged when `char_cfg` is falsy.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_character.py
import json
import recovery


def test_apply_character_overrides_present_fields():
    m = {"attack_key": "c", "buff_keys": [], "detector": "mob_yolo"}
    c = {"attack_key": "x", "buff_keys": ["a", "j"]}
    out = recovery.apply_character(m, c)
    assert out["attack_key"] == "x"
    assert out["buff_keys"] == ["a", "j"]


def test_apply_character_keeps_map_value_for_absent_fields():
    m = {"attack_key": "c", "buff_interval_secs": 300}
    c = {"attack_key": "x"}                       # no buff_interval_secs
    out = recovery.apply_character(m, c)
    assert out["buff_interval_secs"] == 300       # map value preserved


def test_apply_character_none_char_returns_map_unchanged():
    m = {"attack_key": "c"}
    assert recovery.apply_character(m, None) == m


def test_apply_character_ignores_none_valued_char_fields():
    m = {"attack_key": "c"}
    c = {"attack_key": None}                       # explicit null -> not an override
    assert recovery.apply_character(m, c)["attack_key"] == "c"


def test_apply_character_never_touches_non_allowlisted_fields():
    m = {"attack_key": "c", "detector": "mob_yolo", "farm_nodes": ["P1"]}
    c = {"attack_key": "x", "detector": "HACKED", "farm_nodes": []}   # non-allowlisted
    out = recovery.apply_character(m, c)
    assert out["detector"] == "mob_yolo"          # untouched
    assert out["farm_nodes"] == ["P1"]            # untouched


def test_apply_character_does_not_mutate_inputs():
    m = {"attack_key": "c"}
    recovery.apply_character(m, {"attack_key": "x"})
    assert m["attack_key"] == "c"                  # original map unchanged


def test_load_char_reads_file(tmp_path):
    p = tmp_path / "archer1.json"
    p.write_text(json.dumps({"name": "archer1", "attack_key": "x"}), encoding="utf-8")
    assert recovery.load_char(str(p))["attack_key"] == "x"


def test_load_char_passthrough_dict():
    d = {"attack_key": "x"}
    assert recovery.load_char(d) is d
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_character.py -q`
Expected: FAIL with `AttributeError: module 'recovery' has no attribute 'apply_character'`

- [ ] **Step 3: Add the helpers to `recovery.py`**

Place just above `def farming_loop_water(`:

```python
CHAR_FIELDS = ("attack_key", "attack_keys", "buff_keys",
               "buff_interval_secs", "buff_settle_secs")


def load_char(path):
    """Load a chars/<name>.json character config. `path` may be a path string (read as
    UTF-8 JSON) or an already-loaded dict (returned unchanged)."""
    if isinstance(path, dict):
        return path
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_character(map_cfg, char_cfg):
    """Overlay a character's key settings onto a map config. Returns a SHALLOW COPY of
    `map_cfg` with each present CHAR_FIELDS value from `char_cfg` overriding the map's
    (precedence character -> map -> code default). A field absent or None in `char_cfg`
    leaves the map's value untouched. `char_cfg` falsy -> `map_cfg` returned unchanged."""
    if not char_cfg:
        return map_cfg
    merged = dict(map_cfg)
    for k in CHAR_FIELDS:
        if char_cfg.get(k) is not None:
            merged[k] = char_cfg[k]
    return merged
```

- [ ] **Step 4: Create the example character file**

```json
// chars/archer1.json
{
  "name": "archer1",
  "attack_key": "x",
  "attack_keys": { "fishhouse": "x", "goby": "z" },
  "buff_keys": ["a", "j"],
  "buff_interval_secs": 240,
  "buff_settle_secs": 0.6
}
```
(JSON has no comments — omit the `// chars/archer1.json` line; it only names the file here.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_character.py -q`
Expected: PASS (8 passed)

- [ ] **Step 6: Commit**

```bash
git add recovery.py tests/test_character.py chars/archer1.json
git commit -m "feat(char): apply_character/load_char + example character file"
```

---

### Task 2: apply the character in `farming_loop_water`

**Files:**
- Modify: `recovery.py` — `farming_loop_water` signature + the map-load block (currently around `recovery.py:2049`–`2059`)
- Test: `tests/test_character.py` (add one integration-style test)

**Interfaces:**
- Consumes: `load_char`, `apply_character` (Task 1).
- Produces: `farming_loop_water(map_cfg, char=None, enemy_check=None, panic=None, ...)` — new second-positional-by-keyword `char` param (a path string, a dict, or None). When set, the loaded `map_cfg` is replaced by `apply_character(map_cfg, load_char(char))` before any `map_cfg.get(...)` read.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_character.py  (append)
def test_farming_loop_water_merges_char_before_reading(monkeypatch, tmp_path):
    # Prove the character's attack_key reaches the loop: stub focus() to bail right after the
    # merge, and capture the attack_key the loop resolved.
    import recovery
    seen = {}
    m = {"detector": "time", "farm_nodes": ["P1"], "nodes": [{"name": "P1", "band": [0, 4, 0, 4]}],
         "attack_key": "c"}
    monkeypatch.setattr(recovery.watermap, "load_map", lambda p: dict(m))
    monkeypatch.setattr(recovery.watermap, "minimap_crop", lambda cfg: (20, 171, 229, 259))
    monkeypatch.setattr(recovery.watermap, "node_centers", lambda cfg: {"P1": (2, 2)})
    monkeypatch.setattr(recovery.watermap, "swim_tol", lambda cfg: (10, 8, 16))
    monkeypatch.setattr(recovery, "set_minimap", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "set_enemy_threshold", lambda *a, **k: None)

    def fake_focus():
        seen["attack_key"] = recovery._LAST_WATER_ATTACK_KEY[0]
        return False                                  # bail out of the loop immediately

    monkeypatch.setattr(recovery, "focus", fake_focus)
    ch = tmp_path / "c.json"
    ch.write_text('{"attack_key": "x"}', encoding="utf-8")
    recovery.farming_loop_water("m.json", char=str(ch))
    assert seen["attack_key"] == "x"
```

Note: this test relies on a tiny observability hook `_LAST_WATER_ATTACK_KEY` set right
after the merge (added in Step 3) so we can assert the merged value without running the
full loop.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_character.py::test_farming_loop_water_merges_char_before_reading -q`
Expected: FAIL (`TypeError: farming_loop_water() got an unexpected keyword argument 'char'`)

- [ ] **Step 3: Add the `char` param + merge + observability hook**

In `recovery.py`, module level (near other module state, e.g. by `STATUS`):

```python
_LAST_WATER_ATTACK_KEY = [None]   # test/inspection hook: attack_key after character merge
```

Change the signature (add `char=None` as the second parameter):

```python
def farming_loop_water(map_cfg, char=None, enemy_check=None, panic=None,
                       stand_secs=(6, 8), break_every=(8 * 60, 15 * 60),
                       rest_range=(30, 120), skill_interval=(240, 300),
                       deplete_threshold=1, max_seconds=None):
```

Right after the existing map-load block:

```python
    if isinstance(map_cfg, str):
        map_cfg = watermap.load_map(map_cfg)
    if char is not None:                              # overlay this character's keys
        map_cfg = apply_character(map_cfg, load_char(char))
    _LAST_WATER_ATTACK_KEY[0] = map_cfg.get("attack_key", "c")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_character.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python -m pytest tests/ -q`
Expected: PASS (all prior tests still green; `char` defaults to None so existing callers are unchanged)

- [ ] **Step 6: Commit**

```bash
git add recovery.py tests/test_character.py
git commit -m "feat(char): farming_loop_water applies a selected character's keys"
```

---

### Task 3: panel character dropdown + plumbing

**Files:**
- Modify: `panel.py` — add `list_chars`, a character `<select>` in the HTML, and thread `char_path` through the `farm` action and its dispatch (`_build_actions`, the `_dispatch` GET handler, and the JS that posts the farm action)

**Interfaces:**
- Consumes: `recovery.farming_loop_water(map_cfg, char=..., ...)` (Task 2).
- Produces: `list_chars(chars_dir="chars")` → sorted list of char file paths (mirrors `list_maps`); `farm` action accepts an optional `char` path and forwards it to `farming_loop_water`.

- [ ] **Step 1: Write the failing test for `list_chars`**

```python
# tests/test_panel_chars.py
import panel


def test_list_chars_lists_json(tmp_path):
    (tmp_path / "archer1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "mage.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    out = panel.list_chars(str(tmp_path))
    names = [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in out]
    assert names == ["archer1.json", "mage.json"]     # sorted, .json only


def test_list_chars_missing_dir_returns_empty(tmp_path):
    assert panel.list_chars(str(tmp_path / "nope")) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_panel_chars.py -q`
Expected: FAIL (`AttributeError: module 'panel' has no attribute 'list_chars'`)

- [ ] **Step 3: Add `list_chars` to `panel.py`**

Mirror `list_maps` (find it near `panel.py:383`). Add beside it:

```python
def list_chars(chars_dir="chars"):
    """Character config files (chars/*.json), sorted. Empty if the dir is absent."""
    import os
    from glob import glob
    if not os.path.isdir(chars_dir):
        return []
    return sorted(glob(os.path.join(chars_dir, "*.json")))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_panel_chars.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Thread `char` through farm action → `controller.start` → `_dispatch`**

The panel routes farming as `_dispatch("start") → controller.start(mode, map_path) →
actions["farm"](map_path)`. Add `char` along that whole path.

(a) In `_build_actions`, change the `farm` action (`panel.py:217`):

```python
    def farm(map_path, char=None):
        """One farm entry: the selected map's `loop` field picks the loop -- 'nav' = the
        land dragon-nest loop (map geometry unused), else the water swim loop."""
        import watermap
        def _go():
            kb.pause = False
            cfg = watermap.load_map(map_path) if map_path else {}
            if cfg.get("loop") == "nav":
                recovery.farming_loop_nav(exp_check=None, enemy_check=_enemy_check, panic=_panic)
            else:
                recovery.farming_loop_water(map_path, char=char or None,
                                            enemy_check=_enemy_check, panic=_panic)
        _run_bg(_go)
```

(b) In `Controller.start` (`panel.py:110`), add the `char` param and forward it:

```python
    def start(self, mode, map_path=None, char=None):
        if self.mode != "idle":
            return False, f"busy ({self.mode})"
        if mode not in self._MODE_NAME:
            return False, f"unknown mode {mode}"
        if mode == "farm":                            # map-driven: the config's `loop` field
            if not map_path:                          # picks nav vs water (see the farm action)
                return False, "no map selected"
            self.mode = "farming"
            self.actions["farm"](map_path, char)
            return True, "farming"
        self.mode = self._MODE_NAME[mode]
        self.actions[mode]()
        return True, self.mode
```

(c) In the `_dispatch` handler's `start` branch (`panel.py:689`), pass the `char` query param:

```python
            if action == "start":
                return controller.start(q.get("mode", [""])[0],
                                        map_path=q.get("map", [None])[0],
                                        char=q.get("char", [None])[0])
```

- [ ] **Step 6: Add the `/chars` endpoint**

In `do_GET` (next to the `/maps` handler at `panel.py:666`):

```python
            if parsed.path == "/chars":
                return self._send(200, json.dumps(list_chars()))
```
`list_chars` returns a list of path strings; the JS renders each path's basename as the
label (Step 7).

- [ ] **Step 7: Add the dropdown + `loadChars()` + include `char` in the farm request (HTML/JS)**

(a) In the `PAGE` HTML, beside the map select (`<select id=mapsel></select>` at
`panel.py:455`), add:

```html
<select id=charsel></select>
```

(b) Add a `loadChars()` JS mirroring `loadMaps()` (which fetches `/maps` into `mapsel`):

```javascript
 async function loadChars(){
   const chars = await (await fetch('/chars')).json();
   const sel = document.getElementById('charsel');
   sel.innerHTML = '<option value="">(map default)</option>';
   for(const p of chars){
     const name = p.split(/[\\/]/).pop().replace(/\.json$/, '');
     const o=document.createElement('option'); o.value=p; o.textContent=name; sel.appendChild(o);
   }
 }
```
Call `loadChars()` wherever `loadMaps()` is already called on page load.

(c) In `startFarm()` (`panel.py:597`), add `char` to the request:

```javascript
 async function startFarm(){
   const map = document.getElementById('mapsel').value;
   if(!map){ alert('no map selected'); return; }
   const char = document.getElementById('charsel').value;
   const j = await (await fetch('/cmd?'+new URLSearchParams({action:'start',mode:'farm',map,char}))).json();
   if(!j.ok) alert(j.msg);
```
(An empty `char` value is sent as `char=` → `q.get("char",[None])[0]` yields `""`, and
`char or None` in the farm action makes it `None` = map default.)

- [ ] **Step 8: Manual smoke test**

Run the panel, pick a map + the `archer1` character, start a short farm, and confirm the
log prints the character's attack key in use (e.g. `[water] ... 'x' burst`). Stop it.

Run: `python panel.py` (or the project's usual panel launch), then exercise in the browser.
Expected: farming uses the character's `attack_key`; with `(map default)` it uses the map's.

- [ ] **Step 9: Run the full suite + commit**

```bash
python -m pytest tests/ -q
git add panel.py tests/test_panel_chars.py
git commit -m "feat(char): panel character dropdown selects per-character keys"
```

---

## Notes / Deferred

- `farming_loop_nav` (dragon loop) uses hardcoded keys (`'c'`, `'a'`, `'j'`, `'h'`) and takes no `map_cfg`; per-character support there needs those keys parameterized — a separate follow-up, intentionally out of this increment.
- F3 (per-mob attack skill) consumes `attack_keys` from the same character file and is a separate plan; the example `chars/archer1.json` already includes an `attack_keys` sample so F3 has data to read.
