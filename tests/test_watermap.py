"""Tests for the pure water-navigation helpers."""
import watermap


def test_swim_keys_directions():
    # target up-left of cur -> up + left
    assert watermap.swim_keys((100, 100), (80, 60)) == {"left", "up"}
    # target down-right -> down + right
    assert watermap.swim_keys((100, 100), (130, 140)) == {"right", "down"}
    # straight up (smaller y)
    assert watermap.swim_keys((100, 100), (100, 50)) == {"up"}
    # straight down
    assert watermap.swim_keys((100, 100), (100, 150)) == {"down"}


def test_swim_keys_arrived_within_tol():
    assert watermap.swim_keys((100, 100), (101, 99), tol=(3, 3)) == set()
    assert watermap.arrived((100, 100), (102, 98), tol=(3, 3)) is True
    assert watermap.arrived((100, 100), (100, 120), tol=(3, 3)) is False


def test_node_centers():
    route = {"nodes": [{"name": "A", "band": [10, 20, 40, 60]},
                       {"name": "B", "band": [0, 10, 0, 10]}]}
    c = watermap.node_centers(route)
    assert c["A"] == (50, 15)      # ((40+60)//2, (10+20)//2)
    assert c["B"] == (5, 5)


def test_nearest_node():
    centers = {"A": (50, 15), "B": (5, 5)}
    assert watermap.nearest_node((6, 6), centers) == "B"
    assert watermap.nearest_node((49, 16), centers) == "A"
    assert watermap.nearest_node((0, 0), {}) is None


def test_next_farm_rotation():
    farm = ["P_BOT", "P_MID", "P_TOP"]
    assert watermap.next_farm("P_BOT", farm) == "P_MID"
    assert watermap.next_farm("P_TOP", farm) == "P_BOT"      # wraps
    assert watermap.next_farm("unknown", farm) == "P_BOT"    # not in list -> start
    assert watermap.next_farm("x", []) is None


def test_load_map_and_accessors(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"name":"deep_sea_2","minimap":{"x":20,"y":171,"w":210,"h":390},'
                 '"nodes":[],"farm_nodes":[],"swim":{"tol_x":4,"tol_y":2}}')
    cfg = watermap.load_map(str(p))
    assert cfg["name"] == "deep_sea_2"
    assert watermap.minimap_crop(cfg) == (20, 171, 210, 390)
    assert watermap.swim_tol(cfg) == (4, 2)


def test_swim_tol_defaults():
    assert watermap.swim_tol({"minimap": {}}) == (3, 3)
