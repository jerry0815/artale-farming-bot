# Lie-Check YOLO Recall Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build (and run) a measurement script that quantifies the lie-check YOLO detector's recall and the templates' coverage on real frames, so the decision to retire the fragile monster templates is evidence-based rather than assumed.

**Architecture:** A standalone `eval_lie_check_yolo.py` that reuses the *existing* detectors (`detection.detect_lie_check` for templates, `lie_check_yolo.detect_lie_check_yolo` for YOLO) — it never re-implements detection. Pure aggregation functions (detector callables injected) are unit-tested; a thin `main()` wires the real detectors, runs ultralytics `model.val` for held-out per-class recall, scans the real capture frames bucketed by filename tag, and scans the mob frames as guaranteed negatives, then writes one Markdown report.

**Tech Stack:** Python, OpenCV, ultralytics YOLO, pytest. No new dependencies.

## Context: why this plan is measurement-only

The migration spec's Conversion 1 wants the fragile monster templates retired in favor of YOLO — but **gated on measured recall**. Current data can't obviously clear that gate: of 111 `monster_check` labels only ~2–3 are real (the rest synthetic), the held-out val split has just **2 monster / 0 curse / 13 transparent** images, and `datasets/lie_check/captures/` holds **~61 real transparent, many real curse, but essentially no real monster** frames. So the near-term deliverable is the *gate itself* plus its numbers; the retirement is a human go/no-go on the output (most likely: **keep monster templates, keep gathering real monster frames**). **This plan changes no runtime detection path.**

## Global Constraints

- **Measurement only.** Do NOT modify `detection.py`, `recovery.py`, `lie_check_yolo.py`, or any runtime path. The script is read-only w.r.t. production and writes only a report file.
- **Reuse, never duplicate detection.** Call `detection.detect_lie_check(...)` and `lie_check_yolo.detect_lie_check_yolo(...)`; do not re-implement matching or inference.
- **Negatives set:** `datasets/mobs_real/images/*.png` (149 real farming frames — guaranteed non-lie-check). A YOLO or template detection on any of these is a false fire.
- **Real-positive set:** `datasets/lie_check/captures/*.png`, bucketed by filename tag → expected class: substring `transparent`→`transparent_shape`, `curse`→`curse`, `monster`→`monster_check`; anything else → `unknown` (reported, not scored).
- **Held-out val recall:** ultralytics `model.val(data=datasets/lie_check_yolo/lie_check.yaml)` — report per-class recall, explicitly noting the tiny/zero support (monster=2, curse=0, transparent=13) so no one over-reads it.
- **Class names** (fixed): `0: monster_check, 1: curse, 2: transparent_shape`.
- **YOLO call:** `detect_lie_check_yolo(frame, conf=0.80, imgsz=1280)` — 0.80 is the production conf; the report also sweeps a few conf values so the gate can see the recall/false-fire tradeoff.
- **Windows repo:** run pytest as `python -m pytest ...`; Bash tool is Git Bash (POSIX).
- No new dependencies (OpenCV + ultralytics already used).

---

### Task 1: `eval_lie_check_yolo.py` — aggregation core + report (TDD)

**Files:**
- Create: `eval_lie_check_yolo.py`
- Test: `tests/test_eval_lie_check_yolo.py`

**Interfaces:**
- Consumes: `detection.detect_lie_check`, `lie_check_yolo.detect_lie_check_yolo` (in `main()` only — the tested functions take injected callables).
- Produces:
  - `expected_class(filename: str) -> str` — one of `"transparent_shape" | "curse" | "monster_check" | "unknown"` from the filename substring rule.
  - `scan_frames(paths, yolo_fn, template_fn) -> list[dict]` — one record per path: `{"path", "expected", "yolo_classes": set[str], "template_names": set[str]}`. `yolo_fn(frame)->list[(cls,conf,box)]`, `template_fn(frame)->list[(name,score)]`; frames are loaded with `cv2.imread` inside, skipping unreadable paths.
  - `summarize_positives(records) -> dict` — per expected-class bucket: `n`, `yolo_any` (frames with ≥1 YOLO box), `yolo_expected` (frames whose YOLO fired the expected class), `template_any`.
  - `summarize_negatives(records) -> dict` — `{"n", "yolo_false", "template_false", "yolo_false_paths", "template_false_paths"}` (a "false" = ≥1 detection on a guaranteed-negative frame).
  - `render_report(val_metrics, pos_summary, neg_summary, conf) -> str` — Markdown.

- [ ] **Step 1: Write the failing tests**

`scan_frames` loads each path with `cv2.imread` and passes the resulting frame to the injected detectors. The test frames are identical zero-images, so the fakes must NOT key off frame content — they consume a per-detector sequence in call order (paths are scanned in the order passed).

```python
# tests/test_eval_lie_check_yolo.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_eval_lie_check_yolo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval_lie_check_yolo'` (then, once the file exists but functions don't, `AttributeError`).

- [ ] **Step 3: Implement the aggregation core**

```python
# eval_lie_check_yolo.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_eval_lie_check_yolo.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Add the `main()` that wires the real detectors (not unit-tested — exercised in Task 2)**

Append to `eval_lie_check_yolo.py`:

```python
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
```

- [ ] **Step 6: Commit**

```bash
git add eval_lie_check_yolo.py tests/test_eval_lie_check_yolo.py
git commit -m "feat(lie-yolo): recall-gate eval script (val recall + captures coverage + negatives)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Run the gate on the local model and record the numbers

**Files:** none committed to source (produces a report artifact).

This task runs the script against the real local `models/lie_check_yolo.pt` and captures the output for the human go/no-go on template retirement. It is a run-and-record task, not TDD.

- [ ] **Step 1: Confirm prerequisites**

Run: `python -c "import ultralytics, os; print('model', os.path.exists('models/lie_check_yolo.pt'))"`
Expected: prints `model True`. If ultralytics is missing or the model is absent, STOP and report NEEDS_CONTEXT (the environment can't run the gate).

- [ ] **Step 2: Run the gate at the production conf**

Run: `python eval_lie_check_yolo.py --conf 0.80 --out logs/lie_check_gate_0.80.md`
Expected: prints the Markdown report and `[gate] wrote logs/lie_check_gate_0.80.md`. It scans ~83 captures + 149 negatives + the val split; allow a minute or two on GPU.

- [ ] **Step 3: Run a small conf sweep for the tradeoff curve**

Run these and keep each report:
```bash
python eval_lie_check_yolo.py --conf 0.60 --out logs/lie_check_gate_0.60.md
python eval_lie_check_yolo.py --conf 0.90 --out logs/lie_check_gate_0.90.md
```

- [ ] **Step 4: Copy the reports into the SDD workspace and write a one-paragraph reading**

Copy the three `logs/lie_check_gate_*.md` into the plan's SDD workspace as the durable gate record, and write a short plain-language summary answering: (a) does YOLO fire the *monster_check* class on the ~2-3 real monster frames? (b) how many false fires does YOLO vs the templates produce on the 149 negatives? (c) does the transparent/curse real-frame coverage from captures confirm YOLO ≥ templates? State the go/no-go recommendation on retiring the 4 monster templates — expected, given the data, to be **no-go / defer** unless monster recall is clean AND YOLO's negative false-fire count is ≤ the templates'.

- [ ] **Step 5: No commit of artifacts**

`logs/` reports are run outputs, not source. Do not commit them (leave them for the human). The SDD workspace copies are the record.

---

## Out of scope (explicit)

- **Retiring the monster templates** — the code change (remove `monster_instr_box`/`monster_warn_box`/`monster_instr_temple`/`monster_warn_temple` from `recovery._FULL_TEMPLATES` and their `detection.LIE_CHECK_THRESHOLDS`/`LIE_CHECK_FRACTIONS` entries) is a *separate* change, made only if this gate's evidence supports it. Not in this plan.
- **Any change to `recovery.py` / `detection.py` / `lie_check_yolo.py` runtime behavior.**
- **Retraining or new data collection** (that's the alternative "data-gathering" track).
