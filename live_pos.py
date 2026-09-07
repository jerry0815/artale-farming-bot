"""READ-ONLY live minimap position readout. Sends NO key input, moves nothing.

Shows the (x, y) the FARMING LOOP actually sees -- recovery.get_character_full (color-first,
the SAME reader swim_to uses) -- with a running per-axis min/max so you can see read jitter.
Stand where you want to measure (e.g. on P4) and watch each axis settle.

Usage:
    python live_pos.py            # stream one updating line until Ctrl+C
    python live_pos.py 12         # take 12 samples, then print a median/range summary
"""
import sys
import time

import recovery


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    xs, ys = [], []
    stream = n is None
    try:
        i = 0
        while n is None or i < n:
            x, y = recovery.get_character_full()
            i += 1
            if x >= 0:
                xs.append(x); ys.append(y)
                line = (f"x={x:4d}  y={y:4d}   |  x[{min(xs)}-{max(xs)}]  "
                        f"y[{min(ys)}-{max(ys)}]  n={len(xs)}")
            else:
                line = "character NOT detected on minimap"
            print(("\r" + line).ljust(70), end="" if stream else "\n", flush=True)
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    if xs:
        sx, sy = sorted(xs), sorted(ys)
        print(f"\n[median] x={sx[len(sx) // 2]}  y={sy[len(sy) // 2]}   "
              f"(x {min(xs)}-{max(xs)}, y {min(ys)}-{max(ys)}, n={len(xs)})")
    else:
        print("\n[live-pos] never detected the character (window gone? wrong minimap ROI?)")


if __name__ == "__main__":
    main()
