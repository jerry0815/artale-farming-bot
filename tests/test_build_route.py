"""Tests for build_route: the pure capture -> route.json transform."""
import build_route


def test_split_records_partitions_and_sorts():
    recs = [
        {"t": 2.0, "x": 90, "y": 100},
        {"t": 0.5, "key": "up", "ev": "down"},
        {"t": 1.0, "mark": "NODE", "name": "TOP"},
        {"t": 0.1, "x": 91, "y": 101},
    ]
    positions, keys, marks = build_route.split_records(recs)
    assert [p["t"] for p in positions] == [0.1, 2.0]
    assert [k["t"] for k in keys] == [0.5]
    assert [m["name"] for m in marks] == ["TOP"]


def test_node_band_bbox_with_margin():
    positions = [
        {"t": 9.0, "x": 200, "y": 200},   # far in time -> excluded
        {"t": 10.0, "x": 60, "y": 85},
        {"t": 10.2, "x": 64, "y": 88},
        {"t": 10.4, "x": 62, "y": 86},
    ]
    band = build_route.node_band(positions, mark_t=10.2, window=0.6, margin=2)
    assert band == [83, 90, 58, 66]   # [min_y-2, max_y+2, min_x-2, max_x+2]


def test_node_band_falls_back_to_nearest_when_window_empty():
    positions = [{"t": 0.0, "x": 100, "y": 140}]
    band = build_route.node_band(positions, mark_t=99.0, window=0.6, margin=2)
    assert band == [138, 142, 98, 102]


def test_classify_rope_extracts_grab_land_dismount():
    positions = [
        {"t": 0.0, "x": 132, "y": 190},
        {"t": 0.5, "x": 132, "y": 160},
        {"t": 1.0, "x": 132, "y": 151},   # min y here
        {"t": 1.4, "x": 140, "y": 151},   # dismounted right
    ]
    keys = [
        {"t": 0.1, "key": "up", "ev": "down"},
        {"t": 1.2, "key": "right", "ev": "down"},
    ]
    kind, params = build_route.classify_traversal(positions, keys)
    assert kind == "rope"
    assert params == {"grab_x": 132, "land_y": 151, "dismount": "right"}


def test_classify_downjump_when_y_rises_with_jump():
    positions = [{"t": 0.0, "x": 63, "y": 85}, {"t": 0.6, "x": 90, "y": 136}]
    keys = [{"t": 0.1, "key": "alt_l", "ev": "down"},
            {"t": 0.1, "key": "right", "ev": "down"}]
    kind, params = build_route.classify_traversal(positions, keys)
    assert kind == "downjump"
    assert params == {}


def test_classify_walk_when_flat():
    positions = [{"t": 0.0, "x": 63, "y": 85}, {"t": 0.4, "x": 112, "y": 85}]
    keys = [{"t": 0.1, "key": "right", "ev": "down"}]
    kind, params = build_route.classify_traversal(positions, keys)
    assert kind == "walk"
    assert params == {"target_x": 112}


def test_build_route_end_to_end():
    recs = [
        {"t": 0.0, "mark": "NODE", "name": "TOP"},
        {"t": 0.0, "x": 63, "y": 85},
        {"t": 0.2, "x": 63, "y": 85},
        {"t": 0.3, "key": "right", "ev": "down"},
        {"t": 0.6, "x": 112, "y": 85},
        {"t": 0.8, "mark": "NODE", "name": "FAR"},
        {"t": 0.8, "x": 112, "y": 85},
    ]
    route = build_route.build_route(recs, "blue_dragon", farm_nodes=["TOP", "FAR"], window=0.3)
    assert route["map"] == "blue_dragon"
    assert route["farm_nodes"] == ["TOP", "FAR"]
    assert [n["name"] for n in route["nodes"]] == ["TOP", "FAR"]
    assert route["nodes"][0]["band"] == [83, 87, 61, 65]
    assert route["edges"] == [
        {"src": "TOP", "dst": "FAR", "kind": "walk", "params": {"target_x": 112}}
    ]


def test_load_capture_and_write_route_roundtrip(tmp_path):
    cap = tmp_path / "c.jsonl"
    cap.write_text('{"t":0.0,"mark":"NODE","name":"A"}\n\n{"t":0.0,"x":1,"y":2}\n')
    recs = build_route.load_capture(str(cap))
    assert len(recs) == 2
    route = build_route.build_route(recs, "m")
    out = tmp_path / "r.json"
    build_route.write_route(route, str(out))
    import json
    assert json.loads(out.read_text())["map"] == "m"


def test_build_route_emits_water_map_config():
    recs = [
        {"t": 0.0, "mark": "NODE", "name": "P_BOT"},
        {"t": 0.0, "x": 60, "y": 200},
        {"t": 0.1, "x": 62, "y": 201},
    ]
    route = build_route.build_route(recs, "deep_sea_2", farm_nodes=["P_BOT"],
                                    minimap=(20, 171, 210, 390), swim=(3, 3))
    assert route["minimap"] == {"x": 20, "y": 171, "w": 210, "h": 390}
    assert route["swim"] == {"tol_x": 3, "tol_y": 3}
    assert route["name"] == "deep_sea_2"
    # usable directly by watermap
    import watermap
    assert watermap.minimap_crop(route) == (20, 171, 210, 390)
    assert "P_BOT" in watermap.node_centers(route)


def test_build_route_uses_exact_mark_position_when_present():
    # A mark carrying its own x,y (captured at F9 instant) -> band centered there,
    # independent of nearby position samples.
    recs = [
        {"t": 0.0, "x": 999, "y": 999},                       # noise, far away
        {"t": 0.5, "mark": "NODE", "name": "P_BOT", "x": 60, "y": 200},
    ]
    route = build_route.build_route(recs, "deep_sea_2", farm_nodes=["P_BOT"])
    assert route["nodes"][0]["band"] == [198, 202, 58, 62]     # centered on (60,200), margin 2
    import watermap
    assert watermap.node_centers(route)["P_BOT"] == (60, 200)


def test_build_route_main_merges_into_existing_config(tmp_path):
    import json, sys, build_route
    cap = tmp_path / "c.jsonl"
    cap.write_text('{"t":0.0,"mark":"NODE","name":"P1","x":60,"y":88}\n'
                   '{"t":0.1,"mark":"NODE","name":"P2","x":140,"y":105}\n')
    out = tmp_path / "m.json"
    # pre-existing config with hand-set keys that must survive a rebuild
    out.write_text(json.dumps({"rotation": "sweep", "reset_node": "RIGHT",
                               "detector": "fish", "nodes": [], "farm_nodes": []}))
    argv = ["build_route.py", str(cap), str(out), "deep_sea_2", "P1,P2",
            "--minimap", "20,171,210,400"]
    old = sys.argv
    try:
        sys.argv = argv
        build_route.main()
    finally:
        sys.argv = old
    cfg = json.loads(out.read_text())
    assert cfg["rotation"] == "sweep"          # preserved
    assert cfg["reset_node"] == "RIGHT"        # preserved
    assert cfg["detector"] == "fish"           # preserved
    assert cfg["farm_nodes"] == ["P1", "P2"]   # regenerated
    assert [n["name"] for n in cfg["nodes"]] == ["P1", "P2"]
    assert cfg["minimap"] == {"x": 20, "y": 171, "w": 210, "h": 400}
