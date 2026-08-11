"""Record the character's position trail (read-only) while YOU manually walk her
from a fallen platform back to farming. The trail reveals the exact path (x moves,
climb points, layer transitions) so recovery can replay it.

Usage: python trail_log.py [seconds]   (default 20)
Start it, then immediately do the manual recovery in-game.
"""
import sys, time
from recovery import get_character_color as pos   # robust color-based sensing

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0


def main():
    print(f"recording {secs}s ... do the manual recovery NOW")
    t0 = time.time()
    trail = []
    while time.time() - t0 < secs:
        x, y = pos()
        t = round(time.time() - t0, 1)
        if not trail or (x, y) != (trail[-1][1], trail[-1][2]):   # log only changes
            trail.append((t, x, y))
            print(f"  t={t:4} ({x},{y})")
        time.sleep(0.15)
    print("\n--- distinct waypoints (in order) ---")
    for t, x, y in trail:
        print(f"  t={t:4} ({x},{y})")


if __name__ == "__main__":
    main()
