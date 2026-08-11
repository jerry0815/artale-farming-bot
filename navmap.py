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
