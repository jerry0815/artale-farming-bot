"""Local web control panel for the farming bot (stdlib http.server, no deps).

Run:   python panel.py            # then open http://localhost:8080
Controls: Start (farm) / Pause / Stop, Recover, Watch-only (passive lie-check
loop), and Record route (map name + node marks from the browser). Live status +
a big green/red lie-check banner, polled from recovery.STATUS.

The bot runs in a background thread; the panel sets keyboard.pause and reads the
shared status, so the existing F8 hotkey and all lie-check ticks keep working.
See docs/superpowers/specs/2026-08-24-route-recorder-and-ui-design.md.
"""
import json
import threading
import http.server
import socketserver
import urllib.parse

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

    def start(self, mode):
        if self.mode != "idle":
            return False, f"busy ({self.mode})"
        if mode not in self._MODE_NAME:
            return False, f"unknown mode {mode}"
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

    def farm():
        def _enemy_check():
            return len(recovery.get_enemy()) > 0

        def _panic():
            kb.safe_release_all()
            print("[panel] safety -> release keys and PAUSE (F8 to resume).")
            kb.pause = True

        def _go():
            kb.pause = False
            recovery.farming_loop_nav(exp_check=None, enemy_check=_enemy_check, panic=_panic)
        _run_bg(_go)

    def watch():
        _run_bg(recovery.watch_loop)

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

    return {"farm": farm, "watch": watch, "recover": recover,
            "make_recorder": make_recorder, "pause_toggle": pause_toggle, "stop": stop}


def _status_dict(controller):
    import recovery
    st = dict(recovery.STATUS)
    st["mode"] = controller.mode
    st["recorder_marks"] = controller.recorder.marks if controller.recorder else 0
    return st


PAGE = """<!doctype html><html><head><meta charset=utf-8><title>maple control</title>
<style>
 body{margin:0;font-family:system-ui;background:#14141a;color:#eee}
 #wrap{max-width:640px;margin:0 auto;padding:18px}
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
</style></head><body><div id=wrap>
 <h1>MapleStory bot — control panel</h1>
 <div id=lie class=ok>lie-check: OK</div>
 <div class=row>
   <button class=go onclick="cmd('start','farm')">▶ Start farm</button>
   <button onclick="cmd('pause')">⏸ Pause / Resume</button>
   <button class=stop onclick="cmd('stop')">■ Stop</button>
 </div>
 <div class=row>
   <button onclick="cmd('start','recover')">↥ Recover to top</button>
   <button class=go onclick="cmd('start','watch')">👁 Watch-only (lie-check)</button>
 </div>
 <fieldset><legend>Record route</legend>
   <div class=row>
     <input id=map placeholder="map name (e.g. blue_dragon)" value="blue_dragon">
     <button class=go onclick="recStart()">● Start recording</button>
   </div>
   <div class=row>
     <input id=node placeholder="node name (blank = auto)">
     <button onclick="recMark()">＋ Mark node</button>
     <button class=stop onclick="cmd('record_stop')">■ Stop recording</button>
   </div>
 </fieldset>
 <div id=stat>loading…</div>
</div>
<script>
 async function cmd(action, mode){
   const q = new URLSearchParams({action}); if(mode) q.set('mode', mode);
   const r = await fetch('/cmd?'+q); const j = await r.json();
   if(!j.ok) alert(j.msg);
 }
 async function recStart(){
   const q = new URLSearchParams({action:'record_start', map:document.getElementById('map').value});
   const j = await (await fetch('/cmd?'+q)).json(); if(!j.ok) alert(j.msg);
 }
 async function recMark(){
   const q = new URLSearchParams({action:'record_mark', name:document.getElementById('node').value});
   const j = await (await fetch('/cmd?'+q)).json();
   if(j.ok){ document.getElementById('node').value=''; } else alert(j.msg);
 }
 async function poll(){
   try{
     const s = await (await fetch('/status')).json();
     const lie = document.getElementById('lie');
     lie.className = s.lie ? 'bad' : 'ok';
     lie.textContent = s.lie ? '⚠ lie-check: NEEDS HUMAN' : 'lie-check: OK';
     document.getElementById('stat').textContent =
       `mode: ${s.mode}\nstate: ${s.state}\nnode: ${s.node}\ndragons: ${s.count}\nmarks: ${s.recorder_marks}`;
   }catch(e){}
   setTimeout(poll, 500);
 }
 poll();
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
            if parsed.path == "/status":
                return self._send(200, json.dumps(_status_dict(controller)))
            if parsed.path == "/cmd":
                action = q.get("action", [""])[0]
                ok, msg = self._dispatch(action, q)
                return self._send(200, json.dumps({"ok": ok, "msg": msg}))
            return self._send(404, json.dumps({"ok": False, "msg": "not found"}))

        def _dispatch(self, action, q):
            if action == "start":
                return controller.start(q.get("mode", [""])[0])
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
            return False, f"unknown action {action}"

        def log_message(self, *a):
            pass                                   # quiet
    return Handler


def serve(port=PORT):
    import keyboard as kb
    from pynput.keyboard import Listener
    ref = [None]
    controller = Controller(_build_actions(ref))
    ref[0] = controller
    lis = Listener(on_press=kb.on_press)           # keep F8 pause working
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
