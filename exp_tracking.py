"""EXP reading, per-window tracking, and session persistence.

Extracted verbatim from core.py (behavior unchanged). These call back into the engine
as `core.<name>` so tests that `monkeypatch.setattr(core, ...)` still bind the callee
(get_exp_number, ExpProcessor, capture_pil_np, dbg/dbg_on). core.py re-exports the public
names, so external callers/tests keep using `core.make_exp_tracker`, `core.get_exp`, etc.
"""
import os

import core


def _dpi_offsets(ow, oh, rw=1920, rh=1080, wmax=40, hmax=80):
    """Window-border offsets (wo, ho) to subtract from a capture before scaling the
    1920x1080-referenced EXP crop. A DPI-scaled game window grabs a few px larger than
    1920x1080 (window chrome): ~22x56 at 150% DPI, jittering a pixel or two between launches
    (1942x1136, 1940x1135, ...). The border is exactly the overage above the reference, so
    compute it directly and clamp -- instead of the old `ow % 10 == 0` proxy, which stripped
    NO border when the width happened to be divisible by 10 (e.g. 1940), sliding the crop onto
    the 'EXP' label so number+percent OCR fused into one unparseable digit run and EXP tracking
    silently died. A genuinely different resolution (overage beyond the clamp, or smaller than
    the reference) is scaled proportionally with no border stripped."""
    wo = ow - rw if 0 < ow - rw <= wmax else 0
    ho = oh - rh if 0 < oh - rh <= hmax else 0
    return wo, ho


# EXP text box on the HUD, in 1920x1080 reference px: "EXP 103349960[96.65%]". The right edge must
# clear the WHOLE percent: the parser needs it to split number from bracket. At 1190 a 9-digit EXP
# (>= 100,000,000) pushed the percent out of the box and every read failed ('103122300[' -> None),
# so "last 10 min" sat at 0 (2026-10-20). Empty HUD continues to ~1370 (自由市場 button).
EXP_OCR_REG = (1028, 996, 1290, 1025)


def get_exp_number(exp_processor):
    """Absolute EXP number via OCR (or None). Unlike get_exp it does NOT run the delta /
    'reasonable gain' logic, so it never prints the anomaly warning -- the per-10-min
    tracker diffs these numbers itself with its own filtering."""
    import ocr_processor
    pil_img, _ = core.capture_pil_np()
    if pil_img is None:
        return None
    ow, oh = pil_img.width, pil_img.height
    wo, ho = _dpi_offsets(ow, oh)
    cw, ch = ow - wo, oh - ho
    rw, rh = 1920, 1080
    reg = EXP_OCR_REG
    try:
        left = int(cw * reg[0] / rw) + wo
        top = max(0, int(ch * reg[1] / rh) + ho)
        right = min(ow, int(cw * reg[2] / rw) + wo)
        bottom = min(oh, int(ch * reg[3] / rh) + ho)
        adj = int(max(-30, min(30, (cw - rw) * 0.01)))
        left = max(0, left + adj)
        raw = ocr_processor.run_ocr(ocr_processor.preprocess_exp_image(
            pil_img.crop((left, top, right, bottom))), 'exp')
        num_str, _pct = exp_processor.parse_exp_data(raw)
        val = int(num_str) if num_str else None
        if core.dbg_on():                                 # detection trace for exp-anomaly debugging
            core.dbg(f"[exp] ocr raw={raw!r} parsed={num_str!r} -> {val}")
        return val
    except Exception:
        return None


def get_exp(exp_processor):
    """Recent EXP gain as [gain, num_str, pct_str] (or None). Faithful port of the
    notebook get_exp: the width/height offsets normalize the 150%-DPI physical grab
    (1942x1136 -> 1920x1080) so the crop region lines up."""
    pil_img, _ = core.capture_pil_np()
    if pil_img is None:
        return None
    ow, oh = pil_img.width, pil_img.height
    wo, ho = _dpi_offsets(ow, oh)
    cw, ch = ow - wo, oh - ho
    rw, rh = 1920, 1080
    reg = EXP_OCR_REG
    try:
        left = int(cw * reg[0] / rw) + wo
        top = max(0, int(ch * reg[1] / rh) + ho)
        right = min(ow, int(cw * reg[2] / rw) + wo)
        bottom = min(oh, int(ch * reg[3] / rh) + ho)
        adj = int(max(-30, min(30, (cw - rw) * 0.01)))
        left = max(0, left + adj)
        gain, _img = exp_processor.process_exp_value(pil_img.crop((left, top, right, bottom)), n=2)
        return gain
    except Exception as e:
        print("[exp] read error:", e)
        return None


def make_exp_tracker(log_exp=True, sample_secs=10, window_secs=600, label="exp", min_run_secs=60,
                     max_exp_per_sec=20000):
    """A per-window EXP logger, shared by the water and nav farming loops. Returns an
    `exp_tick()` to call each loop iteration: it samples the ABSOLUTE EXP number every
    `sample_secs` (the FIRST sample is taken immediately so a gain is available within a
    couple of samples), and every `window_secs` (10 min default) logs the gain over that
    window (sum of positive consecutive deltas, skipping level-ups and digit-count OCR
    errors) plus the running average across windows. Publishes STATUS['exp_10min'] (last
    window gain), STATUS['exp_per_min'] (LIVE run-average EXP/min, published once the run has
    at least `min_run_secs` elapsed so the estimate is meaningful) and STATUS['exp_total']
    (cumulative gain). No-op if log_exp is False. Self-contained (closes over its buffer)."""
    exp_proc = core.ExpProcessor() if log_exp else None
    core.STATUS["exp_per_min"], core.STATUS["exp_10min"], core.STATUS["exp_total"] = None, None, 0  # fresh run
    run_start = core.time.time()
    next_sample = [core.time.time()]                            # sample immediately at run start
    next_log = [core.time.time() + window_secs]
    samples = []                                                # [(t, abs_exp_number)]
    windows = [0, 0.0]                                          # [count, cumulative gain]
    cumulative = [0]                                            # gain since run start (incremental)
    prev = [None]                                              # last (t, num) for the running delta

    def _accept(pn, num, dt):
        """Is (num - pn) a plausible EXP delta? Rejects negatives / level-ups (digit-count
        change) AND implausibly large jumps -- a same-digit-count OCR misread (one wrong digit)
        makes a positive, right-digit-count delta the old filter counted, silently inflating the
        total. The cap = max_exp_per_sec * dt bounds a single sample's gain. Returns (ok, d, reason)."""
        d = num - pn
        if d <= 0:
            return False, d, "nonpositive"
        if len(str(pn)) != len(str(num)):
            return False, d, "digit-change"                     # level-up or gross OCR error
        cap = max_exp_per_sec * max(dt, 1.0)
        if d > cap:
            return False, d, f"over-cap(>{int(cap)})"
        return True, d, "ok"

    def _window_gain(since):
        win = [(t, n) for t, n in samples if t >= since]
        total = 0
        for (t0, n0), (t1, n1) in zip(win, win[1:]):
            ok, d, _reason = _accept(n0, n1, t1 - t0)
            if ok:
                total += d
        return total

    def exp_tick():
        if exp_proc is None:
            return
        now = core.time.time()
        if now >= next_sample[0]:
            next_sample[0] = now + sample_secs
            num = core.get_exp_number(exp_proc)
            if num:
                if prev[0] is not None:                         # accumulate the run total incrementally
                    pt, pn = prev[0]
                    ok, d, reason = _accept(pn, num, now - pt)
                    if ok:
                        cumulative[0] += d
                    if core.dbg_on():                           # exp-anomaly trace (see [exp] ocr lines)
                        core.dbg(f"[exp] sample num={num} prev={pn} dt={now - pt:.0f}s delta={d} "
                                 f"-> {'count' if ok else 'REJECT ' + reason} (total={cumulative[0]})")
                elif core.dbg_on():
                    core.dbg(f"[exp] sample num={num} (first)")
                prev[0] = (now, num)
                samples.append((now, num))
                if len(samples) > 400:
                    del samples[:200]
                core.STATUS["exp_total"] = cumulative[0]
                elapsed = now - run_start
                if elapsed >= min_run_secs:                     # live run-average EXP per minute
                    core.STATUS["exp_per_min"] = round(cumulative[0] / (elapsed / 60.0))
        if now >= next_log[0]:
            next_log[0] = now + window_secs
            gain = _window_gain(now - window_secs)
            windows[0] += 1
            windows[1] += gain
            avg = windows[1] / windows[0]
            core.STATUS["exp_10min"] = round(gain)
            mins = int(round(window_secs / 60))
            print(f"[{label}] last {mins} min: {gain:,.0f} EXP  |  avg/{mins}min: {avg:,.0f} "
                  f"(over {windows[0]} window{'s' if windows[0] != 1 else ''})")

    return exp_tick


# --- EXP session log: persist human/bot farm sessions (start/duration/gain/rate/label) to a
# JSON-Lines file so a human-farm reference can be compared against bot runs. ---
_EXP_SESSIONS_PATH = "logs/exp_sessions.jsonl"


def append_exp_session(row, path=_EXP_SESSIONS_PATH):
    """Append one session row as a JSON line. Best-effort -- never raises (it must not crash
    the recorder's shutdown path)."""
    import json
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[exp] could not append session: {e}")


def read_exp_sessions(path=_EXP_SESSIONS_PATH, limit=50):
    """The last `limit` session rows, NEWEST FIRST, for the panel's history table. Skips
    malformed lines; returns [] if the file is absent."""
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []
    rows = []
    for ln in lines[-limit:]:
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    rows.reverse()                                     # newest first
    return rows
