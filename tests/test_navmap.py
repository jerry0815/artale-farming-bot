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
