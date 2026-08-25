"""Node-graph map of the blue-dragon-nest map + pathfinding + travel.

Pure module (stdlib only): the graph, node classification, and path planning
are testable with no live game. `travel()` takes injected locate/execute
callables so it is testable too. recovery.py wires the real ones.

Node y-bands are in the minimap frame that recovery.get_character_color()
returns (offset 20,171 -> ROPE_X=91). They mirror recovery.py constants.
"""

NODES = ["TOP_FARM", "REST", "MID", "BOTTOM_FARM", "LOWER_R", "PORTAL_BOT", "LOWER_LEDGE"]
FARM_NODES = ["TOP_FARM", "BOTTOM_FARM"]

# (name, y_lo, y_hi, x_lo, x_hi) inclusive bands; ordered so the first match wins.
#
# PORTAL_BOT and LOWER_R are the two RIGHT-side entry ledges of the portal-bottom
# recovery chain (PORTAL_BOT -> LOWER_R -> TOP_FARM). They are the only right-side
# ledges deliberately given bands: both sit well BELOW the overlapping/scroll-pinned
# mid cluster, so they classify cleanly. The mid stretch above LOWER_R (where the old
# MID_R lived) is intentionally NOT banded -- MID/BOTTOM_FARM/right-rope-top all read
# ~y136-142 there and can't be separated by a rectangle, and the climb past it hits the
# minimap scroll-pin. Reads in that zone fall to classify_node None and are handled by
# the nav loop's recover-or-panic fallback (recover_to_farming is scroll-aware and walks
# left to the central rope). See docs/superpowers/specs/2026-08-16-portal-rope-recovery-design.md.
_BANDS = [
    ("TOP_FARM",     0,  95, 60, 140),
    ("REST",        96, 110, 60, 100),
    ("MID",        111, 131, 80, 140),
    ("BOTTOM_FARM",132, 156, 55, 100),
    ("LOWER_R",    143, 151,128, 163),   # portal-rope landing; needs the right rope
    ("PORTAL_BOT", 178, 190,108, 162),   # deep ledge by the portal; before LOWER_LEDGE
    ("LOWER_LEDGE",152, 200, 40, 160),   # y_lo 157->152: rope-transit just below LOWER_R
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
    # PORTAL-BOTTOM recovery (right side). Both are "recover-macro" edges (like R_C):
    # the executor climbs the rope(s) up to the central-reachable mid stretch and then
    # recover_to_farming finishes to the top. PORTAL_BOT->TOP_FARM is adaptive (portal
    # rope, then the right rope only if still on LOWER_R). No downjump edges OUT, so a
    # plan only ever climbs UP out of them.
    {"src": "PORTAL_BOT",   "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_PORTAL"},
    {"src": "LOWER_R",      "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_RIGHT"},
    # NOTE: the R_C edges are logical "recover-macro" edges. Physically the bottom rope-
    # LADDER and the upper CHAIN are STACKED in the same minimap column (verified via synced
    # screen+minimap capture) with a vertical jump between them at the ladder top (~y124) --
    # NOT a horizontal gap. recovery.climb_and_jump rides both by holding Up (bridging the
    # gap with one jump if it stalls). The graph keeps a single BOTTOM_FARM->TOP_FARM edge (executed by
    # recover_to_farming) so navmap.travel needs no per-rope executor. Do not split it into
    # BOTTOM_FARM->MID without also adding an EDGE_ACTIONS executor for that hop.
    {"src": "BOTTOM_FARM",  "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_C"},
    {"src": "LOWER_LEDGE",  "dst": "TOP_FARM",    "kind": "rope",     "rope": "R_C"},
]

import json as _json

# Built-in defaults (the hand-tuned graph) so load_route is reversible.
_DEFAULT_NODES = list(NODES)
_DEFAULT_FARM_NODES = list(FARM_NODES)
_DEFAULT_BANDS = list(_BANDS)
_DEFAULT_EDGES = [dict(e) for e in EDGES]


def load_route(route_or_path):
    """Replace the module graph from a route dict or a route.json path.

    Backward compatible: if this is never called, the hand-tuned defaults above
    stay in force. Edge params (grab_x, land_y, dismount, target_x, ...) are
    flattened onto each EDGES entry so recovery.execute_edge can read them, and
    the `kind` key is preserved for the data-driven dispatch."""
    global NODES, FARM_NODES, _BANDS, EDGES
    route = route_or_path
    if isinstance(route_or_path, str):
        with open(route_or_path, encoding="utf-8") as fh:
            route = _json.load(fh)
    NODES = [n["name"] for n in route["nodes"]]
    FARM_NODES = list(route.get("farm_nodes", []))
    _BANDS = [(n["name"], *n["band"]) for n in route["nodes"]]
    EDGES = [dict(src=e["src"], dst=e["dst"], kind=e.get("kind", "walk"),
                  **e.get("params", {})) for e in route["edges"]]


def reset_route():
    """Restore the built-in hand-tuned graph."""
    global NODES, FARM_NODES, _BANDS, EDGES
    NODES = list(_DEFAULT_NODES)
    FARM_NODES = list(_DEFAULT_FARM_NODES)
    _BANDS = list(_DEFAULT_BANDS)
    EDGES = [dict(e) for e in _DEFAULT_EDGES]


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

# Farming states (per platform). STAND_SHOOT = park at home and fire in place;
# WALK_SHOOT = sweep out-and-back shooting. They alternate while the platform is
# populated; a low count rotates to the next platform instead.
STAND_SHOOT, WALK_SHOOT, ROTATE = "STAND_SHOOT", "WALK_SHOOT", "ROTATE"

def next_farm_state(prev_state, dragon_count, threshold=2):
    """Transition for the farming state machine, evaluated at a standstill between
    moves. Returns ROTATE when the platform is depleted (count < threshold), else
    alternates STAND_SHOOT <-> WALK_SHOOT."""
    if dragon_count < threshold:
        return ROTATE
    return WALK_SHOOT if prev_state == STAND_SHOOT else STAND_SHOOT

def travel(dst, locate_fn, execute_fn, max_rounds=8):
    for _ in range(max_rounds):
        cur = locate_fn()
        if cur == dst:
            return True
        if cur is None:
            return False                 # lost -> caller decides (recover/panic)
        path = plan(cur, dst)
        if not path:                     # unreachable or already there handled above
            return False   # unreachable (cur == dst already handled above)
        edge = path[0]                   # execute one hop, then re-localize + re-plan
        execute_fn(edge)                 # success is judged by re-localizing, not by return
    return locate_fn() == dst
