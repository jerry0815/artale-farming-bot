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
    "datasets/lie_check/monster_intr_test.png",             # name-the-monster (old overlay art)
    "datasets/lie_check/monster_box_test.png",              # name-the-monster (new opaque-box art, live windowed)
    "datasets/lie_check/monster_temple_test.png",           # name-the-monster (same art, dark temple map)
]


def _load(path):
    im = cv2.imread(path)
    assert im is not None, path
    return im


def test_each_production_capture_triggers():
    for src in PRODUCTION_POSITIVES:
        hits = detection.detect_lie_check(_load(src))
        assert hits, f"expected a lie-check hit on {src}, got none"


def test_new_monster_popup_has_two_independent_signals():
    # Recall guard: the new opaque-box monster popup must be caught by EACH of its two
    # OR'd templates on its own (instruction text + red warning line), so a single
    # degraded template can't silently cause a miss (the other would mask it in the OR).
    cases = {
        "datasets/lie_check/monster_box_test.png": ("monster_instr_box.png", "monster_warn_box.png"),
        "datasets/lie_check/monster_temple_test.png": ("monster_instr_temple.png", "monster_warn_temple.png"),
    }
    for src, tpls in cases.items():
        frame = _load(src)
        for tpl in tpls:
            hits = detection.detect_lie_check(frame, template_filter=[tpl], work_width=1000)
            assert hits and hits[0][0] == tpl, f"{tpl} alone failed to fire on {src}: {hits}"


def test_scores_out_param_reports_best_per_template():
    # The near-miss telemetry relies on detect_lie_check filling `scores` with the best score
    # per template, even when nothing clears threshold.
    frame = _load("datasets/lie_check/monster_box_test.png")
    scores = {}
    detection.detect_lie_check(frame, template_filter=["monster_instr_box.png", "curse_lock.png"],
                               work_width=1000, scores=scores)
    assert set(scores) == {"monster_instr_box.png", "curse_lock.png"}
    assert scores["monster_instr_box.png"] >= 0.78     # real popup, hits
    assert scores["curse_lock.png"] < 0.78             # sub-threshold best is reported too


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
