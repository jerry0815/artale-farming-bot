"""Live player-position locator: find the exact minimap x of a portal from BOTH sides.

    python locate_player.py flat_sweep

Play by hand (it presses no keys). The console shows your minimap (x, y) live. Every UP press is
logged automatically with WHERE you stood, which way you were WALKING into it (last left/right
arrow), and whether the portal took you (minimap y changed by >= 8 within 2 s). F9 marks any spot
by hand. F10 quits and prints, per arrival side, the x range where UP worked vs failed -- put those
numbers in the route. Marks are also saved to routes/<map>.marks.jsonl.
"""
import json
import os
import statistics
import sys
import threading
import time as _time

OUT_DIR = "routes"


def summarize(marks):
    """Per arrival side: x values where UP worked / failed, plus manual F9 marks.
    marks: [{"kind": "up"|"mark", "x", "y", "side": "left"|"right"|None, "ok": bool|None}].
    Returns {side: {"ok": [x...], "fail": [x...], "mark": [x...]}} with sorted lists."""
    out = {}
    for m in marks:
        side = m.get("side") or "unknown"
        d = out.setdefault(side, {"ok": [], "fail": [], "mark": []})
        if m["kind"] == "up":
            d["ok" if m.get("ok") else "fail"].append(m["x"])
        else:
            d["mark"].append(m["x"])
    for d in out.values():
        for k in d:
            d[k].sort()
    return out


def format_summary(summary):
    lines = []
    for side, d in sorted(summary.items()):
        lines.append(f"  walking {side.upper()} into it:")
        if d["ok"]:
            lines.append(f"    UP worked at x = {d['ok']}  -> range {d['ok'][0]}..{d['ok'][-1]}, "
                         f"median {int(statistics.median(d['ok']))}")
        if d["fail"]:
            lines.append(f"    UP failed at x = {d['fail']}")
        if d["mark"]:
            lines.append(f"    F9 marks at x = {d['mark']}")
    return "\n".join(lines) if lines else "  (no UP presses or marks)"


def locate_live(map_name, hz=20, up_wait=2.0, y_change=8):
    from pynput.keyboard import Key, Listener
    import core

    map_path = map_name if os.path.exists(map_name) else os.path.join("maps", f"{map_name}.json")
    cfg = core.watermap.load_map(map_path)
    core.apply_map_minimap(cfg)
    if cfg.get("map_box"):
        core.set_map_box(*cfg["map_box"])
    name = os.path.splitext(os.path.basename(map_path))[0]
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{name}.marks.jsonl")
    fh = open(out_path, "a", encoding="utf-8")

    state = {"stop": False, "side": None, "pos": (-1, -1), "up_t": 0.0}
    marks = []
    lock = threading.Lock()

    def record(kind, x, y, ok=None):
        m = {"kind": kind, "x": int(x), "y": int(y), "side": state["side"], "ok": ok,
             "ts": _time.strftime("%H:%M:%S")}
        with lock:
            marks.append(m)
            fh.write(json.dumps(m) + "\n"); fh.flush()
        tag = {True: "WORKED", False: "failed", None: ""}[ok]
        print(f"\n[{kind.upper()}] x={x} y={y} walking {state['side'] or '?'} {tag}")

    def check_up(x, y):
        """After an UP press: did the portal take her (minimap level change)?"""
        t_end = _time.time() + up_wait
        hits = 0                                       # 2 reads in a row: a phantom blob can't fake it
        while _time.time() < t_end:
            nx, ny = core.get_character_color(near=(x, y))
            hits = hits + 1 if (ny >= 0 and abs(ny - y) >= y_change) else 0
            if hits >= 2:
                record("up", x, y, ok=True); return
            _time.sleep(0.05)
        record("up", x, y, ok=False)

    def on_press(key):
        if key == Key.f10:
            state["stop"] = True
            return False
        if key in (Key.left, Key.right):
            state["side"] = "left" if key == Key.left else "right"
        elif key == Key.up:
            x, y = state["pos"]                        # where she stood at the press
            now = _time.time()
            if x >= 0 and now - state["up_t"] > 1.0:   # held UP key-repeats: log the first press
                state["up_t"] = now
                threading.Thread(target=check_up, args=(x, y), daemon=True).start()
        elif key == Key.f9:
            x, y = core.stable_char(3)
            if x >= 0:
                record("mark", x, y)

    print(f"[locate] {map_path}: walk into the portal and press UP (logged automatically); "
          f"F9 = mark a spot, F10 = quit")
    listener = Listener(on_press=on_press)
    listener.start()
    last = None
    try:
        while not state["stop"]:
            x, y = core.get_character_color(near=last) if last else core.get_character_full()
            if x >= 0:
                last = (x, y)
                state["pos"] = (x, y)
                sys.stdout.write(f"\r  x={x:4d}  y={y:4d}  walking {state['side'] or '?':5s}   ")
                sys.stdout.flush()
            _time.sleep(1.0 / hz)
    finally:
        listener.stop()
        fh.close()
    print(f"\n[locate] saved {len(marks)} entries -> {out_path}")
    print(format_summary(summarize(marks)))


if __name__ == "__main__":
    locate_live(sys.argv[1] if len(sys.argv) > 1 else "flat_sweep")
