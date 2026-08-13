"""Clean frame collector for the YOLO dragon dataset. Presses NO keys — you farm
manually while this grabs raw game-window frames. Saved frames carry NO overlay
(unlike record_climb_frames.py) so they are usable as training images.

Usage:
    python capture_frames.py [outdir] [fps]
      outdir : where to save frames (default: datasets/dragons/images)
      fps    : frames per second (default: 8)
Farm across BOTH platforms and varied densities (0/few/many/overlapping);
press F8 to stop. Aim for ~200-400 frames.
"""
import os, sys, time
import cv2
import recovery as R
from pynput.keyboard import Listener


def save_frame(outdir, n, img):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"frame_{n:05d}.png")
    cv2.imwrite(path, img)
    return path


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join("datasets", "dragons", "images")
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    cap_secs = 600.0

    if not R.focus():
        print("could not focus game window"); return
    lis = Listener(on_press=R.kb.on_press); lis.start()   # F8 stops
    R.kb.pause = False
    print(f"[cap] farm both platforms; press F8 to stop. Saving clean frames to {outdir}/")

    t0, n, interval, next_t, misses = time.time(), 0, 1.0 / fps, 0.0, 0
    try:
        while time.time() - t0 < cap_secs:
            if R.kb.pause:
                print("[cap] F8 -> stop"); break
            t = time.time() - t0
            if t < next_t:
                time.sleep(0.01); continue
            next_t = t + interval
            img = R.capture()
            if img is None:
                misses += 1
                if misses in (1, 10, 50):
                    print(f"[cap] capture() None x{misses} (game window not found?)")
                continue
            save_frame(outdir, n, img)
            n += 1
            if n % 25 == 0:
                print(f"[cap] saved {n} frames")
    finally:
        R.kb.safe_release_all(); lis.stop()
    print(f"[cap] wrote {n} frames to {outdir}/")


if __name__ == "__main__":
    main()
