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
