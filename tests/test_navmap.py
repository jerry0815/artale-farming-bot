import navmap

def test_classify_each_node_center():
    assert navmap.classify_node(74, 85) == "TOP_FARM"
    assert navmap.classify_node(77, 105) == "REST"
    assert navmap.classify_node(110, 120) == "MID"
    assert navmap.classify_node(63, 145) == "BOTTOM_FARM"
    assert navmap.classify_node(63, 165) == "LOWER_LEDGE"

def test_classify_band_boundaries():
    assert navmap.classify_node(74, 95) == "TOP_FARM"    # y==95 upper band inclusive
    assert navmap.classify_node(77, 96) == "REST"        # y==96 -> REST (no dead zone)
    assert navmap.classify_node(63, 132) == "BOTTOM_FARM"

def test_classify_unknown_returns_none():
    assert navmap.classify_node(-1, -1) is None          # not detected
    assert navmap.classify_node(74, 250) is None         # deep fall, off-map

def test_plan_same_node_is_empty():
    assert navmap.plan("TOP_FARM", "TOP_FARM") == []

def test_plan_top_to_bottom_exists_and_ends_at_bottom():
    path = navmap.plan("TOP_FARM", "BOTTOM_FARM")
    assert path is not None and len(path) >= 1
    assert path[0]["src"] == "TOP_FARM"
    assert path[-1]["dst"] == "BOTTOM_FARM"

def test_plan_bottom_to_top_uses_rope():
    path = navmap.plan("BOTTOM_FARM", "TOP_FARM")
    assert path is not None
    assert any(e["kind"] == "rope" for e in path)   # climbing up needs a rope

def test_plan_recovery_from_lower_ledge_to_top():
    path = navmap.plan("LOWER_LEDGE", "TOP_FARM")
    assert path is not None and path[-1]["dst"] == "TOP_FARM"

def test_plan_invalid_node_returns_none():
    assert navmap.plan("NOWHERE", "TOP_FARM") is None

def test_next_farm_target_rotates_when_depleted():
    assert navmap.next_farm_target("TOP_FARM", 1) == "BOTTOM_FARM"
    assert navmap.next_farm_target("BOTTOM_FARM", 0) == "TOP_FARM"

def test_next_farm_target_stays_when_populated():
    assert navmap.next_farm_target("TOP_FARM", 2) is None
    assert navmap.next_farm_target("TOP_FARM", 5) is None

def test_travel_already_there():
    calls = []
    ok = navmap.travel("TOP_FARM",
                        locate_fn=lambda: "TOP_FARM",
                        execute_fn=lambda e: calls.append(e) or True)
    assert ok is True and calls == []

def test_travel_executes_planned_edges_until_arrival():
    # locate reports the dst of the last executed edge (as if moves succeed)
    state = {"node": "BOTTOM_FARM"}
    executed = []
    def execute(e):
        executed.append((e["src"], e["dst"]))
        state["node"] = e["dst"]
        return True
    ok = navmap.travel("TOP_FARM",
                       locate_fn=lambda: state["node"],
                       execute_fn=execute)
    assert ok is True and state["node"] == "TOP_FARM"
    assert executed[-1][1] == "TOP_FARM"

def test_travel_gives_up_when_edge_never_advances():
    # execute "succeeds" but locate never moves -> must bail within max_rounds, not loop forever
    ok = navmap.travel("TOP_FARM",
                       locate_fn=lambda: "BOTTOM_FARM",
                       execute_fn=lambda e: True,
                       max_rounds=3)
    assert ok is False

def test_travel_bails_when_lost():
    ok = navmap.travel("TOP_FARM",
                       locate_fn=lambda: None,      # cannot localize
                       execute_fn=lambda e: True,
                       max_rounds=3)
    assert ok is False

def test_every_edge_has_an_executor():
    # only run if recovery.py's heavy deps are importable in this env
    try:
        import recovery
    except Exception as e:
        import pytest
        pytest.skip(f"recovery not importable here: {e}")
    import navmap
    for e in navmap.EDGES:
        assert (e["src"], e["dst"]) in recovery.EDGE_ACTIONS, (e["src"], e["dst"])
