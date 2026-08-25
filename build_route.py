"""Pure, offline transform: a recorded capture -> a route.json node-graph.

No game, no screen capture. Import-safe and fully unit-testable. See
docs/superpowers/specs/2026-08-24-route-recorder-and-ui-design.md.

Capture records (JSONL, one dict per line):
  position  {"t": float, "x": int, "y": int}
  key       {"t": float, "key": str, "ev": "down"|"up"}
  mark      {"t": float, "mark": "NODE", "name": str}

Minimap convention: y DECREASES going up (climbing a rope lowers y).
"""
import json


def split_records(records):
    """Partition raw capture records into (positions, keys, marks), each sorted by t.
    position: has 'x'; mark: has 'mark'; otherwise a key event."""
    positions, keys, marks = [], [], []
    for r in records:
        if "x" in r:
            positions.append(r)
        elif "mark" in r:
            marks.append(r)
        else:
            keys.append(r)
    for lst in (positions, keys, marks):
        lst.sort(key=lambda r: r["t"])
    return positions, keys, marks


def node_band(positions, mark_t, window=0.6, margin=2):
    """Bounding box [ylo, yhi, xlo, xhi] (padded by margin) of positions within
    +/-window seconds of mark_t. Falls back to the single nearest position if the
    window is empty."""
    near = [p for p in positions if abs(p["t"] - mark_t) <= window]
    if not near:
        near = [min(positions, key=lambda p: abs(p["t"] - mark_t))]
    xs = [p["x"] for p in near]
    ys = [p["y"] for p in near]
    return [min(ys) - margin, max(ys) + margin, min(xs) - margin, max(xs) + margin]


def _first_down(keys, names):
    for k in keys:
        if k.get("ev") == "down" and k.get("key") in names:
            return k
    return None


def classify_traversal(positions, keys, up_keys=("up",), jump_keys=("alt_l",), dy_thresh=4):
    """Classify one traversal (records strictly between two marks) into a
    (kind, params) pair.

      rope     -> an up-key held and y fell (rose on screen) by more than dy_thresh.
                  params = {grab_x, land_y, dismount}
      downjump -> a jump-key pressed and y rose (fell on screen) by more than dy_thresh.
                  params = {}
      walk     -> otherwise. params = {target_x}
    """
    up_keys, jump_keys = set(up_keys), set(jump_keys)
    y0 = positions[0]["y"]
    y_last = positions[-1]["y"]
    min_p = min(positions, key=lambda p: p["y"])   # highest point (smallest y)
    up_ev = _first_down(keys, up_keys)
    jump_ev = _first_down(keys, jump_keys)

    if up_ev is not None and (y0 - min_p["y"]) > dy_thresh:
        grab_p = min(positions, key=lambda p: abs(p["t"] - up_ev["t"]))
        dismount = None
        for k in keys:
            if (k.get("ev") == "down" and k.get("key") in ("left", "right")
                    and k["t"] >= min_p["t"]):
                dismount = k["key"]
                break
        return "rope", {"grab_x": grab_p["x"], "land_y": y_last, "dismount": dismount}

    if jump_ev is not None and (y_last - y0) > dy_thresh:
        return "downjump", {}

    return "walk", {"target_x": positions[-1]["x"]}


def build_route(records, map_name, farm_nodes=None, **cfg):
    """Assemble a route dict from a capture. One node per (first) mark; one edge per
    consecutive pair of marks, from the positions/keys between their timestamps."""
    positions, keys, marks = split_records(records)
    nodes, seen = [], set()
    for m in marks:
        if m["name"] in seen:
            continue
        seen.add(m["name"])
        nodes.append({"name": m["name"],
                      "band": node_band(positions, m["t"],
                                        window=cfg.get("window", 0.6),
                                        margin=cfg.get("margin", 2))})
    edges = []
    for a, b in zip(marks, marks[1:]):
        seg_pos = [p for p in positions if a["t"] <= p["t"] <= b["t"]]
        seg_keys = [k for k in keys if a["t"] <= k["t"] <= b["t"]]
        if len(seg_pos) < 2 or a["name"] == b["name"]:
            continue
        kind, params = classify_traversal(seg_pos, seg_keys,
                                          up_keys=cfg.get("up_keys", ("up",)),
                                          jump_keys=cfg.get("jump_keys", ("alt_l",)),
                                          dy_thresh=cfg.get("dy_thresh", 4))
        edges.append({"src": a["name"], "dst": b["name"], "kind": kind, "params": params})
    return {"map": map_name, "nodes": nodes, "edges": edges,
            "farm_nodes": farm_nodes or []}


def load_capture(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_route(route, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(route, fh, indent=2)


def main():
    import sys
    if len(sys.argv) < 4:
        print("usage: python build_route.py <capture.jsonl> <out.route.json> <map> [farm_csv]")
        return
    cap, out, mp = sys.argv[1], sys.argv[2], sys.argv[3]
    farm = sys.argv[4].split(",") if len(sys.argv) > 4 else []
    route = build_route(load_capture(cap), mp, farm_nodes=farm)
    write_route(route, out)
    print(f"[build_route] {len(route['nodes'])} nodes, {len(route['edges'])} edges -> {out}")


if __name__ == "__main__":
    main()
