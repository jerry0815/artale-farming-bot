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

def test_classify_portal_recovery_entry_ledges():
    # The two right-side entry ledges of the portal-bottom recovery chain. Both sit below
    # the overlapping/scroll-pinned mid cluster, so they band cleanly.
    assert navmap.classify_node(153, 183) == "PORTAL_BOT"   # deep, by the portal
    assert navmap.classify_node(140, 149) == "LOWER_R"      # portal-rope landing
    # LOWER_R / LOWER_LEDGE seam: rope-transit just below LOWER_R reads as ledge (y>=152)
    assert navmap.classify_node(132, 152) == "LOWER_LEDGE"
    assert navmap.classify_node(140, 151) == "LOWER_R"      # y==151 inclusive top of LOWER_LEDGE seam
    # the mid cluster is deliberately NOT banded -> None (handled by recover-or-panic fallback)
    assert navmap.classify_node(155, 131) is None

def test_plan_portal_bottom_to_top_is_two_rope_hops():
    # PORTAL_BOT -> LOWER_R -> TOP_FARM, both hops rope (climb up); no downjump out.
    path = navmap.plan("PORTAL_BOT", "TOP_FARM")
    assert path is not None
    assert [e["src"] for e in path] == ["PORTAL_BOT", "LOWER_R"]
    assert path[-1]["dst"] == "TOP_FARM"
    assert all(e["kind"] == "rope" for e in path)
    # a partial fall onto LOWER_R plans straight up
    assert navmap.plan("LOWER_R", "TOP_FARM") == [
        e for e in navmap.EDGES if e["src"] == "LOWER_R" and e["dst"] == "TOP_FARM"
    ]

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

def test_next_farm_state_alternates_when_populated():
    import navmap
    assert navmap.next_farm_state(navmap.STAND_SHOOT, 5) == navmap.WALK_SHOOT
    assert navmap.next_farm_state(navmap.WALK_SHOOT, 5) == navmap.STAND_SHOOT

def test_next_farm_state_rotates_when_few():
    import navmap
    assert navmap.next_farm_state(navmap.STAND_SHOOT, 1) == navmap.ROTATE
    assert navmap.next_farm_state(navmap.WALK_SHOOT, 0, threshold=2) == navmap.ROTATE

def test_next_farm_state_threshold_boundary_keeps_farming():
    import navmap
    # exactly at threshold is NOT "few" -> keep alternating, don't rotate
    assert navmap.next_farm_state(navmap.STAND_SHOOT, 2) == navmap.WALK_SHOOT

def test_rotation_decision_end_to_end():
    import navmap
    # depleted top -> go bottom; then depleted bottom -> go top; populated -> stay
    assert navmap.next_farm_target("TOP_FARM", 1, threshold=2) == "BOTTOM_FARM"
    assert navmap.next_farm_target("BOTTOM_FARM", 1, threshold=2) == "TOP_FARM"
    assert navmap.next_farm_target("TOP_FARM", 4, threshold=2) is None
    # and travel between the two farm nodes is always planttable
    assert navmap.plan("TOP_FARM", "BOTTOM_FARM") is not None
    assert navmap.plan("BOTTOM_FARM", "TOP_FARM") is not None
