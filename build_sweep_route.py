"""Turn a human demo run (record-route capture) into a sweep ROUTE: the ordered, cyclic list of
segments the general loop replays (see sweep.py route mode).

    python core.py record-route <map>          # play 2-3 cycles, F10 to stop
    python build_sweep_route.py routes/<map>.capture.jsonl --t0 <s> --t1 <s> [--out maps/<map>.json]

--t0/--t1 bound ONE clean cycle. A segment ends where the walking direction changes or where you
pressed a transition key (up = portal/ladder, alt_l = jump). Same-direction walking split only by
a pause (stopping to kill) is merged. until_x = minimap x where the segment ended; attack = you used
the gather key (z) during it. --out writes the route into the map config (other keys kept).
"""
import bisect
import json
import statistics
import sys

MOVE = ("left", "right")
TRANSITIONS = {"up": "up", "alt_l": "jump"}


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _intervals(recs, t_end):
    """Key presses as (key, t_down, t_up): collapse OS key-repeat downs into one press."""
    held, out = {}, []
    for r in recs:
        if "key" not in r:
            continue
        k = r["key"]
        if r["ev"] == "down" and k not in held:
            held[k] = r["t"]
        elif r["ev"] == "up" and k in held:
            out.append((k, held.pop(k), r["t"]))
    out += [(k, t0, t_end) for k, t0 in held.items()]
    return sorted(out, key=lambda a: a[1])


def extract_segments(recs, t0, t1, y_min=48, min_run=0.15, gather_key="z"):
    """Segments for the cycle in [t0, t1]: [{"dir","until_x","then","attack"}, ...].
    Minimap reads with y < y_min are phantoms (not a real platform level) and are ignored."""
    pos = [r for r in recs if "x" in r and "key" not in r and "mark" not in r
           and r.get("y", 0) >= y_min]
    pt = [p["t"] for p in pos]

    def x_at(t, span=0.2):              # short median: rejects a stray read, little lag
        i = bisect.bisect_right(pt, t)
        near = [p["x"] for p in pos[max(0, i - 50):i] if t - span <= p["t"] <= t]
        if near:
            return int(statistics.median(near))
        j = min(max(i - 1, 0), len(pos) - 1)
        return pos[j]["x"]

    iv = [(k, a, b) for k, a, b in _intervals(recs, t1) if t0 <= a <= t1]
    runs = [[k, a, min(b, t1), None] for k, a, b in iv
            if k in MOVE and min(b, t1) - a >= min_run]   # clip to the window first
    gathers = [a for k, a, _ in iv if k == gather_key]
    # a transition belongs to the last run that ended at/before it (a double jump is one jump)
    for k, a, _ in iv:
        if k in TRANSITIONS:
            done = [r for r in runs if r[2] <= a + 0.05]
            if done and done[-1][3] is None:
                done[-1][3] = TRANSITIONS[k]
    segs = []
    for k, a, b, then in runs:
        if segs and segs[-1]["dir"] == k and segs[-1]["then"] is None:
            segs[-1]["_t1"] = b; segs[-1]["then"] = then          # merge a kill-pause split
        else:
            segs.append({"dir": k, "_t0": a, "_t1": b, "then": then})
    for s in segs:
        s["until_x"] = x_at(s["_t1"])
        s["attack"] = any(s["_t0"] <= g <= s["_t1"] for g in gathers)
        del s["_t0"], s["_t1"]
    return [{"dir": s["dir"], "until_x": s["until_x"], "then": s["then"], "attack": s["attack"]}
            for s in segs]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cap = args[0] if args else "routes/flat_sweep.capture.jsonl"
    opt = lambda n, d=None: sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d
    t0, t1 = float(opt("--t0", 0)), float(opt("--t1", 1e9))
    segs = extract_segments(load(cap), t0, t1)
    for i, s in enumerate(segs, 1):
        print(f"  {i}. {s['dir']:5} until x={s['until_x']:3}  then={s['then'] or '-':4}  "
              f"attack={s['attack']}")
    out = opt("--out")
    if out:
        with open(out, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["route"] = segs
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"[route] wrote {len(segs)} segments -> {out} (key 'route')")


if __name__ == "__main__":
    main()
