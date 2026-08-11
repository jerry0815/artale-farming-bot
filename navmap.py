"""Node-graph map of the blue-dragon-nest map + pathfinding + travel.

Pure module (stdlib only): the graph, node classification, and path planning
are testable with no live game. `travel()` takes injected locate/execute
callables so it is testable too. recovery.py wires the real ones.

Node y-bands are in the minimap frame that recovery.get_character_color()
returns (offset 20,171 -> ROPE_X=91). They mirror recovery.py constants.
"""

NODES = ["TOP_FARM", "REST", "MID", "BOTTOM_FARM", "LOWER_LEDGE"]
FARM_NODES = ["TOP_FARM", "BOTTOM_FARM"]

# (name, y_lo, y_hi, x_lo, x_hi) inclusive bands; ordered so the first match wins.
_BANDS = [
    ("TOP_FARM",     0,  95, 60, 140),
    ("REST",        96, 110, 60, 100),
    ("MID",        111, 131, 80, 140),
    ("BOTTOM_FARM",132, 156, 55, 100),
    ("LOWER_LEDGE",157, 200, 40, 160),
]

def classify_node(x, y):
    if x is None or y is None or x < 0 or y < 0:
        return None
    for name, ylo, yhi, xlo, xhi in _BANDS:
        if ylo <= y <= yhi and xlo <= x <= xhi:
            return name
    return None

from collections import deque

# Executable edges. Downjumps go one level DOWN through a gap; ropes climb UP.
# `rope` names the physical rope (metadata; per-rope wiring is a live follow-up).
EDGES = [
    {"src": "TOP_FARM",     "dst": "REST",        "kind": "downjump", "rope": None},
    {"src": "TOP_FARM",     "dst": "BOTTOM_FARM", "kind": "downjump", "rope": None},
    {"src": "REST",         "dst": "BOTTOM_FARM", "kind": "downjump", "rope": None},
    {"src": "REST",         "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_L"},
    {"src": "MID",          "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_L"},
    {"src": "BOTTOM_FARM",  "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_C"},
    {"src": "LOWER_LEDGE",  "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_C"},
]

def neighbors(node):
    return [e for e in EDGES if e["src"] == node]

def plan(src, dst):
    if src not in NODES or dst not in NODES:
        return None
    if src == dst:
        return []
    # BFS over EDGES, fewest hops.
    q = deque([(src, [])])
    seen = {src}
    while q:
        node, path = q.popleft()
        for e in neighbors(node):
            if e["dst"] in seen:
                continue
            new_path = path + [e]
            if e["dst"] == dst:
                return new_path
            seen.add(e["dst"])
            q.append((e["dst"], new_path))
    return None

def next_farm_target(current, dragon_count, threshold=2):
    if dragon_count >= threshold:
        return None
    if current not in FARM_NODES:
        return None
    return FARM_NODES[1] if current == FARM_NODES[0] else FARM_NODES[0]

def travel(dst, locate_fn, execute_fn, max_rounds=8):
    for _ in range(max_rounds):
        cur = locate_fn()
        if cur == dst:
            return True
        if cur is None:
            return False                 # lost -> caller decides (recover/panic)
        path = plan(cur, dst)
        if not path:                     # unreachable or already there handled above
            return cur == dst
        edge = path[0]                   # execute one hop, then re-localize + re-plan
        execute_fn(edge)                 # success is judged by re-localizing, not by return
    return locate_fn() == dst
