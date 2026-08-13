"""Live test of the YOLO dragon detector against the running game window.

Presses NO keys. Focuses the game, captures the screen, and runs the detector
over the active-playfield band (monsters.MOTION_ROI_BY_NODE["TOP_FARM"]) so you
can see what it finds. This is a platform-agnostic detection sanity check — the
BOT itself still counts per platform via monsters.count_dragons_best(node); this
tool does not.

Usage:
  python live_test_yolo.py         # snapshot -> annotated PNG + count
  python live_test_yolo.py loop    # live count ~1/sec until F8 (F8 = kb.pause)

Annotated image -> debug_output/live_yolo.png:
  yellow rect = active-playfield band (TOP_FARM) that gates the count
  red box     = a dragon whose CENTER is inside the band  -> counted
  gray box    = a dragon outside the band (minimap/UI area) -> not counted
"""
import os, sys, time
import cv2
import recovery as R
import monsters

CONF = 0.35                     # lower to see more, raise to be stricter
# The tighter TOP_FARM band reads better than the wide DEFAULT_MOTION_ROI: the
# camera keeps the player's platform near the same screen height, so this band
# frames the active playfield without pulling in far-off ledges/UI. Extended a
# bit downward to catch dragons lower in frame; bump EXTEND_DOWN to taste.
_b = monsters.MOTION_ROI_BY_NODE["TOP_FARM"]
EXTEND_DOWN = 70
ROI = (_b[0], _b[1], _b[2], _b[3] + EXTEND_DOWN)


def annotate(frame):
    model = monsters.load_dragon_model()
    if model is None:
        print("[live] no models/dragon_yolo.pt -> bot would use motion fallback")
        return frame, 0
    allb = monsters.run_yolo(model, frame, conf=CONF)
    vis = frame.copy()
    x0, y0, x1, y1 = ROI
    cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 255, 255), 2)
    counted = 0
    for (a, b, c, d, sc) in allb:
        inband = monsters.box_center_in_roi((a, b, c, d), ROI)
        cv2.rectangle(vis, (a, b), (c, d), (0, 0, 255) if inband else (150, 150, 150),
                      3 if inband else 1)
        if inband:
            cv2.putText(vis, f"{sc:.2f}", (a, max(0, b - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            counted += 1
    cv2.putText(vis, f"{counted} dragons counted / {len(allb)} seen",
                (x0 + 8, y0 + 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
    return vis, counted


def snapshot():
    if not R.focus():
        print("[live] could not focus game window"); return
    frame = R.capture()
    if frame is None:
        print("[live] capture failed"); return
    os.makedirs("debug_output", exist_ok=True)
    vis, n = annotate(frame)
    out = os.path.join("debug_output", "live_yolo.png")
    cv2.imwrite(out, vis)
    print(f"[live] {n} dragons counted -> {out}")


def loop():
    if not R.focus():
        print("[live] could not focus"); return
    from pynput.keyboard import Listener
    lis = Listener(on_press=R.kb.on_press); lis.start(); R.kb.pause = False
    model = monsters.load_dragon_model()
    if model is None:
        print("[live] no models/dragon_yolo.pt found"); lis.stop(); return
    print("[live] playfield dragon count; F8 to stop")
    try:
        while not R.kb.pause:
            print(f"[live] dragons={monsters.count_dragons_yolo(R.capture, model, ROI)}")
            time.sleep(1.0)
    finally:
        R.kb.safe_release_all(); lis.stop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        loop()
    else:
        snapshot()
