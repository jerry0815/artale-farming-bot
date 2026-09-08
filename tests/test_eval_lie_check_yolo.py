"""Aggregation logic for the lie-check YOLO recall gate (detectors injected)."""
import numpy as np
import cv2
import eval_lie_check_yolo as ev


def test_expected_class_from_filename():
    assert ev.expected_class("x_fast_auto_transparent_title0.90.png") == "transparent_shape"
    assert ev.expected_class("y_full_auto_curse_lock0.94.png") == "curse"
    assert ev.expected_class("z_near_miss_monster_warn_box0.74.png") == "monster_check"
    assert ev.expected_class("random_frame.png") == "unknown"


def _img(tmp_path, name):
    p = tmp_path / name
    cv2.imwrite(str(p), np.zeros((20, 20, 3), dtype=np.uint8))
    return str(p)


def test_scan_frames_records_both_detectors(tmp_path):
    a = _img(tmp_path, "a_transparent_title.png")
    b = _img(tmp_path, "b_curse_lock.png")
    yolo_seq = iter([[("transparent_shape", 0.9, (0, 0, 1, 1))], []])   # a fires, b doesn't
    tmpl_seq = iter([[], [("curse_lock.png", 0.94)]])                   # a doesn't, b fires
    recs = ev.scan_frames([a, b],
                          yolo_fn=lambda f: next(yolo_seq),
                          template_fn=lambda f: next(tmpl_seq))
    by = {r["path"]: r for r in recs}
    assert by[a]["expected"] == "transparent_shape"
    assert by[a]["yolo_classes"] == {"transparent_shape"}
    assert by[a]["template_names"] == set()
    assert by[b]["expected"] == "curse"
    assert by[b]["template_names"] == {"curse_lock.png"}


def test_scan_frames_skips_unreadable(tmp_path):
    good = _img(tmp_path, "good_transparent_title.png")
    missing = str(tmp_path / "does_not_exist.png")
    yolo_seq = iter([[]])            # called once, for the readable frame only
    tmpl_seq = iter([[]])
    recs = ev.scan_frames([missing, good],
                          yolo_fn=lambda f: next(yolo_seq),
                          template_fn=lambda f: next(tmpl_seq))
    assert [r["path"] for r in recs] == [good]


def test_summarize_positives_counts_recall_buckets():
    recs = [
        {"expected": "transparent_shape", "yolo_classes": {"transparent_shape"}, "template_names": {"transparent_title.png"}},
        {"expected": "transparent_shape", "yolo_classes": set(), "template_names": {"transparent_title.png"}},
        {"expected": "monster_check", "yolo_classes": {"curse"}, "template_names": set()},
    ]
    s = ev.summarize_positives(recs)
    assert s["transparent_shape"] == {"n": 2, "yolo_any": 1, "yolo_expected": 1, "template_any": 2}
    # a YOLO box of the WRONG class counts for yolo_any but not yolo_expected:
    assert s["monster_check"] == {"n": 1, "yolo_any": 1, "yolo_expected": 0, "template_any": 0}


def test_summarize_negatives_flags_any_detection():
    recs = [
        {"path": "n1.png", "yolo_classes": set(), "template_names": set()},
        {"path": "n2.png", "yolo_classes": {"curse"}, "template_names": set()},
        {"path": "n3.png", "yolo_classes": set(), "template_names": {"monster_instr_box.png"}},
    ]
    s = ev.summarize_negatives(recs)
    assert s["n"] == 3
    assert s["yolo_false"] == 1 and s["yolo_false_paths"] == ["n2.png"]
    assert s["template_false"] == 1 and s["template_false_paths"] == ["n3.png"]


def test_render_report_is_markdown_with_sections():
    out = ev.render_report(
        val_metrics={"monster_check": {"recall": 1.0, "n": 2},
                     "curse": {"recall": None, "n": 0},
                     "transparent_shape": {"recall": 0.92, "n": 13}},
        pos_summary={"transparent_shape": {"n": 2, "yolo_any": 1, "yolo_expected": 1, "template_any": 2}},
        neg_summary={"n": 3, "yolo_false": 1, "template_false": 1,
                     "yolo_false_paths": ["n2.png"], "template_false_paths": ["n3.png"]},
        conf=0.80)
    assert "# Lie-Check YOLO Recall Gate" in out
    assert "Held-out val recall" in out
    assert "curse" in out and "n=0" in out          # zero-support caveat surfaced
    assert "False fires on negatives" in out
