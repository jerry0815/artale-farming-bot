import numpy as np
import monsters

def test_build_mask_excludes_green():
    img = np.zeros((4, 4, 3), np.uint8)
    img[:, :] = (0, 255, 0)          # all chroma green
    img[1, 1] = (200, 50, 50)        # one dragon-blue pixel
    mask = monsters.build_mask(img)
    assert mask[1, 1] == 255
    assert mask[0, 0] == 0
    assert mask.dtype == np.uint8

def test_load_templates_returns_pairs_and_caps_count():
    tpls = monsters.load_templates(max_templates=8)
    assert 1 <= len(tpls) <= 8
    img, mask = tpls[0]
    assert img.shape[:2] == mask.shape[:2]
    assert max(img.shape[:2]) <= 120     # downscaled

def test_load_templates_missing_folder_returns_empty():
    assert monsters.load_templates(folder="does/not/exist") == []

def _paste(canvas, tile_bgr, x, y):
    mask = monsters.build_mask(tile_bgr)
    h, w = tile_bgr.shape[:2]
    roi = canvas[y:y+h, x:x+w]
    roi[mask > 0] = tile_bgr[mask > 0]     # paste only non-green pixels

def test_detect_counts_two_pasted_dragons():
    tpls = monsters.load_templates(max_templates=1)
    assert tpls, "need at least one template"
    tile = tpls[0][0]                       # the (downscaled) template image, green bg
    h, w = tile.shape[:2]
    canvas = np.zeros((h + 40, w * 3 + 60, 3), np.uint8)   # black playfield
    _paste(canvas, tile, 10, 20)
    _paste(canvas, tile, w + 40, 20)
    roi = (0, 0, canvas.shape[1], canvas.shape[0])
    boxes = monsters.detect_dragons(canvas, roi, tpls, diff_thres=0.30)
    assert len(boxes) == 2

def test_detect_empty_frame_is_zero():
    tpls = monsters.load_templates(max_templates=1)
    canvas = np.zeros((200, 300, 3), np.uint8)
    boxes = monsters.detect_dragons(canvas, (0, 0, 300, 200), tpls, diff_thres=0.30)
    assert boxes == []

def test_is_depleted_threshold():
    assert monsters.is_depleted(1) is True
    assert monsters.is_depleted(0) is True
    assert monsters.is_depleted(2) is False
    assert monsters.is_depleted(3) is False

def test_count_from_frames_is_median(monkeypatch):
    # stub detect_dragons to return controlled per-frame counts 3,3,0 -> median 3
    seq = iter([[( 0,0,1,1)]*3, [(0,0,1,1)]*3, []])
    monkeypatch.setattr(monsters, "detect_dragons", lambda *a, **k: next(seq))
    frames = [object(), object(), object()]
    assert monsters.count_from_frames(frames, (0,0,10,10), templates=[]) == 3

def test_count_dragons_uses_capture_fn(monkeypatch):
    grabbed = {"n": 0}
    def fake_capture():
        grabbed["n"] += 1
        return np.zeros((10, 10, 3), np.uint8)
    monkeypatch.setattr(monsters, "count_from_frames", lambda frames, *a, **k: len(frames))
    n = monsters.count_dragons(fake_capture, templates=[], samples=3, interval=0)
    assert n == 3 and grabbed["n"] == 3

def test_roi_by_node_covers_farm_nodes():
    assert set(monsters.ROI_BY_NODE) == {"TOP_FARM", "BOTTOM_FARM"}
    for roi in monsters.ROI_BY_NODE.values():
        assert len(roi) == 4

def test_load_templates_sorts_numerically():
    # the sort key is numeric, so _2 sorts before _10 (lexicographic would reverse this)
    assert monsters._frame_num("blue_wing_dragon_2.png") == 2
    assert monsters._frame_num("blue_wing_dragon_10.png") == 10
