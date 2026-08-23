"""Tests for detect_lie_check.

Positives are the real full-screen production captures each template was cut from
(what capture() actually feeds the detector in-game). Negatives are ordinary game
frames. The earlier zoomed/cropped screenshots in datasets/lie_check are NOT valid
positives for the production-scale templates and are intentionally not asserted.
"""
import cv2
import numpy as np
import detection

# Real production captures (full-screen), one per check screen.
PRODUCTION_POSITIVES = [
    "datasets/lie_check/Screenshot 2026-08-20 013627.png",  # find-transparent-shape
    "datasets/lie_check/rune_check_test.png",               # curse (fullscreen ~2683w)
    "datasets/lie_check/rune_check_live_test.png",          # curse (live windowed ~1914w)
    "datasets/lie_check/monster_intr_test.png",             # name-the-monster
]


def _load(path):
    im = cv2.imread(path)
    assert im is not None, path
    return im


def test_each_production_capture_triggers():
    for src in PRODUCTION_POSITIVES:
        hits = detection.detect_lie_check(_load(src))
        assert hits, f"expected a lie-check hit on {src}, got none"


def test_blank_screen_does_not_trigger():
    canvas = np.full((1626, 2691, 3), 30, np.uint8)  # dark full-screen, no popup
    assert detection.detect_lie_check(canvas) == []


def test_normal_game_frames_do_not_trigger():
    from glob import glob
    negs = (glob("datasets/lie_check/normal_test.png")   # real full-screen, no popup
            + glob("debug_output/*.png") + glob("debug_mm*.png"))
    checked = 0
    for p in negs:
        im = cv2.imread(p)
        if im is None:
            continue
        checked += 1
        assert detection.detect_lie_check(im) == [], f"false positive on {p}"
    assert checked > 0, "no negative frames available to test"


def test_missing_folder_returns_empty():
    canvas = np.zeros((200, 200, 3), np.uint8)
    assert detection.detect_lie_check(canvas, templates_folder="does/not/exist/") == []


def test_fast_filter_matches_transparent_only():
    # The fast in-state path checks only the transparent template (cheap).
    transp = _load("datasets/lie_check/Screenshot 2026-08-20 013627.png")
    hits = detection.detect_lie_check(transp, template_filter=["transparent_title.png"],
                                      work_width=520)
    assert len(hits) == 1 and hits[0][0] == "transparent_title.png", hits


def test_fast_filter_ignores_other_screens():
    # A curse screen must NOT fire the transparent-only fast filter.
    curse = _load("datasets/lie_check/rune_check_test.png")
    hits = detection.detect_lie_check(curse, template_filter=["transparent_title.png"],
                                      work_width=520)
    assert hits == [], hits
