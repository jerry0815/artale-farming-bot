"""Water-world map support: pure navigation helpers + per-map config.

Water maps (e.g. 水世界/深海峽谷2) have NO minimap scroll-pin -- the character dot
reads true (x,y) directly -- and free 2D swimming instead of walk/rope/gravity. So
navigation collapses to one idea: press the arrow keys toward a target minimap
coordinate until you arrive. This module holds the pure pieces (import-safe, fully
unit-testable); recovery.py wires the live key presses + screen reads.

A map config JSON (maps/<name>.json):
  {
    "name": "deep_sea_2",
    "minimap": {"x": 20, "y": 171, "w": 210, "h": 390},   # full-minimap crop for THIS map
    "nodes": [{"name": "P_BOT", "band": [ylo, yhi, xlo, xhi]}, ...],
    "farm_nodes": ["P_BOT", "P_MID", "P_TOP"],
    "swim": {"tol_x": 3, "tol_y": 3}
  }
Node bands come straight from a recording via build_route; farm order is the rotation.
"""
import json


def swim_keys(cur, target, tol=(3, 3)):
    """Which arrow keys to HOLD to swim from cur=(x,y) toward target=(x,y).

    Minimap convention: smaller y is HIGHER on the map, so a target above (smaller y)
    -> 'up'. Returns a set drawn from {'left','right','up','down'}; an empty set means
    'arrived' (within tolerance on both axes).

    `tol` is (tol_x, tol_y) symmetric, OR (tol_x, tol_below, tol_above) for an ASYMMETRIC
    y-band: `tol_below` = how far the player may sit BELOW the target and still count as
    arrived (keep tight -- below means she hasn't risen to the platform yet); `tol_above` =
    how far ABOVE (jumped high -- she'll sink onto it, so this can be generous)."""
    cx, cy = cur
    tx, ty = target
    tol_x = tol[0]
    tol_below = tol[1]
    tol_above = tol[2] if len(tol) >= 3 else tol[1]
    keys = set()
    if tx < cx - tol_x:
        keys.add("left")
    elif tx > cx + tol_x:
        keys.add("right")
    if ty < cy - tol_below:        # player is BELOW target (lower) by > tol_below -> rise
        keys.add("up")
    elif ty > cy + tol_above:      # player is ABOVE target (higher) by > tol_above -> sink
        keys.add("down")
    return keys


def arrived(cur, target, tol=(3, 3)):
    """True once cur is within tolerance of target on both axes."""
    return not swim_keys(cur, target, tol)


def node_centers(route):
    """Map node name -> (cx, cy) center of its band. Band is [ylo, yhi, xlo, xhi]."""
    out = {}
    for n in route["nodes"]:
        ylo, yhi, xlo, xhi = n["band"]
        out[n["name"]] = ((xlo + xhi) // 2, (ylo + yhi) // 2)
    return out


def nearest_node(cur, centers):
    """Name of the node center closest to cur=(x,y) (squared-distance). None if empty."""
    if not centers:
        return None
    cx, cy = cur
    return min(centers, key=lambda k: (centers[k][0] - cx) ** 2 + (centers[k][1] - cy) ** 2)


def next_farm(current, farm_nodes):
    """Next platform in the rotation after `current` (wraps). If `current` isn't in the
    list, start at the first farm node."""
    if not farm_nodes:
        return None
    if current not in farm_nodes:
        return farm_nodes[0]
    i = farm_nodes.index(current)
    return farm_nodes[(i + 1) % len(farm_nodes)]


def load_map(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def minimap_crop(cfg):
    """(x, y, w, h) crop tuple for recovery.set_minimap, from a map config."""
    m = cfg["minimap"]
    return (m["x"], m["y"], m["w"], m["h"])


def swim_tol(cfg):
    """(tol_x, tol_y) or, if the map sets tol_y_down/tol_y_up, the asymmetric
    (tol_x, tol_below, tol_above) 3-tuple that swim_keys accepts."""
    s = cfg.get("swim", {})
    tol_x = s.get("tol_x", 3)
    if "tol_y_down" in s or "tol_y_up" in s:
        base = s.get("tol_y", 3)
        return (tol_x, s.get("tol_y_down", base), s.get("tol_y_up", base))
    return (tol_x, s.get("tol_y", 3))


def node_swim_tol(cfg, node, base):
    """`base` swim_tol with tol_y_up (the ABOVE band) overridden for `node` when
    node_tol_y_up_override sets it. Tightening tol_y_up on a stacked pin (P4) forces her to
    actually rise near the lifted target to count as arrived, instead of 'arriving' anywhere
    across the wide default above-band. Only applies to the 3-tuple asymmetric form; a
    symmetric base is returned unchanged (there's no separate above-band to tighten)."""
    ov = cfg.get("node_tol_y_up_override", {})
    if node in ov and len(base) >= 3:
        return (base[0], base[1], int(ov[node]))
    return base
