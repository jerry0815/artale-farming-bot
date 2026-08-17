"""Live band-verification interface (READ-ONLY: never presses a key).

Walk each platform edge-to-edge; this prints your live (x,y), which PROPOSED
node band you're in, and accumulates the true min/max extent of every label it
sees. Use it to confirm/adjust the new PORTAL_BOT / L4_RIGHT bands (and the
MID_R narrowing) in the portal-rope-recovery spec before we implement.

  python verify_bands.py

Press F8 to stop -> prints an extent summary. Walk to the far LEFT and far RIGHT
of each platform (and to its lowest droop) so the min/max captures the whole
ledge. UNMAPPED reads (in no band) are flagged -> those are spots a band must
grow to cover.
"""
import time
from pynput.keyboard import Listener
import keyboard as kb
import recovery as rec

# PROPOSED bands = navmap's current set with MID_R narrowed (150->142) and the two
# new right-side nodes added. First match wins (same rule as navmap.classify_node).
# Keep this in sync with the spec; it is what the map WILL classify post-implementation.
PROPOSED = [
    ("TOP_FARM",     0,  95, 60, 140),
    ("REST",        96, 110, 60, 100),
    ("MID",        111, 131, 80, 140),
    ("BOTTOM_R",   129, 142, 141, 166),   # right twin of BOTTOM_FARM (central-reachable)
    ("LOWER_R",    143, 151, 128, 163),   # right twin of LOWER_LEDGE (portal-rope landing; needs rope)
    ("BOTTOM_FARM",132, 156, 55, 100),
    ("PORTAL_BOT", 178, 190, 108, 162),   # NEW: before LOWER_LEDGE (carved out)
    ("LOWER_LEDGE",152, 200, 40, 160),    # y_lo 157->152 so rope-transit below LOWER_R reads as ledge
]

# Labels we're actively calibrating -> highlighted in the live line.
NEW = {"PORTAL_BOT", "LOWER_R", "BOTTOM_R"}


def classify(x, y):
    for name, ylo, yhi, xlo, xhi in PROPOSED:
        if ylo <= y <= yhi and xlo <= x <= xhi:
            return name
    return None


def main():
    if not rec.focus():
        print("[verify] could not focus game"); return
    print("[verify] READ-ONLY live band check. Walk each platform L<->R edge.")
    print("[verify] F8 to stop. New nodes under test:", ", ".join(sorted(NEW)))
    print("[verify] WALK ONE PLATFORM PER RUN. The RAW box below is ground truth.")
    lis = Listener(on_press=kb.on_press); lis.start()   # F8 -> kb.pause
    raw = None            # [xmin, xmax, ymin, ymax, n]  <- pure reads, no bands
    labels = {}           # proposed label -> count (secondary hint only)
    last_line = None
    try:
        while not kb.pause:
            x, y = rec.stable_char(4)
            if x < 0:
                time.sleep(0.05); continue
            if raw is None:
                raw = [x, x, y, y, 1]
            else:
                raw[0] = min(raw[0], x); raw[1] = max(raw[1], x)
                raw[2] = min(raw[2], y); raw[3] = max(raw[3], y); raw[4] += 1
            label = classify(x, y)
            labels[label if label else "UNMAPPED"] = labels.get(label if label else "UNMAPPED", 0) + 1
            # live: current read + the running RAW box so far
            line = (f"({x:>3},{y:>3})  now={label or 'UNMAPPED':<11} "
                    f"RAW x[{raw[0]}-{raw[1]}] y[{raw[2]}-{raw[3]}]")
            if line != last_line:
                print(line); last_line = line
            time.sleep(0.08)
    finally:
        lis.stop()
    if raw is None:
        print("[verify] no reads"); return
    print("\n=== THIS PLATFORM (raw ground truth, ignores proposed bands) ===")
    print(f"  x[{raw[0]}-{raw[1]}]  y[{raw[2]}-{raw[3]}]  n={raw[4]}")
    print(f"  -> suggested band (with ~3px margin): "
          f"y {raw[2]-3}-{raw[3]+3}, x {raw[0]-3}-{raw[1]+3}")
    print("  proposed-label breakdown while walking it:",
          ", ".join(f"{k}={v}" for k, v in sorted(labels.items(), key=lambda kv: -kv[1])))
    print("  (a single platform hitting multiple labels = my seam is wrong; tell me its name)")


if __name__ == "__main__":
    main()
