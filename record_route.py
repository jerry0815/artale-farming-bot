"""Sense-only route recorder.

Logs (t,x,y) position samples + key down/up events + node marks to a
`.capture.jsonl` while you play the map manually. Presses NO keys -- zero risk.
`build_route.py` turns the capture into a route.json node-graph.

CLI (via recovery.py):  python recovery.py record-route <map_name>
Hotkeys while recording:  F10 = stop,  F9 = mark a node,  F8 = existing pause.

See docs/superpowers/specs/2026-08-24-route-recorder-and-ui-design.md.
"""
import json
import time as _time

OUT_DIR = "routes"


def record(get_xy, out_path, poll_hz=30, is_paused=lambda: False,
           stop_after=None, clock=_time.time, sleep=_time.sleep, listener=None):
    """Poll get_xy() -> (x,y) at poll_hz, appending {t,x,y} records to out_path.

    Pure w.r.t. the game: presses no keys. Stops after `stop_after` samples (test
    hook) or when `listener.stopped` becomes True. `clock`/`sleep` are injectable
    for tests. Returns the in-memory record list."""
    period = 1.0 / poll_hz
    t0 = clock()
    recs = []
    with open(out_path, "w", encoding="utf-8") as fh:
        n = 0
        while True:
            if listener is not None and getattr(listener, "stopped", False):
                break
            if not is_paused():
                x, y = get_xy()
                if x is not None and x >= 0:
                    r = {"t": round(clock() - t0, 3), "x": int(x), "y": int(y)}
                    recs.append(r)
                    fh.write(json.dumps(r) + "\n")
                    fh.flush()
                    n += 1
                    if stop_after is not None and n >= stop_after:
                        break
            sleep(period)
    return recs


def _key_name(key):
    """Normalize a pynput key to a short string: Key.up->'up', Key.alt_l->'alt_l',
    char keys -> the char. Unknown -> str(key)."""
    name = getattr(key, "name", None)
    if name is not None:
        return name
    ch = getattr(key, "char", None)
    if ch is not None:
        return ch
    return str(key)


def record_live(map_name, poll_hz=30):
    """Live recorder: wires a real pynput listener (F10 stop, F9 mark, key logging)
    around the position poll, using recovery.get_character_full for positions and
    keyboard.pause (F8) for pausing. Writes routes/<map_name>.capture.jsonl."""
    import os
    from pynput.keyboard import Key, Listener
    import recovery
    import keyboard as kb

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{map_name}.capture.jsonl")
    fh = open(out_path, "w", encoding="utf-8")
    t0 = _time.time()

    state = {"stopped": False, "marks": 0}

    def emit(rec):
        rec["t"] = round(_time.time() - t0, 3)
        fh.write(json.dumps(rec) + "\n")
        fh.flush()

    def on_press(key):
        kb.on_press(key)                       # keep the existing F8 pause behavior
        if key == Key.f10:
            state["stopped"] = True
            return False                       # stop the listener
        if key == Key.f9:
            state["marks"] += 1
            name = None
            try:
                name = input(f"\n[record] node name for mark #{state['marks']} "
                             f"(blank = N{state['marks']}): ").strip()
            except EOFError:
                name = ""
            name = name or f"N{state['marks']}"
            emit({"mark": "NODE", "name": name})
            print(f"[record] marked node '{name}'")
            return
        emit({"key": _key_name(key), "ev": "down"})

    def on_release(key):
        emit({"key": _key_name(key), "ev": "up"})

    lis = Listener(on_press=on_press, on_release=on_release)
    lis.start()
    print(f"[record] recording -> {out_path}")
    print("[record] play the map. F9 = mark node, F10 = stop, F8 = pause.")
    try:
        period = 1.0 / poll_hz
        while not state["stopped"]:
            if not kb.pause:
                x, y = recovery.get_character_full()
                if x is not None and x >= 0:
                    emit({"x": int(x), "y": int(y)})
            _time.sleep(period)
    finally:
        lis.stop()
        fh.close()
    print(f"[record] stopped. {state['marks']} marks. Build with:")
    print(f"  python build_route.py {out_path} {OUT_DIR}/{map_name}.route.json {map_name} <farm_csv>")
    return out_path
