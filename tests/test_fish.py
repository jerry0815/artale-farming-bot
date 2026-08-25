"""Tests for the fish template-match counter (synthetic images; no game)."""
import numpy as np
import cv2
import fish


def test_mask_from_green_keys_out_background():
    im = np.zeros((4, 4, 4), np.uint8)
    im[:, :] = (0, 255, 0, 255)              # all green
    im[1:3, 1:3] = (200, 50, 50, 255)        # a blue-ish sprite patch
    m = fish.mask_from_green(im)
    assert m[0, 0] == 0                       # green -> masked out
    assert m[1, 1] == 255                     # sprite -> kept
    assert m.dtype == np.uint8


def test_mask_honors_alpha_zero():
    im = np.zeros((2, 2, 4), np.uint8)
    im[:, :] = (200, 50, 50, 0)              # non-green but fully transparent
    assert (fish.mask_from_green(im) == 0).all()


def test_iou_and_nms():
    assert fish.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert fish.iou((0, 0, 10, 10), (100, 100, 10, 10)) == 0.0
    dets = [(0.9, 0, 0, 10, 10), (0.8, 2, 2, 10, 10), (0.95, 100, 100, 10, 10)]
    kept = fish.nms(dets, iou_thr=0.4)
    assert len(kept) == 2                     # the two overlapping merge to one
    scores = sorted(k[0] for k in kept)
    assert scores == [0.9, 0.95]


def test_count_fish_finds_pasted_templates():
    # A distinctive template (red block) with a green border for the mask.
    tmpl = np.full((16, 16, 3), (0, 255, 0), np.uint8)   # green bg
    tmpl[2:14, 2:14] = (40, 40, 220)                      # red core (BGR)
    mask = fish.mask_from_green(tmpl)
    templates = [("red", tmpl, mask)]
    # Frame: neutral gray, paste the red core (no green) at two spots.
    frame = np.full((120, 200, 3), 127, np.uint8)
    for (px, py) in [(20, 20), (120, 60)]:
        frame[py + 2:py + 14, px + 2:px + 14] = (40, 40, 220)
    n = fish.count_fish(frame, templates, threshold=0.95, iou_thr=0.3)
    assert n == 2


def test_count_fish_zero_on_empty_frame():
    tmpl = np.full((16, 16, 3), (0, 255, 0), np.uint8)
    tmpl[2:14, 2:14] = (40, 40, 220)
    templates = [("red", tmpl, fish.mask_from_green(tmpl))]
    frame = np.full((120, 200, 3), 127, np.uint8)         # nothing to match
    assert fish.count_fish(frame, templates, threshold=0.99) == 0


def test_count_fish_respects_roi():
    tmpl = np.full((16, 16, 3), (0, 255, 0), np.uint8)
    tmpl[2:14, 2:14] = (40, 40, 220)
    templates = [("red", tmpl, fish.mask_from_green(tmpl))]
    frame = np.full((120, 200, 3), 127, np.uint8)
    frame[22:34, 22:34] = (40, 40, 220)                   # one fish at (20,20)
    # ROI excludes that region -> zero
    assert fish.count_fish(frame, templates, roi=(80, 0, 200, 120), threshold=0.95) == 0
    # ROI includes it -> one
    assert fish.count_fish(frame, templates, roi=(0, 0, 80, 120), threshold=0.95) == 1


def test_load_live_templates_and_ccoeff_match(tmp_path):
    # A live crop = tight BGR image, no green screen, mask None -> CCOEFF matching.
    import os
    d = tmp_path / "mob" / "deep_sea_2"
    d.mkdir(parents=True)
    tmpl = np.full((20, 24, 3), 60, np.uint8)
    tmpl[4:16, 4:20] = (40, 40, 220)                 # distinctive red core
    cv2.imwrite(str(d / "fishhouse_1.png"), tmpl)
    cv2.imwrite(str(d / "fishhouse_2.png"), tmpl)
    tmpls = fish.load_live_templates(str(d))
    assert len(tmpls) == 2
    assert all(m is None for _n, _t, m in tmpls)     # no mask -> CCOEFF path
    assert tmpls[0][0] == "fishhouse"                # trailing _N stripped

    frame = np.full((120, 200, 3), 60, np.uint8)
    for (px, py) in [(30, 30), (120, 70)]:
        frame[py + 4:py + 16, px + 4:px + 20] = (40, 40, 220)
    assert fish.count_fish(frame, tmpls, threshold=0.7) == 2


def test_templates_for_prefers_live(tmp_path):
    d = tmp_path / "mob"
    d.mkdir()
    cv2.imwrite(str(d / "goby_1.png"), np.full((10, 10, 3), 50, np.uint8))
    tl, mode = fish.templates_for({"mob_template_dir": str(d)})
    assert mode == "live" and len(tl) == 1
    tl2, mode2 = fish.templates_for({"fish_species": ["goby"], "fish_per_species": 1})
    assert mode2 == "sprite"
    assert fish.default_threshold("live") < fish.default_threshold("sprite")
