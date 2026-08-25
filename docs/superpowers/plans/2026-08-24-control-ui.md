# Control UI + Easy lie_check Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** A local web control panel to start/pause/stop farming, recover, record routes (UI-driven node marks), and run a one-click passive lie_check watch loop — replacing the CLI-only workflow.

**Architecture:** The bot runs in a background thread. `recovery` exposes a shared `STATUS` dict, `is_lie_check_active()`, and a cooperative `STOP` event + a `watch_loop`. `record_route.RouteRecorder` records with UI-supplied marks (no stdin). `panel.py` (stdlib `http.server`) has a `Controller` (injected callables → unit-testable state machine) and serves the page + `/status` JSON + `/cmd` actions.

**Tech Stack:** Python stdlib (`http.server`, `threading`, `json`), existing `recovery`/`record_route`/`keyboard`. No new dependencies.

## Global Constraints

- No new third-party dependencies.
- localhost only; no auth. Single worker at a time (farm XOR watch XOR record XOR recover).
- Must not change the farming loop's behavior when driven from the CLI (STATUS/STOP are additive; F8 pause still works).

---

### Task 1: `recovery` status surface + STOP + watch_loop

**Files:** Modify `recovery.py`; Test `tests/test_status_watch.py`.

**Interfaces (Produces):**
- `recovery.STATUS: dict` — keys `state` ("idle"/"farming"/"watching"/"recording"/"recovering"), `node`, `count`, `lie` (bool). Updated inside `farming_loop_nav`.
- `recovery.STOP: threading.Event` — set to request the loop stop; `farming_loop_nav` breaks when set.
- `recovery.is_lie_check_active() -> bool` — true when either alarm controller's alarm is active.
- `recovery.watch_loop(sleep=time.sleep) -> None` — loops `lie_check_tick()` until `STOP` is set, updating `STATUS["lie"]`; calls `lie_check_silence()` on exit.

- [ ] **Step 1: failing test**
```python
# tests/test_status_watch.py
import recovery

def test_is_lie_check_active_reads_alarms(monkeypatch):
    monkeypatch.setattr(recovery._fast_alert.alarm, "_thread", None)
    monkeypatch.setattr(recovery._full_alert.alarm, "_thread", None)
    assert recovery.is_lie_check_active() is False

def test_watch_loop_stops_on_event(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(recovery, "lie_check_tick", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(recovery, "lie_check_silence", lambda: None)
    recovery.STOP.set()                     # already set -> loop body runs zero or once then exits
    recovery.watch_loop(sleep=lambda s: None)
    recovery.STOP.clear()
    assert calls["n"] >= 0                   # exits promptly, no hang
```
- [ ] **Step 2:** `python -m pytest tests/test_status_watch.py -v` → FAIL (no `is_lie_check_active`).
- [ ] **Step 3:** implement (add near the lie_check block and STATUS near module top):
```python
import threading
STATUS = {"state": "idle", "node": None, "count": None, "lie": False}
STOP = threading.Event()

def is_lie_check_active():
    return bool(_fast_alert.alarm.active or _full_alert.alarm.active)

def watch_loop(sleep=time.sleep):
    STOP.clear()
    STATUS["state"] = "watching"
    try:
        while not STOP.is_set():
            lie_check_tick()
            STATUS["lie"] = is_lie_check_active()
            sleep(0.5)
    finally:
        lie_check_silence(); STATUS["lie"] = False; STATUS["state"] = "idle"
```
Then in `farming_loop_nav`: set `STATUS["state"]="farming"` at entry; at the top of the while loop add `if STOP.is_set(): kb.safe_release_all(); STATUS["state"]="idle"; return`; after `node = _nav_locate()` set `STATUS["node"]=node`; where the count is computed set `STATUS["count"]`; and `STATUS["lie"]=is_lie_check_active()` each iteration.
- [ ] **Step 4:** `python -m pytest tests/test_status_watch.py tests/test_recovery.py -q` → PASS.
- [ ] **Step 5:** commit `feat(nav): STATUS surface + STOP event + watch_loop`.

---

### Task 2: `RouteRecorder` (UI-driven marks)

**Files:** Modify `record_route.py`; Test `tests/test_record_route.py`.

**Interfaces (Produces):** `record_route.RouteRecorder(map_name, get_xy=None, poll_hz=30, use_listener=True)` with `.start()`, `.mark(name) -> str` (auto-name `N{n}` if blank), `.stop() -> out_path`, `.marks: int`, `.out_path`. Records positions in a daemon thread; keys via an optional pynput listener; marks come from `mark()`.

- [ ] **Step 1: failing test**
```python
def test_route_recorder_marks_and_positions(tmp_path, monkeypatch):
    import record_route
    monkeypatch.setattr(record_route, "OUT_DIR", str(tmp_path))
    xs = iter([(10, 20), (11, 21), (12, 22)])
    rec = record_route.RouteRecorder("m", get_xy=lambda: next(xs, (12, 22)),
                                     poll_hz=1000, use_listener=False)
    rec.start()
    name = rec.mark("TOP")
    import time; time.sleep(0.05)
    out = rec.stop()
    assert name == "TOP" and rec.marks == 1
    import build_route
    recs = build_route.load_capture(out)
    assert any("mark" in r for r in recs) and any("x" in r for r in recs)
```
- [ ] **Step 2:** run → FAIL (no `RouteRecorder`).
- [ ] **Step 3:** implement the class (poll thread + `_emit`; `get_xy` defaults lazily to `recovery.get_character_full`).
- [ ] **Step 4:** run `tests/test_record_route.py` → PASS.
- [ ] **Step 5:** commit `feat(route): RouteRecorder for UI-driven marks`.

---

### Task 3: `panel.Controller` state machine

**Files:** Create `panel.py`; Test `tests/test_panel.py`.

**Interfaces (Produces):** `panel.Controller(actions)` where `actions` is a dict of callables: `farm`, `watch`, `recover`, `make_recorder`, `pause_toggle`, `stop`. Methods: `start(mode)` (starts only if idle; `mode` in farm/watch/recover), `record_start(map)`, `record_mark(name)`, `record_stop()`, `pause()`, `stop()`, `state()`. Enforces single-worker; returns `(ok, message)`.

- [ ] **Step 1: failing test**
```python
# tests/test_panel.py
import panel

def make():
    log = []
    return log, panel.Controller({
        "farm": lambda: log.append("farm"),
        "watch": lambda: log.append("watch"),
        "recover": lambda: log.append("recover"),
        "make_recorder": lambda mp: log.append(("rec", mp)) or _Rec(log),
        "pause_toggle": lambda: log.append("pause"),
        "stop": lambda: log.append("stop"),
    })

class _Rec:
    def __init__(self, log): self.log = log; self.marks = 0
    def mark(self, n): self.marks += 1; self.log.append(("mark", n)); return n or f"N{self.marks}"
    def stop(self): self.log.append("recstop"); return "out.jsonl"

def test_single_worker_enforced():
    log, c = make()
    assert c.start("farm")[0] is True
    assert c.start("watch")[0] is False        # busy
    assert c.stop()[0] is True
    assert c.start("watch")[0] is True

def test_record_flow():
    log, c = make()
    assert c.record_start("blue")[0] is True
    assert c.record_mark("TOP")[0] is True
    assert c.record_stop()[0] is True
    assert ("mark", "TOP") in log and "recstop" in log
```
- [ ] **Step 2:** run → FAIL (no `panel`).
- [ ] **Step 3:** implement `Controller` (a `busy` flag / current mode; `start` refuses when busy; worker callables run in daemon threads for the real wiring but the Controller itself just guards + dispatches; recorder stored on the instance).
- [ ] **Step 4:** run `tests/test_panel.py` → PASS.
- [ ] **Step 5:** commit `feat(ui): panel.Controller single-worker state machine`.

---

### Task 4: `panel.py` HTTP server + page (live-verified)

**Files:** Modify `panel.py` (add `serve()`, request handler, HTML). Live-verified in browser.

**Interfaces:** `python panel.py` starts `http.server` on `:8080`. `GET /` = page; `GET /status` = `{...recovery.STATUS, mode, recorder_marks}`; `GET /cmd?action=start&mode=farm|watch|recover`, `action=pause|stop`, `action=record_start&map=..`, `action=record_mark&name=..`, `action=record_stop`. The page polls `/status` every 500ms and shows a big green/red lie banner + current node/count/state and the control buttons + a map-name/node-name input for recording. A pynput F8 listener is started so pause still works.

- [ ] **Step 1:** implement handler wiring the real `Controller` (actions call `recovery`/`RouteRecorder` in threads; `farm` uses the same `_enemy_check`/`_panic` hooks as `runnav`).
- [ ] **Step 2:** `python panel.py`, open `http://localhost:8080`, confirm `/status` updates.
- [ ] **Step 3 (user, in-game):** Start → farm; Pause/Resume; Watch-only shows lie banner; Record → mark nodes → stop writes the capture.
- [ ] **Step 4:** commit `feat(ui): local web control panel (panel.py)`.

---

## Self-Review
- Coverage: STATUS/lie banner (T1) ✓; watch loop (T1) ✓; UI-driven recorder (T2) ✓; single-worker control (T3) ✓; page + endpoints (T4) ✓.
- Placeholders: none in T1–T3 code; T4 is live-verified UI wiring.
- Type consistency: `Controller(actions)` keys match panel handler; `RouteRecorder.mark/stop` match `_Rec` fake and real usage.
