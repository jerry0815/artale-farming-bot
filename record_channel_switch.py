"""Frame collector for the channel-hop feature. Presses NO keys and clicks nothing -- you do the
channel switch by hand while this records:

  * every LEFT CLICK -> clicks/click_NN.png (the frame at the moment you clicked, with a red
    marker in clicks/click_NN_marked.png) + a line in clicks.jsonl with the click's
    WINDOW-relative (x, y) -- these become the button templates / click offsets.
  * a steady stream of frames (default 2 fps) -> stream/ -- catches the loading screen, the
    login / character-select screens and the minimap after landing.
  * F9 -> marks.jsonl gets a timestamped mark (press it when the map has loaded and the minimap
    is readable, i.e. step 8), and the frame is saved as marks/mark_NN.png.
  * F10 -> stop.

Usage:
    python record_channel_switch.py [outdir] [fps]
      outdir : default datasets/channel_switch/<timestamp>
      fps    : stream rate (default 2)

Do the full cycle 2-3 times (目錄 -> 頻道 -> random-channel icon -> 確定 -> loading -> 登入 ->
選擇角色 -> map), ideally once landing in a channel WITH other players and once without.
"""
import ctypes, json, os, sys, threading, time
import cv2
import core as R
from pynput import keyboard as pk, mouse as pm

try:                                   # match mss's physical-pixel coords for pynput's mouse x/y
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "datasets", "channel_switch", time.strftime("%Y%m%d_%H%M%S"))
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    for d in ("clicks", "stream", "marks"):
        os.makedirs(os.path.join(outdir, d), exist_ok=True)
    if R.capture() is None:
        print("[chsw] game window not found"); return

    t0 = time.time()
    stop = threading.Event()
    lock = threading.Lock()
    counts = {"click": 0, "mark": 0}

    def log(name, rec):
        with open(os.path.join(outdir, name), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def on_click(x, y, button, pressed):
        if not pressed or button != pm.Button.left:
            return
        img, box = R.capture(), R._window_box()      # grabbed before the game redraws for the click
        if img is None or box is None:
            return
        wx, wy = x - box["left"], y - box["top"]
        if not (0 <= wx < box["width"] and 0 <= wy < box["height"]):
            return                                    # click outside the game window -> ignore
        with lock:
            n = counts["click"]; counts["click"] += 1
        base = os.path.join(outdir, "clicks", f"click_{n:02d}")
        cv2.imwrite(base + ".png", img)
        marked = img.copy()
        cv2.circle(marked, (wx, wy), 12, (0, 0, 255), 2)
        cv2.imwrite(base + "_marked.png", marked)
        log("clicks.jsonl", {"n": n, "t": round(time.time() - t0, 2), "x": wx, "y": wy,
                             "win_w": box["width"], "win_h": box["height"]})
        print(f"[chsw] click {n:02d} @ window ({wx},{wy})  t={time.time() - t0:.1f}s")

    def on_key(key):
        if key == pk.Key.f10:
            stop.set(); return False
        if key == pk.Key.f9:
            img = R.capture()
            with lock:
                n = counts["mark"]; counts["mark"] += 1
            if img is not None:
                cv2.imwrite(os.path.join(outdir, "marks", f"mark_{n:02d}.png"), img)
            log("marks.jsonl", {"n": n, "t": round(time.time() - t0, 2)})
            print(f"[chsw] mark {n:02d}  t={time.time() - t0:.1f}s")

    ml = pm.Listener(on_click=on_click); kl = pk.Listener(on_press=on_key)
    ml.start(); kl.start()
    print(f"[chsw] recording to {outdir}/  -- do the channel switch by hand. "
          f"F9 = 'map loaded' mark, F10 = stop")
    n, interval = 0, 1.0 / fps
    try:
        while not stop.is_set():
            img = R.capture()
            if img is not None:
                cv2.imwrite(os.path.join(outdir, "stream", f"f_{n:05d}_{time.time() - t0:07.2f}.png"), img)
                n += 1
            stop.wait(interval)
    except KeyboardInterrupt:
        pass
    finally:
        ml.stop(); kl.stop()
    print(f"[chsw] done: {counts['click']} clicks, {counts['mark']} marks, {n} stream frames -> {outdir}/")


if __name__ == "__main__":
    main()
