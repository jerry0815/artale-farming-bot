"""YOLO detector for the lie-check screens (monster / curse / transparent-shape).

Additive backstop to the template checks in detection.detect_lie_check: returns [] when
models/lie_check_yolo.pt is absent, so with no model the system behaves exactly as the
template-only path. Never raises -- it rides the safety loop."""
import os

_MODEL_PATH = os.path.join("models", "lie_check_yolo.pt")
_CLASS_NAMES = {0: "monster_check", 1: "curse", 2: "transparent_shape"}
_model_cache = {}          # path -> model, or False for known-absent/failed


def reset_cache():
    _model_cache.clear()


def _load(path=_MODEL_PATH):
    if path in _model_cache:
        return _model_cache[path]
    if not os.path.exists(path):
        _model_cache[path] = False
        return False
    try:
        from ultralytics import YOLO
        m = YOLO(path)
    except Exception as e:
        print(f"[lie-yolo] load failed: {e}")
        m = False
    _model_cache[path] = m
    return m


def detect_lie_check_yolo(frame, conf=0.35, model_path=_MODEL_PATH, imgsz=1280):
    """[(class_name, conf, (x0,y0,x1,y1)), ...] for lie-check popups, or [] if the model is
    absent / the frame is bad / inference fails. Never raises."""
    if frame is None or not hasattr(frame, "shape"):
        return []
    m = _load(model_path)
    if not m:
        return []
    try:
        r = m.predict(frame, imgsz=imgsz, conf=conf, verbose=False)[0]
    except Exception as e:
        print(f"[lie-yolo] predict failed: {e}")
        return []
    out = []
    for b in r.boxes:
        cls = int(b.cls[0]); c = float(b.conf[0])
        x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
        out.append((_CLASS_NAMES.get(cls, str(cls)), c, (x0, y0, x1, y1)))
    return out
