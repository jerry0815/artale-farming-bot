"""YOLO mob detector for water maps -- drop-in replacement for the fish template matcher.

Returns boxes in the SAME shape as fish.scan's dets: [(score, x, y, w, h)] in full-frame
coords, so approach_shoot / depletion counting are detector-agnostic. Both classes
(fishhouse, goby) are just "mob" to the approach logic.
"""
_MODEL_CACHE = {}


def load_yolo(path="models/mob_yolo.pt"):
    if path not in _MODEL_CACHE:
        from ultralytics import YOLO           # lazy import (heavy)
        _MODEL_CACHE[path] = YOLO(path)
    return _MODEL_CACHE[path]


def yolo_boxes(model, frame_bgr, roi=None, conf=0.3, imgsz=640):
    """Detected mob boxes [(score, x, y, w, h)] in full-frame coords. `roi`=(x0,y0,x1,y1)
    restricts + speeds up inference (offset added back)."""
    sub, ox, oy = frame_bgr, 0, 0
    if roi is not None:
        x0, y0, x1, y1 = roi
        if x1 - x0 < 8 or y1 - y0 < 8:
            return []
        sub, ox, oy = frame_bgr[y0:y1, x0:x1], x0, y0
    r = model.predict(sub, imgsz=imgsz, conf=conf, verbose=False)[0]
    out = []
    for b in r.boxes:
        x0f, y0f, x1f, y1f = b.xyxy[0].tolist()
        out.append((float(b.conf[0]), int(x0f) + ox, int(y0f) + oy,
                    int(x1f - x0f), int(y1f - y0f)))
    return out
