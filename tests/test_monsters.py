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
