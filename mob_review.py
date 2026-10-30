"""Collect frames for fixing mob-detector false positives (item drops, pets, buff icons).

Real mob boxes on middle_road2 are >= ~58 px tall unless cut off at a screen edge; a dropped item
is a ~30 px icon on the floor. With "mob_review" in the map config, the sweep loop saves the CLEAN
frame (no overlay -- usable for training) plus its boxes whenever a short box away from the edges
shows up, throttled and capped:

    "mob_review": {"every_secs": 4, "small_h": 70, "max_frames": 300}

Output: datasets/mob_review/<HHMMSS_ms>.jpg + .json ({"boxes": [[score, x, y, w, h], ...],
"suspects": [...]}) -- review the suspects, then add the frames to the generic mob dataset with
only the REAL mobs labelled and retrain (train_generic_mob.py).
"""
import json, os, time
import cv2

EDGE = 3          # px: a box this close to the left/right edge is a mob cut off by the screen
TOP = 45          # px: below the title bar; a box starting above this is cut off at the top


def suspects(boxes, shape, small_h=70):
    """Boxes [(score, x, y, w, h), ...] that are short but NOT cut off by a screen edge."""
    H, W = shape[:2]
    out = []
    for b in boxes:
        _, x, y, w, h = b[:5]
        cut = x <= EDGE or x + w >= W - EDGE or y <= TOP
        if h < small_h and not cut:
            out.append(b)
    return out


class Reviewer:
    def __init__(self, out_dir="datasets/mob_review", every_secs=4.0, small_h=70, max_frames=300):
        self.out_dir, self.every, self.small_h, self.max = out_dir, float(every_secs), int(small_h), int(max_frames)
        self.last, self.saved = None, 0

    @classmethod
    def from_cfg(cls, cfg):
        c = cfg.get("mob_review")
        if not c:
            return None
        c = c if isinstance(c, dict) else {}
        return cls(every_secs=c.get("every_secs", 4), small_h=c.get("small_h", 70),
                   max_frames=c.get("max_frames", 300))

    def due(self, now):
        if self.saved >= self.max:
            return False
        if self.last is None or now - self.last >= self.every:
            self.last = now
            return True
        return False

    def offer(self, frame, boxes, now=None):
        """Save the frame if it has a suspect box and the throttle allows. Never raises."""
        try:
            if frame is None:
                return False
            sus = suspects(boxes, frame.shape, self.small_h)
            if not sus or not self.due(time.time() if now is None else now):
                return False
            os.makedirs(self.out_dir, exist_ok=True)
            t = time.time()
            base = os.path.join(self.out_dir, time.strftime("%Y%m%d_%H%M%S", time.localtime(t))
                                + f"_{int(t % 1 * 1000):03d}")
            cv2.imwrite(base + ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump({"boxes": [list(map(float, b[:5])) for b in boxes],
                           "suspects": [list(map(float, b[:5])) for b in sus]}, f)
            self.saved += 1
            if self.saved in (1, self.max):
                print(f"[review] saved {self.saved}/{self.max} frames with suspect boxes -> {self.out_dir}/")
            return True
        except Exception:
            return False
