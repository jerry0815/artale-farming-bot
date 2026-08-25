"""Capture LIVE mob templates from THIS game for template matching.

The KenYu green-screen sprites match this game's rendering poorly. Cropping the mob
straight from a live frame (correct art, scale, underwater tint) matches far better --
the same trick capture_lie_template.py uses for lie-check screens.

Usage:
    python capture_mob_template.py <map> <mob_name>
    e.g.  python capture_mob_template.py deep_sea_2 fishhouse

Stand where several instances of the mob are on screen, then draw a TIGHT box around
each one (as little background as possible). ENTER saves all boxes, ESC/C cancels.
Crops are saved to assets/mob/<map>/<mob_name>_<n>.png (auto-incrementing), and matched
maskless with TM_CCOEFF_NORMED. Capture a handful per mob (different poses) for robustness.
"""
import os
import sys
import glob
import cv2


def capture():
    import recovery
    return recovery.capture()


def _next_index(outdir, name):
    idx = 0
    for p in glob.glob(os.path.join(outdir, f"{name}_*.png")):
        stem = os.path.splitext(os.path.basename(p))[0]
        try:
            idx = max(idx, int(stem.rsplit("_", 1)[1]))
        except ValueError:
            pass
    return idx + 1


def main():
    if len(sys.argv) < 3:
        print("usage: python capture_mob_template.py <map> <mob_name>")
        print("e.g.:  python capture_mob_template.py deep_sea_2 fishhouse")
        return
    mp, name = sys.argv[1], sys.argv[2]
    outdir = os.path.join("assets", "mob", mp)
    os.makedirs(outdir, exist_ok=True)

    img = capture()
    if img is None:
        print("no frame -- is the game window open and visible (not minimized)?")
        return

    print("Draw a TIGHT box around each mob instance. ENTER = save all, ESC/C = cancel.")
    rois = cv2.selectROIs("select mob templates (tight crops)", img,
                          showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()

    n = _next_index(outdir, name)
    saved = 0
    for (x, y, w, h) in rois:
        if w == 0 or h == 0:
            continue
        out = os.path.join(outdir, f"{name}_{n}.png")
        cv2.imwrite(out, img[y:y + h, x:x + w])
        print(f"saved {out}  ({w}x{h})")
        n += 1
        saved += 1
    print(f"saved {saved} crop(s) to {outdir}")
    if saved:
        rel = outdir.replace(os.sep, "/")
        print(f'set in maps/{mp}.json:  "mob_template_dir": "{rel}"')
        print("then preview in the panel or: python recovery.py fishcount --map maps/"
              f"{mp}.json --thr 0.6")


if __name__ == "__main__":
    main()
