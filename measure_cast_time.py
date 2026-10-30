"""Measure the AoE cast time (one cast cycle) at your CURRENT attack speed, to tune aoe_hold_secs.

Holds the attack key for a few seconds while grabbing the game window as fast as it can. Every cast
flashes the skill effect, so the frame-to-frame change pulses once per cast; the strongest repeat
interval of that signal (autocorrelation) is the cast time.

    python measure_cast_time.py [maps/<map>.json] [secs]      (default maps/middle_road2.json, 5 s)

Stand her somewhere safe (mobs in front are fine -- the effect is what is measured), stop any farm
run, run this, don't touch the keyboard. A started cast always finishes, so a hold of h seconds gives
floor(h / cast_time) + 1 casts (2026-10-21: cast ~0.6 s, so 1.05 = 2 casts).
"""
import sys, time
import cv2
import numpy as np
import core
import watermap

LO, HI = 0.2, 1.5            # plausible cast time range (s)


def cast_period(ts, sig, lo=LO, hi=HI, rate=200.0, harmonic=0.8):
    """Repeat interval (s) of `sig` sampled at times `ts`, searched in [lo, hi]; also returns its
    autocorrelation strength (0..1, ~>0.3 = a clear rhythm). A pulse train repeats at P AND at 2P,
    3P...; noise can make 2P the single highest peak (first live run read 0.59 s), so this takes
    the SHORTEST local peak at least `harmonic` x the highest -- the fundamental. Pure: unit-tested."""
    ts = np.asarray(ts, float); sig = np.asarray(sig, float)
    grid = np.arange(ts[0], ts[-1], 1.0 / rate)
    x = np.interp(grid, ts, sig)
    x = x - x.mean()
    if not x.any():
        return None, 0.0
    ac = np.correlate(x, x, "full")[len(x) - 1:]
    ac = ac / ac[0]
    a, b = int(lo * rate), min(int(hi * rate), len(ac) - 1)
    if b <= a:
        return None, 0.0
    win = ac[a:b]
    best = float(win.max())
    peaks = [i for i in range(1, len(win) - 1)
             if win[i] >= win[i - 1] and win[i] >= win[i + 1] and win[i] >= harmonic * best]
    k = a + (peaks[0] if peaks else int(np.argmax(win)))
    return k / rate, float(ac[k])


def main():
    cfg = watermap.load_map(sys.argv[1] if len(sys.argv) > 1 else "maps/middle_road2.json")
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    key = cfg.get("attack_key", "c")
    if not core.focus():
        print("could not focus the game"); return 1
    time.sleep(0.5)
    ts, sig, prev = [], [], None
    print(f"[cast] holding '{key}' for {secs:g} s ...")
    core.kb.safe_press(key)
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < secs:
            f = core.capture()
            if f is None:
                continue
            g = cv2.cvtColor(cv2.resize(f, None, fx=0.25, fy=0.25), cv2.COLOR_BGR2GRAY).astype(np.int16)
            if prev is not None:
                ts.append(time.perf_counter() - t0); sig.append(float(np.abs(g - prev).mean()))
            prev = g
    finally:
        core.kb.safe_release(key); core.kb.safe_release_all()
    if len(ts) < 20:
        print(f"[cast] only {len(ts)} frames -- too few to measure"); return 1
    skip = np.searchsorted(ts, 0.3)               # drop the wind-up of the first cast
    period, strength = cast_period(ts[skip:], sig[skip:])
    fps = len(ts) / ts[-1]
    print(f"[cast] {len(ts)} frames at {fps:.0f} fps")
    np.savetxt("logs/cast_signal.csv", np.c_[ts, sig], delimiter=",", header="t,diff", comments="")
    print("[cast] raw signal -> logs/cast_signal.csv")
    if period is None or strength < 0.2:
        print(f"[cast] no clear rhythm (strength {strength:.2f}) -- run again, maybe with mobs in front")
        return 1
    print(f"[cast] cast time ~ {period:.2f} s  (rhythm strength {strength:.2f})")
    # a started cast finishes even if x is released: N casts need the hold to outlast the start of
    # cast N ((N-1) * period) but end before cast N+1 starts (N * period)
    now = float(cfg.get("aoe_hold_secs", 1.05))
    for n in (2, 3):
        print(f"[cast] {n} casts: aoe_hold_secs between {(n - 1) * period + 0.05:.2f} and "
              f"{n * period - 0.05:.2f}  (mid {(n - 0.5) * period:.2f})")
    print(f"[cast] now {now:.2f} -> {int(now // period) + 1} cast(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
