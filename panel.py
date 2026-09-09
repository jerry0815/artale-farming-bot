"""Local web control panel for the farming bot (stdlib http.server, no deps).

Run:   python panel.py            # then open http://localhost:8080
Controls: Start (farm) / Pause / Stop, Recover, Watch-only (passive lie-check
loop), and Record route (map name + node marks from the browser). Live status +
a big green/red lie-check banner, polled from recovery.STATUS.

The bot runs in a background thread; the panel sets keyboard.pause and reads the
shared status, so the existing 'n' hotkey and all lie-check ticks keep working.
See docs/superpowers/specs/2026-08-24-route-recorder-and-ui-design.md.
"""
import json
import sys
import time
import threading
import http.server
import socketserver
import urllib.parse
from collections import deque


def _ts():
    """Wall-clock HH:MM:SS.mmm for log lines -- sync the panel Log to a screen recording."""
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"

# --- stdout tee: mirror loop prints into a ring buffer the panel can serve ---
_LOG = deque(maxlen=800)          # (seq, line)
_LOG_SEQ = [0]
_LOG_LOCK = threading.Lock()


class _Tee:
    """Wrap a stream: write through, buffer complete lines into _LOG, and append each line to
    the persistent log FILES, flushed per line so a mid-run shutdown loses nothing."""
    def __init__(self, real, logfiles=()):
        self._real = real
        self._buf = ""
        self._logfiles = list(logfiles)

    def write(self, s):
        self._real.write(s)
        with _LOG_LOCK:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                _LOG_SEQ[0] += 1
                stamped = f"{_ts()} {line}" if line else line
                _LOG.append((_LOG_SEQ[0], stamped))
                for lf in self._logfiles:
                    try:
                        lf.write(stamped + "\n")
                        lf.flush()                        # survive an abrupt shutdown mid-run
                    except Exception:
                        pass

    def flush(self):
        self._real.flush()
        for lf in self._logfiles:
            try:
                lf.flush()
            except Exception:
                pass


LOG_PATH = [None]                                          # path of the current session's log file


def install_log_tee(logdir="logs"):
    """Tee stdout into the in-memory ring AND persistent files: a timestamped run_<ts>.log
    (durable history) plus logs/latest.log (stable path, always the current session)."""
    if isinstance(sys.stdout, _Tee):
        return
    files = []
    try:
        import os
        os.makedirs(logdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        path = os.path.join(logdir, f"run_{stamp}.log")
        files.append(open(path, "a", buffering=1, encoding="utf-8"))       # durable, line-buffered
        files.append(open(os.path.join(logdir, "latest.log"), "w", buffering=1, encoding="utf-8"))
        LOG_PATH[0] = path
    except Exception as e:
        sys.stderr.write(f"[panel] log file disabled: {e}\n")
    sys.stdout = _Tee(sys.stdout, files)
    if LOG_PATH[0]:
        print(f"[panel] logging to {LOG_PATH[0]}  (and logs/latest.log)")


def log_since(cursor):
    with _LOG_LOCK:
        lines = [ln for seq, ln in _LOG if seq > cursor]
        return {"lines": lines, "next": _LOG_SEQ[0]}

PORT = 8080


class Controller:
    """Single-worker control state machine. `actions` supplies the callables so this
    is unit-testable without the game or a server: farm/watch/recover (each starts a
    worker), make_recorder(map)->recorder, pause_toggle, stop."""

    def __init__(self, actions):
        self.actions = actions
        self.mode = "idle"           # idle|farming|watching|recovering|recording
        self.recorder = None

    _MODE_NAME = {"farm": "farming", "watch": "watching", "recover": "recovering"}

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

    def record_start(self, mapname):
        if self.mode != "idle":
            return False, f"busy ({self.mode})"
        self.recorder = self.actions["make_recorder"](mapname or "map")
        self.mode = "recording"
        return True, "recording"

    def record_mark(self, name):
        if self.mode != "recording" or self.recorder is None:
            return False, "not recording"
        return True, self.recorder.mark(name)

    def record_stop(self):
        if self.mode != "recording" or self.recorder is None:
            return False, "not recording"
        out = self.recorder.stop()
        self.recorder = None
        self.mode = "idle"
        return True, out

    def exp_record_start(self, label):
        if self.mode != "idle":
            return False, f"busy ({self.mode})"
        self.mode = "exp_record"
        self.actions["exp_record"](label or "human")
        return True, "exp_record"

    def exp_record_stop(self):
        if self.mode != "exp_record":
            return False, "not recording exp"
        self.actions["stop"]()                        # STOP -> the loop's finally writes the row
        self.mode = "idle"
        return True, "stopped"

    def pause(self):
        self.actions["pause_toggle"]()
        return True, "toggled"

    def stop(self):
        if self.mode == "recording":
            return self.record_stop()
        self.actions["stop"]()
        self.mode = "idle"
        return True, "stopped"

    def finish(self):
        """Called by a one-shot worker (recover) when it completes."""
        if self.mode in ("recovering",):
            self.mode = "idle"


# --------------------------------------------------------------------------------
# Real wiring (game side). Imported lazily so the Controller stays testable alone.
# --------------------------------------------------------------------------------
def _build_actions(controller_ref):
    import recovery
    import keyboard as kb
    import record_route

    def _run_bg(target):
        threading.Thread(target=target, daemon=True).start()

    import subprocess
    import sys as _sys

    def _run_cmd_bg(args, label):
        """Run a CLI step (train/label) as a subprocess, streaming its output to the panel Log."""
        def _go():
            print(f"[{label}] $ {' '.join(str(a) for a in args)}")
            try:
                p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1)
                for line in p.stdout:
                    line = line.rstrip()
                    if line:
                        print(f"[{label}] {line}")
                p.wait()
                print(f"[{label}] done (exit {p.returncode})")
            except Exception as e:
                print(f"[{label}] error: {e}")
        _run_bg(_go)

    def train(step, clip="", count="60"):
        """Setup-tab training pipeline. Output streams to the Log."""
        py = _sys.executable
        if step == "retrain":
            _run_cmd_bg([py, "train_mobs.py", "80", "960", "--real"], "train")
            return True, "retrain started (GPU, ~25min) -> watch Log"
        if step == "grab_misses":
            if not clip:
                return False, "clip path required"
            _run_cmd_bg([py, "label_mobs.py", "--grab-misses", clip, str(count)], "grab")
            return True, "grabbing player-miss frames -> watch Log, then Open labeler"
        if step == "labeler":
            _run_cmd_bg([py, "label_mobs.py"], "label")
            return True, "labeler starting -> open http://localhost:8001"
        return False, f"unknown step {step}"

    def _enemy_check():
        return len(recovery.get_enemy()) > 0

    def _panic():
        kb.safe_release_all()
        print("[panel] safety -> release keys and PAUSE ('n' to resume).")
        kb.pause = True

    def farm(map_path, char=None):
        """One farm entry: the selected map's `loop` field picks the loop -- 'nav' = the
        land dragon-nest loop (map geometry unused), else the water swim loop."""
        import watermap
        def _go():
            kb.pause = False
            cfg = watermap.load_map(map_path) if map_path else {}
            if cfg.get("loop") == "nav":
                recovery.farming_loop_nav(exp_check=None, enemy_check=_enemy_check, panic=_panic, cfg=cfg)
            else:
                recovery.farming_loop_water(map_path, char=char or None,
                                            enemy_check=_enemy_check, panic=_panic)
        _run_bg(_go)

    def watch():
        _run_bg(recovery.watch_loop)

    def exp_record(label):
        _run_bg(lambda: recovery.exp_record_loop(label))

    def recover():
        def _go():
            try:
                recovery.recover_to_farming()
            finally:
                controller_ref[0].finish()
                recovery.STATUS["state"] = "idle"
        recovery.STATUS["state"] = "recovering"
        _run_bg(_go)

    def make_recorder(mapname):
        rec = record_route.RouteRecorder(mapname)
        recovery.STATUS["state"] = "recording"
        rec.start()
        return rec

    def pause_toggle():
        kb.pause = not kb.pause

    def stop():
        recovery.STOP.set()
        recovery.lie_check_silence()
        kb.safe_release_all()

    return {"farm": farm, "watch": watch, "recover": recover, "train": train,
            "make_recorder": make_recorder, "pause_toggle": pause_toggle, "stop": stop,
            "exp_record": exp_record}


def detect_frame(map_path=None, scale=None, thr=None, per=None, species=None, roi=None, max_w=900,
                 kind=None):
    """Capture one game frame, run mob detection (live crops if the map config has
    mob_template_dir, else green sprites), and return {ok, count, best, mode, img}
    for the panel's detection preview. `kind="dragon"` instead previews the dragon-nest
    YOLO count with the per-node count ROIs drawn. No focus steal."""
    import base64
    import cv2
    import numpy as np
    import fish
    import recovery
    import watermap
    if kind == "dragon":                                      # dragon-nest count ROI preview
        import monsters
        f = recovery.capture()
        if f is None:
            return {"ok": False, "msg": "no frame (is the game window visible / not minimized?)"}
        try:
            model = monsters.load_dragon_model()
        except Exception as e:
            return {"ok": False, "msg": f"dragon model load failed: {e}"}
        conf = thr if thr is not None else 0.35
        boxes = monsters.run_yolo(model, f, conf)
        rois = monsters.MOTION_ROI_BY_NODE
        dbg = f.copy()
        for node, r in rois.items():                          # each node's count band (cyan)
            cv2.rectangle(dbg, (r[0], r[1]), (r[2], r[3]), (0, 255, 255), 2)
            cv2.putText(dbg, node, (r[0] + 4, r[1] + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        counted = 0
        for (x1, y1, x2, y2, s) in boxes:                     # green = counted, red = ignored (out of ROI)
            inside = any(monsters.box_center_in_roi((x1, y1, x2, y2), r) for r in rois.values())
            counted += 1 if inside else 0
            color = (0, 200, 0) if inside else (0, 0, 255)
            cv2.rectangle(dbg, (x1, y1), (x2, y2), color, 2)
            cv2.circle(dbg, ((x1 + x2) // 2, (y1 + y2) // 2), 4, color, -1)   # the counted center
            cv2.putText(dbg, f"{s:.2f}", (x1, max(y1 - 3, 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        sc = min(1.0, max_w / dbg.shape[1])
        small = cv2.resize(dbg, None, fx=sc, fy=sc) if sc < 1.0 else dbg
        _ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return {"ok": True, "count": counted, "best": {}, "mode": "dragon_yolo",
                "thr": round(conf, 3), "player": None,
                "anchor": f"{len(boxes)} dragons, {counted} inside ROI  (green=counted, red=ignored)",
                "img": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}
    cfg = watermap.load_map(map_path) if map_path else {}
    if species:
        cfg = {**cfg, "fish_species": species}
    if per:
        cfg = {**cfg, "fish_per_species": per}
    f = recovery.capture()
    if f is None:
        return {"ok": False, "msg": "no frame (is the game window visible / not minimized?)"}
    if roi is None and cfg.get("count_roi"):
        roi = tuple(cfg["count_roi"])
    user_thr = thr                                            # explicit snap override (before reuse)
    if cfg.get("detector") == "mob_yolo":                     # match the loop's real detector
        import mob_detect
        m = mob_detect.load_yolo(cfg.get("mob_model", "models/mob_yolo.pt"))
        conf = thr if thr is not None else float(cfg.get("mob_conf", 0.6))
        _cc = {}                                          # per-class conf floor (match the loop)
        if cfg.get("fishhouse_conf") is not None:
            _cc[0] = float(cfg["fishhouse_conf"])
        if cfg.get("goby_conf") is not None:
            _cc[1] = float(cfg["goby_conf"])
        dets = mob_detect.yolo_boxes(m, f, roi=roi, conf=conf, imgsz=int(cfg.get("mob_imgsz", 640)),
                                     class_conf=_cc or None)
        best, mode, thr = {}, "mob_yolo", conf
    else:
        tmpls, mode = fish.templates_for(cfg, scale=scale)
        if thr is None:
            thr = cfg.get("fish_threshold") or fish.default_threshold(mode)
        ds = cfg.get("match_downscale", 0.5)
        result = fish.scan(f, tmpls, roi=roi, threshold=thr, downscale=ds)
        best, dets = result["best"], result["dets"]
    # player anchor overlay (green) -- use the map's CONFIGURED anchor so the preview matches
    # what the loop uses (name-tag or HP bar).
    import player as _player
    dbg = f.copy()
    if roi:
        cv2.rectangle(dbg, (roi[0], roi[1]), (roi[2], roi[3]), (0, 255, 255), 2)
    for s, x, y, w, h in dets:
        cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.putText(dbg, f"{s:.2f}", (x, max(y - 3, 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    player_pos, anchor_mode = None, cfg.get("anchor", "hpbar")
    if anchor_mode == "yolo_player" and cfg.get("detector") == "mob_yolo":
        try:                                                 # class-2 player box = GREEN
            import mob_detect as _md
            _mp = _md.load_yolo(cfg.get("mob_model", "models/mob_yolo.pt"))
            _pfoot = int(cfg.get("yolo_foot_offset", 0))     # HP-bar box bottom -> feet (~123px down)
            _pconf = user_thr if user_thr is not None else float(cfg.get("yolo_player_conf", cfg.get("mob_conf", 0.6)))
            _, pbox = _md.yolo_detect(_mp, f, roi=None, conf=_pconf, imgsz=int(cfg.get("mob_imgsz", 640)))
            if pbox is not None:
                _s, px, py, pw, ph = pbox
                player_pos = (px + pw // 2, py + ph + _pfoot)
                cv2.rectangle(dbg, (px, py), (px + pw, py + ph), (0, 255, 0), 2)
                cv2.circle(dbg, player_pos, 9, (0, 255, 0), -1)
                cv2.putText(dbg, "player", (px, py - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                anchor_mode = f"player@{player_pos} ({_s:.2f} conf>={_pconf})"
            else:
                anchor_mode = f"player:MISS (conf>={_pconf})"
        except Exception as e:
            anchor_mode = f"yolo_player ERROR: {e}"
    elif anchor_mode == "nametag" and cfg.get("nametag_template"):
        try:
            # Draw BOTH anchors independently so each template can be checked: name tag = GREEN,
            # 稱號 title = CYAN. player_pos = name tag if found, else title (the loop's order).
            parts = []
            name_a = _player.NametagAnchor(_player.load_nametag(cfg["nametag_template"]),
                                           feet_offset=int(cfg.get("nametag_feet_offset", 6)),
                                           accept_thres=float(cfg.get("nametag_accept", 0.55)))
            np_ = name_a.locate(f)
            if np_ is not None and name_a.last is not None:
                lx, ly = name_a.last
                cv2.rectangle(dbg, (lx, ly), (lx + name_a.w, ly + name_a.h), (0, 255, 0), 2)
                cv2.circle(dbg, np_, 9, (0, 255, 0), -1)
                cv2.putText(dbg, "name", (lx, ly - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                parts.append(f"name@{np_}")
                player_pos = np_
            else:
                parts.append("name:MISS")
            if cfg.get("title_template"):
                title_a = _player.NametagAnchor(_player.load_nametag(cfg["title_template"]),
                                                feet_offset=int(cfg.get("title_feet_offset", 40)),
                                                accept_thres=float(cfg.get("title_accept", 0.55)))
                tp = title_a.locate(f)
                if tp is not None and title_a.last is not None:
                    lx, ly = title_a.last
                    cv2.rectangle(dbg, (lx, ly), (lx + title_a.w, ly + title_a.h), (255, 220, 0), 2)
                    cv2.circle(dbg, tp, 9, (255, 220, 0), -1)
                    cv2.putText(dbg, "title", (lx, ly - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 220, 0), 2)
                    parts.append(f"title@{tp}")
                    if player_pos is None:
                        player_pos = tp
                else:
                    parts.append("title:MISS")
            anchor_mode = " ".join(parts)                    # e.g. "name@(x,y) title@(x,y)"
        except Exception as e:
            anchor_mode = f"nametag ERROR: {e}"
    else:
        pinfo = _player.find_player(f, cfg=cfg.get("player"), debug=True)
        for cx, cy, w, h in pinfo["candidates"]:             # candidate red bars = yellow
            cv2.rectangle(dbg, (cx - w // 2, cy - h // 2), (cx + w // 2, cy + h // 2), (0, 220, 220), 1)
        if pinfo["bar"] is not None:
            bx, by = pinfo["bar"][0], pinfo["bar"][1]
            player_pos = pinfo["player"]
            cv2.circle(dbg, (bx, by), 8, (0, 255, 0), 2)
            cv2.circle(dbg, player_pos, 10, (0, 255, 0), -1)
            cv2.line(dbg, (bx, by), player_pos, (0, 255, 0), 2)
    sc = min(1.0, max_w / dbg.shape[1])
    small = cv2.resize(dbg, None, fx=sc, fy=sc) if sc < 1.0 else dbg
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return {"ok": True, "count": len(dets), "best": best, "mode": mode, "thr": round(thr, 3),
            "player": player_pos, "anchor": anchor_mode,
            "img": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}


def list_maps(maps_dir="maps"):
    """Available water map configs: [{name, path}], sorted by name."""
    import os
    import glob
    out = []
    for p in sorted(glob.glob(os.path.join(maps_dir, "*.json"))):
        out.append({"name": os.path.splitext(os.path.basename(p))[0], "path": p})
    return out


def list_chars(chars_dir="chars"):
    """Character config files (chars/*.json), sorted. Empty if the dir is absent."""
    import os
    from glob import glob
    if not os.path.isdir(chars_dir):
        return []
    return sorted(glob(os.path.join(chars_dir, "*.json")))


def _status_dict(controller):
    import recovery, time
    st = dict(recovery.STATUS)
    st["mode"] = controller.mode
    st["recorder_marks"] = controller.recorder.marks if controller.recorder else 0
    ba = st.get("buff_at")
    st["buff_ago"] = int(time.time() - ba) if ba else None   # seconds since last buff (server clock)
    # ACTIVE farming time (excludes pauses): ticks live while farming, frozen when paused.
    base = st.get("run_active_base")
    if base is not None:
        since = st.get("run_active_since")
        active = base + (time.time() - since if (since and st.get("state") == "farming") else 0.0)
        st["run_secs"] = int(active)
        # Derive avg from the SAME active time so total / time / avg agree (>=1 min in).
        st["exp_per_min"] = round(st.get("exp_total", 0) / (active / 60.0)) if active >= 60 else None
    # exp_record has no farming active-time; expose elapsed from its own start so the UI can
    # show a live timer and prove recording is running.
    started = st.get("exp_started_at")
    if st.get("state") == "exp_record" and started:
        st["run_secs"] = int(time.time() - started)
    return st


PAGE = """<!doctype html><html><head><meta charset=utf-8><title>maple control</title>
<style>
 body{margin:0;font-family:system-ui;background:#14141a;color:#eee}
 #wrap{max-width:1320px;margin:0 auto;padding:18px;display:flex;gap:18px;align-items:flex-start}
 #main{flex:0 0 720px;max-width:720px;min-width:0}
 #side{flex:1;min-width:0;position:sticky;top:18px}
 @media(max-width:1080px){ #wrap{flex-direction:column} #main{flex-basis:auto;max-width:100%;width:100%} #side{width:100%} }
 h1{font-size:18px;color:#6cf;margin:0 0 12px}
 #lie{font-size:22px;font-weight:700;text-align:center;padding:18px;border-radius:10px;margin:12px 0}
 .ok{background:#173d17;color:#7fdd7f} .bad{background:#4d1414;color:#ff8a8a}
 .row{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
 button{background:#2a2a33;color:#eee;border:1px solid #444;border-radius:8px;
        padding:10px 14px;cursor:pointer;font-size:14px} button:hover{background:#3a3a46}
 button.go{border-color:#2b6} button.stop{border-color:#b33}
 input{background:#1c1c22;color:#eee;border:1px solid #444;border-radius:8px;padding:9px;font-size:14px}
 #stat{font-family:ui-monospace,monospace;background:#1c1c22;border-radius:8px;padding:12px;margin-top:12px}
 fieldset{border:1px solid #333;border-radius:8px;margin-top:14px}
 legend{color:#9ad}
 #log{font-family:ui-monospace,monospace;font-size:12px;background:#0d0d12;color:#cdd;
      border-radius:8px;padding:10px;height:calc(100vh - 150px);overflow:auto;white-space:pre-wrap;line-height:1.4}
 @media(max-width:1080px){ #log{height:280px} }
 #log .exp{color:#7fdd7f} #log .app{color:#9ad} #log .wtr{color:#e8c06a}
 #exp{background:#161d16;border:1px solid #2b4d2b;border-radius:8px;padding:12px;margin-top:12px;text-align:center}
 #exp .big{font-size:26px;font-weight:700;color:#7fdd7f;line-height:1.1}
 #exp .big small{font-size:13px;color:#9ad;font-weight:400}
 #exp .sub{font-size:12px;color:#9a9;margin-top:5px}
 #tabs{display:flex;gap:6px;margin:14px 0 0}
 .tab{background:#1c1c22;border:1px solid #333;border-bottom:none;border-radius:8px 8px 0 0;
      padding:8px 18px;color:#bbb}
 .tab.active{background:#2a2a33;color:#6cf;border-color:#444}
 .pane{border-top:1px solid #333;padding-top:14px}
 .hidden{display:none}
</style></head><body><div id=wrap>
 <div id=main>
 <h1>MapleStory bot — control panel</h1>
 <div id=lie class=ok>lie-check: OK</div>
 <div id=tabs>
   <button id=tab-b-control class="tab active" onclick="showTab('control')">Control</button>
   <button id=tab-b-setup class=tab onclick="showTab('setup')">Setup</button>
   <button id=tab-b-exp class=tab onclick="showTab('exp')">EXP</button>
 </div>

 <div id=tab-control class=pane>
   <fieldset><legend>Farm</legend>
     <div class=row>
       <select id=mapsel></select>
       <select id=charsel></select>
       <button class=go onclick="startFarm()">▶ Start farm</button>
       <button onclick="loadMaps()">↻</button>
     </div>
     <div class=row>
       <button onclick="cmd('pause')">⏸ Pause / Resume</button>
       <button class=stop onclick="cmd('stop')">■ Stop</button>
     </div>
   </fieldset>
   <div class=row>
     <button onclick="cmd('start','recover')">↥ Recover to top</button>
     <button class=go onclick="cmd('start','watch')">👁 Watch-only (lie-check)</button>
   </div>
   <div id=exp>
     <div class=big><span id=exppm>–</span> <small>EXP / min (run avg)</small></div>
     <div class=sub>total <span id=exptot>–</span> &middot; last 10 min <span id=exp10>–</span></div>
   </div>
   <div id=stat>loading…</div>
 </div>

 <div id=tab-setup class="pane hidden">
   <fieldset><legend>Record route (per map)</legend>
     <div class=row>
       map <input id=map placeholder="map name (e.g. deep_sea_2)" value="deep_sea_2" style="width:200px">
       <button class=go onclick="recStart()">● Start recording</button>
     </div>
     <div class=row>
       <input id=node placeholder="node name (blank = auto)">
       <button onclick="recMark()">＋ Mark node</button>
       <button class=stop onclick="recStop()">■ Stop recording</button>
     </div>
     <div id=recstat style="font-family:ui-monospace,monospace;font-size:12px;margin:6px 0;color:#9ad">not recording</div>
   </fieldset>
   <fieldset><legend>Train detection</legend>
     <div class=row>
       clip <input id=tclip placeholder="C:/path/to/clip.mp4" style="width:250px">
       n <input id=tcount placeholder="60" style="width:44px">
       <button class=go onclick="train('grab_misses')">🎯 Grab misses</button>
     </div>
     <div class=row>
       <button class=go onclick="train('labeler')">🏷 Open labeler (:8001)</button>
       <button class=go onclick="train('retrain')">🧠 Retrain (GPU)</button>
     </div>
     <div id=tstat style="font-family:ui-monospace,monospace;font-size:12px;margin:6px 0;color:#9ad">idle &middot; output goes to the Log below</div>
   </fieldset>
 </div>

 <div id=tab-exp class="pane hidden">
   <fieldset><legend>Record EXP session (manual — you farm by hand, this only records)</legend>
     <div class=row>
       label <input id=explabel value="human" style="width:120px">
       <button class=go onclick="startExp()">● Start recording</button>
       <button class=stop onclick="stopExp()">■ Stop</button>
       <button onclick="loadExpSessions()">↻</button>
     </div>
     <div id=expstat class=sub style="font-family:ui-monospace,monospace">not recording</div>
     <div class=sub><span id=exppm2>–</span> EXP/min (run avg) &middot; total <span id=exptot2>–</span></div>
   </fieldset>
   <table id=exptable style="width:100%;border-collapse:collapse;font-size:12px">
     <thead><tr style="color:#9ad;text-align:left">
       <th>start<th>dur<th>EXP gained<th>EXP/min<th>label</tr></thead>
     <tbody></tbody>
   </table>
 </div>

 <fieldset id=detprev><legend>Detection preview</legend>
   <div class=row>
     scale <input id=dscale placeholder="auto" style="width:56px">
     thr <input id=dthr placeholder="auto" style="width:56px">
     species <input id=dspecies placeholder="sprite mode only" style="width:150px">
   </div>
   <div class=row>
     roi <input id=droi placeholder="x0,y0,x1,y1 (blank=full)" style="width:190px">
     <button class=go onclick="snap()">📸 Snap</button>
     <label><input type=checkbox id=dlive> live (2s)</label>
     <label title="show the dragon-nest count ROIs + which dragons count (green) vs are ignored (red)"><input type=checkbox id=ddragon> 🐉 dragon ROI</label>
   </div>
   <div id=dstat style="font-family:ui-monospace,monospace;font-size:12px;margin:6px 0;white-space:nowrap;overflow-x:auto"></div>
   <img id=detimg style="max-width:100%;border-radius:6px;display:none">
 </fieldset>
 </div>
 <div id=side>
 <fieldset><legend>Log</legend>
   <div class=row><button onclick="document.getElementById('log').innerHTML=''">clear</button>
     <label><input type=checkbox id=autoscroll checked> auto-scroll</label></div>
   <div id=log></div>
 </fieldset>
 </div>
</div>
<script>
 async function cmd(action, mode){
   const q = new URLSearchParams({action}); if(mode) q.set('mode', mode);
   const r = await fetch('/cmd?'+q); const j = await r.json();
   if(!j.ok) alert(j.msg);
 }
 function showTab(name){
   for(const t of ['control','setup','exp']){
     document.getElementById('tab-'+t).classList.toggle('hidden', t!==name);
     document.getElementById('tab-b-'+t).classList.toggle('active', t===name);
   }
   document.getElementById('detprev').classList.toggle('hidden', name==='exp');  // preview irrelevant while recording EXP
 }
 async function train(step){
   const q = new URLSearchParams({action:'train', step});
   if(step==='grab_misses'){
     q.set('clip', document.getElementById('tclip').value.trim());
     q.set('count', document.getElementById('tcount').value.trim()||'60');
   }
   const j = await (await fetch('/cmd?'+q)).json();
   document.getElementById('tstat').textContent = j.msg || (j.ok?'started':'error');
 }
 let recMarks=[];
 function recShow(t){ document.getElementById('recstat').textContent=t; }
 async function recStart(){
   const q = new URLSearchParams({action:'record_start', map:document.getElementById('map').value});
   const j = await (await fetch('/cmd?'+q)).json();
   if(j.ok){ recMarks=[]; recShow('● recording '+document.getElementById('map').value+' — 0 marks'); }
   else alert(j.msg);
 }
 async function recMark(){
   const q = new URLSearchParams({action:'record_mark', name:document.getElementById('node').value});
   const j = await (await fetch('/cmd?'+q)).json();
   if(j.ok){ recMarks.push(j.msg); document.getElementById('node').value='';
     recShow('● '+recMarks.length+' marks: '+recMarks.join(', ')); }
   else { recShow('⚠ '+j.msg); alert(j.msg); }
 }
 async function recStop(){
   const j = await (await fetch('/cmd?action=record_stop')).json();
   if(j.ok) recShow('■ stopped — '+recMarks.length+' marks: '+recMarks.join(', '));
   else alert(j.msg);
 }
 let snapping=false;
 async function snap(){
   if(snapping) return; snapping=true;
   const q = new URLSearchParams();
   if(dscale.value.trim()) q.set('scale', dscale.value.trim());
   if(dthr.value.trim()) q.set('thr', dthr.value.trim());
   if(dspecies.value.trim()) q.set('species', dspecies.value.trim());
   if(droi.value.trim()) q.set('roi', droi.value.trim());
   const mapsel=document.getElementById('mapsel'); if(mapsel && mapsel.value) q.set('map', mapsel.value);
   if(document.getElementById('ddragon').checked) q.set('kind','dragon');   // dragon count-ROI preview
   document.getElementById('dstat').textContent='snapping…';
   try{
     const j = await (await fetch('/detect?'+q)).json();
     if(!j.ok){ document.getElementById('dstat').textContent='error: '+j.msg; }
     else{
       const pl = j.player ? `player@(${j.player[0]},${j.player[1]})` : 'player: NOT FOUND';
       document.getElementById('dstat').textContent =
         `[${j.mode} thr=${j.thr}] count: ${j.count}   ${pl} [${j.anchor}]   best: ${Object.entries(j.best).map(([k,v])=>k+'='+v).join('  ')}`;
       const im=document.getElementById('detimg'); im.src=j.img; im.style.display='block';
     }
   }catch(e){ document.getElementById('dstat').textContent='error: '+e; }
   snapping=false;
 }
 async function snapLoop(){
   if(document.getElementById('dlive').checked) await snap();
   setTimeout(snapLoop, 2000);
 }
 async function loadMaps(){
   const maps = await (await fetch('/maps')).json();
   const sel = document.getElementById('mapsel');
   sel.innerHTML = maps.length ? '' : '<option value="">(no maps/*.json)</option>';
   for(const m of maps){ const o=document.createElement('option'); o.value=m.path; o.textContent=m.name; sel.appendChild(o); }
 }
 async function loadChars(){
   const chars = await (await fetch('/chars')).json();
   const sel = document.getElementById('charsel');
   sel.innerHTML = '<option value="">(map default)</option>';
   for(const p of chars){
     const name = p.split(/[\\/]/).pop().replace(/\.json$/, '');
     const o=document.createElement('option'); o.value=p; o.textContent=name; sel.appendChild(o);
   }
 }
 async function startFarm(){
   const map = document.getElementById('mapsel').value;
   if(!map){ alert('no map selected'); return; }
   const char = document.getElementById('charsel').value;
   const j = await (await fetch('/cmd?'+new URLSearchParams({action:'start',mode:'farm',map,char}))).json();
   if(!j.ok) alert(j.msg);
 }
 async function poll(){
   try{
     const s = await (await fetch('/status')).json();
     const lie = document.getElementById('lie');
     lie.className = s.lie ? 'bad' : 'ok';
     lie.textContent = s.lie ? '⚠ lie-check: NEEDS HUMAN' : 'lie-check: OK';
     const hms = t => { if(t==null) return '–'; t=Math.max(0,t|0);
       const h=t/3600|0, m=(t%3600)/60|0, x=t%60;
       return (h? h+':'+String(m).padStart(2,'0') : m) + ':' + String(x).padStart(2,'0'); };
     const buff = s.buff_ago==null ? 'not yet' : hms(s.buff_ago)+' ago';
     document.getElementById('stat').textContent =
       `farming time: ${hms(s.run_secs)}\nmode: ${s.mode}\nstate: ${s.state}\nnode: ${s.node}`
       + `\ndragons: ${s.count}\nbuff: ${buff}\nmarks: ${s.recorder_marks}`;
     const fmt = n => (n==null ? '–' : Number(n).toLocaleString());
     document.getElementById('exppm').textContent = fmt(s.exp_per_min);
     document.getElementById('exptot').textContent = fmt(s.exp_total);
     document.getElementById('exp10').textContent = fmt(s.exp_10min);
     document.getElementById('exppm2').textContent = fmt(s.exp_per_min);
     document.getElementById('exptot2').textContent = fmt(s.exp_total);
     const rec = s.state==='exp_record', es = document.getElementById('expstat');
     es.textContent = rec ? ('● recording — '+hms(s.run_secs)) : 'not recording';
     es.style.color = rec ? '#7fdd7f' : '#9a9';
   }catch(e){}
   setTimeout(poll, 500);
 }
 let logCursor=0;
 function logClass(s){ s=s.replace(/^\d\d:\d\d:\d\d\.\d\d\d /,'');   // drop timestamp prefix
   if(s.startsWith('[exp]'))return'exp'; if(s.startsWith('[approach'))return'app';
   if(s.startsWith('[water')||s.startsWith('[nav'))return'wtr'; return''; }
 async function pollLog(){
   try{
     const j = await (await fetch('/log?since='+logCursor)).json();
     if(j.lines && j.lines.length){
       const box=document.getElementById('log');
       for(const ln of j.lines){ const d=document.createElement('div');
         d.className=logClass(ln); d.textContent=ln; box.appendChild(d); }
       while(box.childNodes.length>800) box.removeChild(box.firstChild);
       if(document.getElementById('autoscroll').checked) box.scrollTop=box.scrollHeight;
     }
     logCursor=j.next;
   }catch(e){}
   setTimeout(pollLog, 1200);
 }
 async function startExp(){
   const label = document.getElementById('explabel').value || 'human';
   const j = await (await fetch('/cmd?'+new URLSearchParams({action:'exp_record_start', label}))).json();
   if(!j.ok){ alert(j.msg); return; }
   document.getElementById('expstat').textContent = '● recording — 0:00';   // instant feedback; poll takes over
 }
 async function stopExp(){
   const j = await (await fetch('/cmd?'+new URLSearchParams({action:'exp_record_stop'}))).json();
   if(!j.ok){ alert(j.msg); return; }
   document.getElementById('expstat').textContent = 'not recording';
   setTimeout(loadExpSessions, 500);        // let the recorder's finally write the row first
 }
 async function loadExpSessions(){
   try{
     const rows = await (await fetch('/exp_sessions')).json();
     const tb = document.querySelector('#exptable tbody'); tb.innerHTML='';
     for(const r of rows){
       const dur = (r.duration_s!=null)? Math.round(r.duration_s/60)+'m' : '';
       const cells = [r.ts_start||'', dur, fmt(r.exp_gained), fmt(r.exp_per_min), r.label||''];
       const tr = document.createElement('tr');
       for(const c of cells){ const td=document.createElement('td'); td.style.padding='3px 8px 3px 0'; td.textContent=c; tr.appendChild(td); }
       tb.appendChild(tr);
     }
   }catch(e){}
 }
 loadMaps(); loadChars(); poll(); snapLoop(); pollLog(); loadExpSessions();
</script></body></html>"""


def make_handler(controller):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if parsed.path == "/log":
                cursor = int(q.get("since", ["0"])[0])
                return self._send(200, json.dumps(log_since(cursor)))
            if parsed.path == "/status":
                return self._send(200, json.dumps(_status_dict(controller)))
            if parsed.path == "/maps":
                return self._send(200, json.dumps(list_maps()))
            if parsed.path == "/chars":
                return self._send(200, json.dumps(list_chars()))
            if parsed.path == "/exp_sessions":
                import recovery
                return self._send(200, json.dumps(recovery.read_exp_sessions()))
            if parsed.path == "/detect":
                mp = q.get("map", [None])[0] or None
                scale = float(q["scale"][0]) if q.get("scale", [""])[0] else None
                thr = float(q["thr"][0]) if q.get("thr", [""])[0] else None
                per = int(q["per"][0]) if q.get("per", [""])[0] else None
                sp = q.get("species", [None])[0]
                species = sp.split(",") if sp else None
                roi_s = q.get("roi", [None])[0]
                roi = tuple(int(v) for v in roi_s.split(",")) if roi_s else None
                kind = q.get("kind", [None])[0] or None
                try:
                    return self._send(200, json.dumps(detect_frame(
                        map_path=mp, scale=scale, thr=thr, per=per, species=species, roi=roi,
                        kind=kind)))
                except Exception as e:
                    return self._send(200, json.dumps({"ok": False, "msg": str(e)}))
            if parsed.path == "/cmd":
                action = q.get("action", [""])[0]
                ok, msg = self._dispatch(action, q)
                return self._send(200, json.dumps({"ok": ok, "msg": msg}))
            return self._send(404, json.dumps({"ok": False, "msg": "not found"}))

        def _dispatch(self, action, q):
            if action == "start":
                return controller.start(q.get("mode", [""])[0],
                                        map_path=q.get("map", [None])[0],
                                        char=q.get("char", [None])[0])
            if action == "pause":
                return controller.pause()
            if action == "stop":
                return controller.stop()
            if action == "record_start":
                return controller.record_start(q.get("map", [""])[0])
            if action == "record_mark":
                return controller.record_mark(q.get("name", [""])[0])
            if action == "record_stop":
                return controller.record_stop()
            if action == "exp_record_start":
                return controller.exp_record_start(q.get("label", ["human"])[0])
            if action == "exp_record_stop":
                return controller.exp_record_stop()
            if action == "train":
                fn = controller.actions.get("train")
                if not fn:
                    return False, "training unavailable"
                return fn(q.get("step", [""])[0], q.get("clip", [""])[0],
                          q.get("count", ["60"])[0])
            return False, f"unknown action {action}"

        def log_message(self, *a):
            pass                                   # quiet
    return Handler


def serve(port=PORT):
    import keyboard as kb
    from pynput.keyboard import Listener
    install_log_tee()                              # capture loop prints for the panel log pane
    ref = [None]
    controller = Controller(_build_actions(ref))
    ref[0] = controller
    lis = Listener(on_press=kb.on_press)           # keep 'n' pause working
    lis.start()
    handler = make_handler(controller)
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"[panel] control panel at http://localhost:{port}  (Ctrl+C to quit)")
        try:
            httpd.serve_forever()
        finally:
            lis.stop()


if __name__ == "__main__":
    serve()
