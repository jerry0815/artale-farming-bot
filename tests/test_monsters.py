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

# --- motion-based counting -------------------------------------------------

def test_estimate_count_rounds_area_over_dragon_area():
    assert monsters.estimate_count(0) == 0
    assert monsters.estimate_count(16000, dragon_area=16000) == 1
    assert monsters.estimate_count(40000, dragon_area=16000) == 2   # 2.5 -> round -> 2
    assert monsters.estimate_count(56000, dragon_area=16000) == 4   # 3.5 -> round-half-even -> 4

def test_background_plate_is_per_pixel_median():
    # 3 frames; the moving bright pixel appears in only 1 -> median keeps the background
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.zeros((4, 4, 3), np.uint8); b[1, 1] = (255, 255, 255)
    bg = monsters.background_plate([a, b, a])
    assert bg[1, 1].tolist() == [0, 0, 0]        # the transient bright pixel is filtered out

def test_foreground_area_detects_moving_blob_in_roi_only():
    bg = np.zeros((300, 300, 3), np.uint8)
    frame = bg.copy()
    frame[40:240, 40:240] = (255, 255, 255)      # a big 200x200 "dragon" appears (motion)
    roi = (0, 0, 300, 300)
    area, boxes = monsters.foreground_area(frame, bg, roi, diff_thr=45, min_blob=1500)
    assert area > 30000 and len(boxes) == 1
    # same blob but ROI excludes it -> no foreground counted
    area2, boxes2 = monsters.foreground_area(frame, bg, (250, 250, 300, 300))
    assert area2 == 0 and boxes2 == []

def test_foreground_area_ignores_specks_below_min_blob():
    bg = np.zeros((100, 100, 3), np.uint8)
    frame = bg.copy(); frame[10:14, 10:14] = (255, 255, 255)   # tiny 4x4 speck
    area, boxes = monsters.foreground_area(frame, bg, (0, 0, 100, 100), min_blob=1500)
    assert area == 0 and boxes == []

def test_motion_roi_by_node_covers_farm_nodes():
    assert set(monsters.MOTION_ROI_BY_NODE) == {"TOP_FARM", "BOTTOM_FARM"}
    for roi in monsters.MOTION_ROI_BY_NODE.values():
        assert len(roi) == 4

def test_count_dragons_motion_bails_without_enough_frames():
    # capture returns None -> fewer than 3 frames -> returns 0, never divides
    assert monsters.count_dragons_motion(lambda: None, samples=2, interval=0) == 0

def test_box_center_in_roi_inside_and_outside():
    assert monsters.box_center_in_roi((0, 0, 10, 10), (0, 0, 50, 50)) is True
    assert monsters.box_center_in_roi((100, 100, 110, 110), (0, 0, 50, 50)) is False

def test_detect_dragons_yolo_keeps_only_in_band(monkeypatch):
    raw = [(0, 0, 10, 10, 0.9), (100, 100, 110, 110, 0.8)]
    monkeypatch.setattr(monsters, "run_yolo", lambda model, frame, conf=0.35: raw)
    out = monsters.detect_dragons_yolo(frame=object(), model=object(), roi=(0, 0, 50, 50))
    assert out == [(0, 0, 10, 10, 0.9)]

def test_count_dragons_yolo_is_median_of_box_counts(monkeypatch):
    # per-frame detections of length 3, 3, 0 -> sorted [0,3,3] -> median 3
    seq = iter([[(0,0,1,1,0.9)]*3, [(0,0,1,1,0.9)]*3, []])
    monkeypatch.setattr(monsters, "detect_dragons_yolo", lambda *a, **k: next(seq))
    grabbed = {"n": 0}
    def fake_capture():
        grabbed["n"] += 1
        return object()
    n = monsters.count_dragons_yolo(fake_capture, model=object(), roi=(0,0,10,10),
                                    samples=3, interval=0)
    assert n == 3 and grabbed["n"] == 3

def test_count_dragons_yolo_zero_when_no_frames():
    n = monsters.count_dragons_yolo(lambda: None, model=object(), roi=(0,0,10,10),
                                    samples=3, interval=0)
    assert n == 0

def test_load_dragon_model_missing_file_returns_none():
    monsters._MODEL_CACHE.clear()
    assert monsters.load_dragon_model(path="does/not/exist.pt") is None

def test_count_dragons_best_falls_back_to_motion_when_no_model(monkeypatch):
    monkeypatch.setattr(monsters, "load_dragon_model", lambda *a, **k: None)
    monkeypatch.setattr(monsters, "count_dragons_motion", lambda cap, roi=None, **k: 42)
    assert monsters.count_dragons_best(lambda: None, "TOP_FARM") == 42

def test_count_dragons_best_uses_yolo_when_model_present(monkeypatch):
    monkeypatch.setattr(monsters, "load_dragon_model", lambda *a, **k: object())
    monkeypatch.setattr(monsters, "count_dragons_yolo", lambda cap, model, roi, **k: 7)
    assert monsters.count_dragons_best(lambda: None, "BOTTOM_FARM") == 7
