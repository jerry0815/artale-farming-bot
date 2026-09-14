# Panel Buff Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a panel UI to edit a config's buff schedule (keys + interval), saving to the selected character (else map) file, applied on the next farm Start.

**Architecture:** Four pure helpers in `panel.py` (`normalize_buffs`, `apply_buffs`, `validate_groups`, `save_buffs`) do all the buff logic and are unit-tested with no server. Two GET endpoints (`/buffs` read, `/buffs_save` write) wrap them with a config-path whitelist. A "Buffs" card in the existing `PAGE` reads the current `#mapsel`/`#charsel` selection and renders editable rows (interval shown in minutes).

**Tech Stack:** Python stdlib (`http.server`, `json`), pytest, vanilla JS/HTML in the `PAGE` string. No new dependencies.

## Global Constraints

- Buffs stored on disk as `interval_secs` (int seconds); the UI shows/accepts **minutes** (minutes×60).
- Save writes `buff_groups` form and removes `buff_keys` / `buff_interval_secs`; every other config field is preserved.
- JSON written with `json.dump(..., indent=2, ensure_ascii=False)` + trailing newline (the configs contain CJK notes that must NOT be `\uXXXX`-escaped).
- The `file` a save may write is whitelisted to existing `maps/*.json` + `chars/*.json` paths (from `list_maps()`/`list_chars()`); reject anything else.
- Endpoints are GET and never raise a 500: on any error return `{"ok": false, "msg": <str>}` (matches the existing panel pattern).
- `panel.py` top-level imports stay stdlib-only (it is imported by tests; `recovery`/torch stay lazily imported inside functions).

---

### Task 1: `normalize_buffs` — read either schema into a group list

**Files:**
- Modify: `panel.py` (add module-level function near `list_chars`, ~line 468)
- Test: `tests/test_panel_buffs.py` (create)

**Interfaces:**
- Produces: `normalize_buffs(cfg: dict) -> list[dict]`, each item `{"keys": list[str], "interval_secs": int}`. Empty list when the config has no buffs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_panel_buffs.py
import panel


def test_normalize_buffs_groups_schema():
    cfg = {"buff_groups": [{"keys": ["d"], "interval_secs": 300},
                           {"keys": ["g"], "interval_secs": 900}]}
    assert panel.normalize_buffs(cfg) == [
        {"keys": ["d"], "interval_secs": 300},
        {"keys": ["g"], "interval_secs": 900},
    ]


def test_normalize_buffs_simple_schema():
    cfg = {"buff_keys": ["a", "j"], "buff_interval_secs": 300}
    assert panel.normalize_buffs(cfg) == [{"keys": ["a", "j"], "interval_secs": 300}]


def test_normalize_buffs_simple_default_interval():
    # buff_keys with no interval -> default 300s
    assert panel.normalize_buffs({"buff_keys": ["a"]}) == [{"keys": ["a"], "interval_secs": 300}]


def test_normalize_buffs_none():
    assert panel.normalize_buffs({"name": "x"}) == []
    assert panel.normalize_buffs({"buff_groups": []}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_panel_buffs.py -q`
Expected: FAIL with `AttributeError: module 'panel' has no attribute 'normalize_buffs'`

- [ ] **Step 3: Write minimal implementation**

Add to `panel.py` after `list_chars` (~line 468):

```python
def normalize_buffs(cfg):
    """Collapse either buff schema into a list of {keys, interval_secs}. The map form
    is `buff_groups` (per-group interval); the character form is `buff_keys` +
    `buff_interval_secs` (one interval). Empty list if the config declares no buffs."""
    groups = cfg.get("buff_groups")
    if groups:
        return [{"keys": list(g.get("keys", [])),
                 "interval_secs": int(g.get("interval_secs", 300))} for g in groups]
    keys = cfg.get("buff_keys")
    if keys:
        return [{"keys": list(keys), "interval_secs": int(cfg.get("buff_interval_secs", 300))}]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_panel_buffs.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add panel.py tests/test_panel_buffs.py
git commit -m "feat(panel): normalize_buffs reads either buff schema"
```

---

### Task 2: `apply_buffs` + `validate_groups` — build the config to write, and guard input

**Files:**
- Modify: `panel.py` (add after `normalize_buffs`)
- Test: `tests/test_panel_buffs.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly.
- Produces:
  - `apply_buffs(cfg: dict, groups: list[dict]) -> dict` — copy of `cfg` with `buff_groups` set from `groups` and `buff_keys`/`buff_interval_secs` removed; all other keys preserved (order preserved).
  - `validate_groups(groups) -> tuple[bool, str]` — `(True, "ok")` or `(False, reason)`. An empty list is valid (clears buffs); a group with no non-empty key, or a non-positive/non-int interval, is invalid.

- [ ] **Step 1: Write the failing test**

```python
def test_apply_buffs_sets_groups_and_drops_simple():
    cfg = {"name": "archer1", "attack_key": "x", "buff_keys": ["a", "j"],
           "buff_interval_secs": 300, "buff_settle_secs": 0.6}
    out = panel.apply_buffs(cfg, [{"keys": ["a", "j", "3"], "interval_secs": 300}])
    assert out["buff_groups"] == [{"keys": ["a", "j", "3"], "interval_secs": 300}]
    assert "buff_keys" not in out and "buff_interval_secs" not in out
    assert out["name"] == "archer1" and out["buff_settle_secs"] == 0.6   # others preserved
    assert cfg["buff_keys"] == ["a", "j"]                                # input not mutated


def test_apply_buffs_replaces_existing_groups():
    cfg = {"buff_groups": [{"keys": ["d"], "interval_secs": 300}], "platform_half": 60}
    out = panel.apply_buffs(cfg, [{"keys": ["3"], "interval_secs": 300}])
    assert out["buff_groups"] == [{"keys": ["3"], "interval_secs": 300}]
    assert out["platform_half"] == 60


def test_validate_groups():
    assert panel.validate_groups([{"keys": ["a"], "interval_secs": 300}]) == (True, "ok")
    assert panel.validate_groups([]) == (True, "ok")                       # clearing is allowed
    assert panel.validate_groups([{"keys": [], "interval_secs": 300}])[0] is False
    assert panel.validate_groups([{"keys": ["a"], "interval_secs": 0}])[0] is False
    assert panel.validate_groups([{"keys": ["a"], "interval_secs": "x"}])[0] is False
    assert panel.validate_groups("nope")[0] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_panel_buffs.py -q`
Expected: FAIL (`apply_buffs` / `validate_groups` not defined)

- [ ] **Step 3: Write minimal implementation**

Add to `panel.py` after `normalize_buffs`:

```python
def apply_buffs(cfg, groups):
    """Return a copy of cfg with buff_groups set from `groups` and the simple-schema
    keys removed. Preserves every other field and key order (assumes groups already
    validated)."""
    out = dict(cfg)
    out["buff_groups"] = [{"keys": list(g["keys"]), "interval_secs": int(g["interval_secs"])}
                          for g in groups]
    out.pop("buff_keys", None)
    out.pop("buff_interval_secs", None)
    return out


def validate_groups(groups):
    """(ok, msg) for a posted buff group list. Empty list is valid (clears buffs)."""
    if not isinstance(groups, list):
        return False, "groups must be a list"
    for g in groups:
        keys = g.get("keys") if isinstance(g, dict) else None
        if not isinstance(keys, list) or not [k for k in keys if str(k).strip()]:
            return False, "a buff row has no keys"
        try:
            iv = int(g.get("interval_secs"))
        except (TypeError, ValueError):
            return False, "interval must be a whole number of seconds"
        if iv <= 0:
            return False, "interval must be > 0"
    return True, "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_panel_buffs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add panel.py tests/test_panel_buffs.py
git commit -m "feat(panel): apply_buffs + validate_groups for buff writes"
```

---

### Task 3: `save_buffs` — whitelist, validate, read-modify-write

**Files:**
- Modify: `panel.py` (add after `validate_groups`)
- Test: `tests/test_panel_buffs.py`

**Interfaces:**
- Consumes: `apply_buffs`, `validate_groups` (Task 2).
- Produces: `save_buffs(file: str, groups: list, allowed_paths: list[str]) -> tuple[bool, str]`. Rejects a `file` not in `allowed_paths` or missing on disk; else validates groups and writes the file (`indent=2, ensure_ascii=False`, trailing newline). Returns `(ok, msg)`.

- [ ] **Step 1: Write the failing test**

```python
import json


def test_save_buffs_writes_and_preserves(tmp_path):
    p = tmp_path / "archer1.json"
    p.write_text(json.dumps({"name": "archer1", "buff_keys": ["a"],
                             "buff_interval_secs": 300, "note": "弓神"}), encoding="utf-8")
    ok, msg = panel.save_buffs(str(p), [{"keys": ["a", "3"], "interval_secs": 300}],
                               allowed_paths=[str(p)])
    assert ok, msg
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["buff_groups"] == [{"keys": ["a", "3"], "interval_secs": 300}]
    assert "buff_keys" not in data and data["note"] == "弓神"     # preserved, unescaped
    assert "\\u" not in p.read_text(encoding="utf-8")                     # CJK not escaped on disk


def test_save_buffs_rejects_unlisted_file(tmp_path):
    p = tmp_path / "evil.json"
    p.write_text("{}", encoding="utf-8")
    ok, msg = panel.save_buffs(str(p), [{"keys": ["a"], "interval_secs": 300}], allowed_paths=[])
    assert ok is False and "config" in msg


def test_save_buffs_rejects_bad_groups(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{}", encoding="utf-8")
    ok, msg = panel.save_buffs(str(p), [{"keys": [], "interval_secs": 300}], allowed_paths=[str(p)])
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_panel_buffs.py -q`
Expected: FAIL (`save_buffs` not defined)

- [ ] **Step 3: Write minimal implementation**

Add to `panel.py` after `validate_groups`:

```python
def save_buffs(file, groups, allowed_paths):
    """Whitelist `file` against allowed_paths, validate `groups`, then read-modify-write
    the config's buffs. (ok, msg)."""
    import os
    norm = os.path.normpath(file or "")
    if norm not in {os.path.normpath(p) for p in allowed_paths}:
        return False, f"not a config file: {file}"
    if not os.path.isfile(norm):
        return False, f"missing config: {file}"
    ok, msg = validate_groups(groups)
    if not ok:
        return False, msg
    with open(norm, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg = apply_buffs(cfg, groups)
    with open(norm, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return True, f"saved buffs to {file}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_panel_buffs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add panel.py tests/test_panel_buffs.py
git commit -m "feat(panel): save_buffs whitelists + writes config buffs"
```

---

### Task 4: Wire the `/buffs` (read) and `/buffs_save` (write) endpoints

**Files:**
- Modify: `panel.py` — `do_GET` (insert after the `/chars` branch, ~line 829)

**Interfaces:**
- Consumes: `normalize_buffs` (Task 1), `save_buffs` (Task 3), `list_maps`, `list_chars`.
- Produces: HTTP `GET /buffs?file=<path>` → `{ok, file, groups}`; `GET /buffs_save?file=<path>&groups=<json>` → `{ok, msg}`. Both whitelist `file`.

- [ ] **Step 1: Add the read + write branches**

In `panel.py` `do_GET`, immediately after the `if parsed.path == "/chars": ...` branch, insert:

```python
            if parsed.path in ("/buffs", "/buffs_save"):
                import os
                file = q.get("file", [None])[0]
                allowed = [m["path"] for m in list_maps()] + list(list_chars())
                _ok_path = file and os.path.normpath(file) in {os.path.normpath(p) for p in allowed}
                if parsed.path == "/buffs":
                    if not _ok_path:
                        return self._send(200, json.dumps({"ok": False, "msg": "not a config file"}))
                    try:
                        with open(file, encoding="utf-8") as fh:
                            cfg = json.load(fh)
                        return self._send(200, json.dumps(
                            {"ok": True, "file": file, "groups": normalize_buffs(cfg)}))
                    except Exception as e:
                        return self._send(200, json.dumps({"ok": False, "msg": str(e)}))
                try:
                    groups = json.loads(q.get("groups", ["[]"])[0])
                except Exception as e:
                    return self._send(200, json.dumps({"ok": False, "msg": f"bad groups: {e}"}))
                ok, msg = save_buffs(file, groups, allowed)
                return self._send(200, json.dumps({"ok": ok, "msg": msg}))
```

- [ ] **Step 2: Smoke-test the endpoints against a temp server**

Run:

```bash
python - <<'PY'
import threading, time, json, urllib.request, urllib.parse, http.server, socketserver
import panel
h = panel.make_handler(type("C", (), {})())
srv = socketserver.ThreadingTCPServer(("127.0.0.1", 8099), h)
threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
def get(p): return json.load(urllib.request.urlopen("http://127.0.0.1:8099" + p))
print("read:", get("/buffs?file=" + urllib.parse.quote("maps/deep_sea_2.json")))
print("reject:", get("/buffs?file=" + urllib.parse.quote("panel.py")))
srv.shutdown()
PY
```
Expected: `read:` prints `{'ok': True, 'file': 'maps/deep_sea_2.json', 'groups': [...]}` including the `3` group; `reject:` prints `{'ok': False, 'msg': 'not a config file'}`.

- [ ] **Step 3: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS (all).

- [ ] **Step 4: Commit**

```bash
git add panel.py
git commit -m "feat(panel): /buffs read + /buffs_save write endpoints"
```

---

### Task 5: Buffs card in the panel UI

**Files:**
- Modify: `panel.py` `PAGE` — add a fieldset in the Setup tab (`#tab-setup`, after the "Train detection" fieldset ~line 583) and JS functions (near `loadChars`, ~line 720).

**Interfaces:**
- Consumes: `/buffs`, `/buffs_save` (Task 4); existing `#mapsel`/`#charsel` (their `.value` is the config path, `""` for char = map default).

- [ ] **Step 1: Add the HTML card**

In `PAGE`, after the `</fieldset>` that closes "Train detection" (before the `</div>` closing `#tab-setup`, ~line 583), insert:

```html
   <fieldset><legend>Buffs (edits the selected character, else the map)</legend>
     <div id=bufffile class=sub style="font-family:ui-monospace,monospace">–</div>
     <div id=buffrows></div>
     <div class=row style="margin-top:6px">
       <button onclick="addBuffRow()">＋ Add buff</button>
       <button class=go onclick="saveBuffs()">💾 Save buffs</button>
       <button onclick="loadBuffs()">↻</button>
     </div>
     <div id=buffstat class=sub></div>
   </fieldset>
```

- [ ] **Step 2: Add the JS**

In `PAGE`, after the `loadChars` function (~line 720), insert:

```javascript
 function buffTarget(){ return document.getElementById('charsel').value || document.getElementById('mapsel').value; }
 function addBuffRow(keys, mins){
   const d=document.createElement('div'); d.className='row buffrow';
   d.innerHTML='keys <input class=bkeys placeholder="a,j" style="width:120px"> every '
     +'<input class=bmin type=number min=1 step=1 style="width:60px"> min '
     +'<button onclick="this.parentNode.remove()">✕</button>';
   d.querySelector('.bkeys').value=(keys||[]).join(',');
   d.querySelector('.bmin').value=(mins!=null?mins:5);
   document.getElementById('buffrows').appendChild(d);
 }
 async function loadBuffs(){
   const rows=document.getElementById('buffrows'); rows.innerHTML='';
   document.getElementById('buffstat').textContent='';
   const file=buffTarget();
   if(!file){ document.getElementById('bufffile').textContent='(select a map or character first)'; return; }
   const j=await (await fetch('/buffs?'+new URLSearchParams({file}))).json();
   if(!j.ok){ document.getElementById('bufffile').textContent='error: '+j.msg; return; }
   document.getElementById('bufffile').textContent='editing → '+j.file;
   for(const g of j.groups){ addBuffRow(g.keys, Math.round(g.interval_secs/60)); }
   if(!j.groups.length){ addBuffRow([], 5); }
 }
 async function saveBuffs(){
   const file=buffTarget();
   if(!file){ alert('select a map or character first'); return; }
   const groups=[];
   for(const r of document.querySelectorAll('#buffrows .buffrow')){
     const keys=r.querySelector('.bkeys').value.split(',').map(s=>s.trim()).filter(Boolean);
     const mins=parseFloat(r.querySelector('.bmin').value);
     if(!keys.length || !(mins>0)) continue;
     groups.push({keys, interval_secs: Math.round(mins*60)});
   }
   const j=await (await fetch('/buffs_save?'+new URLSearchParams({file, groups: JSON.stringify(groups)}))).json();
   const s=document.getElementById('buffstat'); s.textContent=j.msg; s.style.color=j.ok?'#7d7':'#f77';
 }
```

- [ ] **Step 3: Refresh buffs when the selection changes**

In `PAGE`, find `loadMaps()` and `loadChars()` calls in the init/boot code (search for `loadMaps()` invocation near the bottom `poll`/boot section). After the map/char selects are first populated, add a change wiring. Insert this once in the boot sequence (after `loadChars()` is called at startup):

```javascript
 document.getElementById('mapsel').addEventListener('change', loadBuffs);
 document.getElementById('charsel').addEventListener('change', loadBuffs);
 loadBuffs();
```

- [ ] **Step 4: Manual verification**

Run: `python panel.py --open`
Then:
1. Open the **Setup** tab → the Buffs card shows `editing → maps/deep_sea_2.json` with rows `d / 5`, `3 / 5`, `g / 15`.
2. Pick `archer1` in the character dropdown → card switches to `editing → chars/archer1.json`, rows `a,j / 5`.
3. Add a row `3 / 5`, click **Save buffs** → status turns green `saved buffs to chars/archer1.json`.
4. Confirm `chars/archer1.json` now has `buff_groups` with `["a","j"]` and `["3"]`, no `buff_keys`, and `buff_settle_secs` still present.
5. Start the farm with `archer1` → the log shows `[water] buff -> press ['3'] (next in 300s)`.

- [ ] **Step 5: Commit**

```bash
git add panel.py
git commit -m "feat(panel): Buffs editor card in the Setup tab"
```

---

## Self-Review

- **Spec coverage:** target resolution (char-else-map) → Task 5 `buffTarget()` + Task 4 whitelist; two schemas normalized → Task 1; save-to-file/next-start (no live wiring) → Tasks 3–5 write only; whitelist + validation + preserve-fields + CJK-safe write → Tasks 2–3; UI minutes ↔ secs → Task 5; unit tests for the pure helpers → Tasks 1–3; manual verify → Task 5. All spec sections covered.
- **Placeholder scan:** no TBD/TODO; every code step has full code.
- **Type consistency:** group shape `{"keys": list[str], "interval_secs": int}` is identical across `normalize_buffs`, `apply_buffs`, `validate_groups`, `save_buffs`, and the JS. `save_buffs(file, groups, allowed_paths)` signature matches its call in Task 4.
