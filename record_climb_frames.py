"""Sense-only climb recorder WITH screen frames, for cross-comparing the on-screen
ropes against the minimap (x,y) trajectory. Presses NO keys.

Each tick it grabs ONE game-window frame, reads the minimap (x,y) from that SAME
frame (so screen and minimap are synchronized), stamps t + (x,y) onto the frame, and
saves it. Also writes frames.jsonl mapping each frame to its (t,x,y).

Usage:
    python record_climb_frames.py [outdir] [fps]
      outdir : where to save frames (default: climb_frames)
      fps    : frames per second (default: 5)
Climb bottom->top SLOWLY (so the transition is well sampled); press F8 to stop.
"""
import os, sys, json, time
import cv2
from pynput.keyboard import Listener
import recovery as R


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "climb_frames"
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    cap_secs = 40.0
    os.makedirs(outdir, exist_ok=True)

    if not R.focus():
        print("could not focus game window"); return
    lis = Listener(on_press=R.kb.on_press); lis.start()          # F8 stops
    print(f"[frames] climb bottom->top SLOWLY; press F8 to stop. Saving to {outdir}/")

    reads, t0, n, interval, next_t = [], time.time(), 0, 1.0 / fps, 0.0
    try:
        while time.time() - t0 < cap_secs:
            if R.kb.pause:
                break
            t = time.time() - t0
            if t < next_t:
                time.sleep(0.01); continue
            next_t = t + interval
            img = R.capture()
            if img is None:
                continue
            x, y = R.get_character_full(img)                     # same frame -> synced
            reads.append((round(t, 3), int(x), int(y), n))
            cv2.putText(img, f"t={t:6.2f}s  minimap=({x},{y})  frame {n}",
                        (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2, cv2.LINE_AA)
            cv2.imwrite(os.path.join(outdir, f"frame_{n:03d}.png"), img)
            n += 1
    finally:
        R.kb.safe_release_all(); lis.stop()

    with open(os.path.join(outdir, "frames.jsonl"), "w", encoding="utf-8") as f:
        for t, x, y, i in reads:
            f.write(json.dumps({"t": t, "x": x, "y": y, "frame": f"frame_{i:03d}.png"}) + "\n")
    print(f"[frames] wrote {n} frames + frames.jsonl to {outdir}/")


if __name__ == "__main__":
    main()
