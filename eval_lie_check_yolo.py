"""Measure the lie-check YOLO detector's recall and the templates' coverage on real frames,
so retiring the fragile monster templates can be an evidence-based decision. Measurement only
-- imports and calls the existing detectors, changes no runtime path, writes one report.

Usage:  python eval_lie_check_yolo.py [--conf 0.80] [--out logs/lie_check_gate.md]
"""
import argparse
import glob
import os

import cv2

CLASS_NAMES = ("monster_check", "curse", "transparent_shape")
CAPTURES_DIR = os.path.join("datasets", "lie_check", "captures")
NEGATIVES_DIR = os.path.join("datasets", "mobs_real", "images")
VAL_DATA_YAML = os.path.join("datasets", "lie_check_yolo", "lie_check.yaml")


def expected_class(filename):
    """Best-effort expected class from a capture filename's tag. captures are dumped named
    after what fired (e.g. '..._fast_auto_transparent_title0.90.png'), which is a weak but
    useful ground-truth signal for a real-frame recall proxy."""
    n = os.path.basename(filename).lower()
    if "transparent" in n:
        return "transparent_shape"
    if "curse" in n:
        return "curse"
    if "monster" in n:
        return "monster_check"
    return "unknown"


def scan_frames(paths, yolo_fn, template_fn):
    """One record per readable path: expected class + the sets of YOLO classes and template
    names that fired. Detectors are injected (real ones in main; fakes in tests). Unreadable
    frames are skipped."""
    records = []
    for p in paths:
        frame = cv2.imread(p)
        if frame is None:
            continue
        yolo = yolo_fn(frame)
        tmpl = template_fn(frame)
        records.append({
            "path": p,
            "expected": expected_class(p),
            "yolo_classes": {c for c, _conf, _box in yolo},
            "template_names": {name for name, _score in tmpl},
        })
    return records


def summarize_positives(records):
    """Per expected-class bucket: n frames, yolo_any (>=1 YOLO box), yolo_expected (YOLO fired
    the expected class), template_any (>=1 template hit). 'unknown' frames are excluded."""
    out = {}
    for r in records:
        exp = r["expected"]
        if exp == "unknown":
            continue
        b = out.setdefault(exp, {"n": 0, "yolo_any": 0, "yolo_expected": 0, "template_any": 0})
        b["n"] += 1
        b["yolo_any"] += 1 if r["yolo_classes"] else 0
        b["yolo_expected"] += 1 if exp in r["yolo_classes"] else 0
        b["template_any"] += 1 if r["template_names"] else 0
    return out


def summarize_negatives(records):
    """False fires on guaranteed-negative frames: any detection is a false positive."""
    yolo_false = [r["path"] for r in records if r["yolo_classes"]]
    tmpl_false = [r["path"] for r in records if r["template_names"]]
    return {"n": len(records),
            "yolo_false": len(yolo_false), "yolo_false_paths": yolo_false,
            "template_false": len(tmpl_false), "template_false_paths": tmpl_false}


def render_report(val_metrics, pos_summary, neg_summary, conf):
    """Markdown gate report."""
    lines = ["# Lie-Check YOLO Recall Gate", "",
             f"YOLO conf = {conf}", "",
             "## Held-out val recall (real-only split; tiny support -- do not over-read)", ""]
    for cls in CLASS_NAMES:
        m = val_metrics.get(cls, {"recall": None, "n": 0})
        rec = "n/a" if m["recall"] is None else f"{m['recall']:.3f}"
        lines.append(f"- {cls}: recall {rec} (n={m['n']})")
    lines += ["", "## Real-frame coverage from captures/ (filename-bucketed)", ""]
    for cls in CLASS_NAMES:
        b = pos_summary.get(cls)
        if not b:
            lines.append(f"- {cls}: no real captures")
            continue
        lines.append(f"- {cls}: n={b['n']}  YOLO(any)={b['yolo_any']}  "
                     f"YOLO(expected)={b['yolo_expected']}  template(any)={b['template_any']}")
    lines += ["", "## False fires on negatives (datasets/mobs_real/images)", "",
              f"- frames: {neg_summary['n']}",
              f"- YOLO false fires: {neg_summary['yolo_false']}",
              f"- template false fires: {neg_summary['template_false']}"]
    if neg_summary["yolo_false_paths"]:
        lines.append("  - YOLO: " + ", ".join(os.path.basename(p) for p in neg_summary["yolo_false_paths"][:20]))
    if neg_summary["template_false_paths"]:
        lines.append("  - template: " + ", ".join(os.path.basename(p) for p in neg_summary["template_false_paths"][:20]))
    return "\n".join(lines) + "\n"


def _val_metrics(conf):
    """Per-class recall on the held-out val split via ultralytics. Returns
    {cls: {'recall': float|None, 'n': int}}; recall None / n 0 where a class has no val boxes."""
    from ultralytics import YOLO
    model = YOLO(os.path.join("models", "lie_check_yolo.pt"))
    res = model.val(data=VAL_DATA_YAML, imgsz=1280, conf=conf, verbose=False)
    out = {}
    # ultralytics: res.box.r is per-class recall aligned to res.ap_class_index (classes with val boxes)
    idx_to_recall = {}
    try:
        for i, cls_idx in enumerate(res.ap_class_index):
            idx_to_recall[int(cls_idx)] = float(res.box.r[i])
    except Exception as e:
        print(f"[gate] could not read per-class recall: {e}")
    # per-class val support count from the val label files
    support = {c: 0 for c in range(len(CLASS_NAMES))}
    try:
        with open(VAL_DATA_YAML.replace("lie_check.yaml", "val.txt"), encoding="utf-8") as f:
            for img in [ln.strip() for ln in f if ln.strip()]:
                lbl = os.path.join("datasets", "lie_check_yolo", "labels",
                                   os.path.splitext(os.path.basename(img))[0] + ".txt")
                if os.path.exists(lbl):
                    with open(lbl, encoding="utf-8") as lf:
                        for line in lf:
                            if line.strip():
                                support[int(line.split()[0])] += 1
    except Exception as e:
        print(f"[gate] could not count val support: {e}")
    return {CLASS_NAMES[c]: {"recall": idx_to_recall.get(c), "n": support.get(c, 0)}
            for c in range(len(CLASS_NAMES))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", type=float, default=0.80)
    ap.add_argument("--out", default=os.path.join("logs", "lie_check_gate.md"))
    args = ap.parse_args()

    import detection
    import lie_check_yolo

    def yolo_fn(frame):
        return lie_check_yolo.detect_lie_check_yolo(frame, conf=args.conf, imgsz=1280)

    def template_fn(frame):
        # the FULL production template set (curse + monster), same as recovery._FULL_TEMPLATES
        return detection.detect_lie_check(
            frame, templates_folder="assets/lie_check/",
            template_filter=["curse_banner.png", "curse_lock.png", "monster_instr.png",
                             "monster_instr_box.png", "monster_warn_box.png",
                             "monster_instr_temple.png", "monster_warn_temple.png"],
            work_width=1000)

    pos = scan_frames(sorted(glob.glob(os.path.join(CAPTURES_DIR, "*.png"))), yolo_fn, template_fn)
    neg = scan_frames(sorted(glob.glob(os.path.join(NEGATIVES_DIR, "*.png"))), yolo_fn, template_fn)
    val = _val_metrics(args.conf)

    report = render_report(val, summarize_positives(pos), summarize_negatives(neg), args.conf)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"[gate] wrote {args.out}")


if __name__ == "__main__":
    main()
