"""YOLO mob detector for water maps -- drop-in replacement for the fish template matcher.

Returns boxes in the SAME shape as fish.scan's dets: [(score, x, y, w, h)] in full-frame
coords, so approach_shoot / depletion counting are detector-agnostic. Mob classes
(fishhouse=0, goby=1) are just "mob" to the approach logic; class 2 = player.

The 3-class model lets ONE inference find both the mobs AND the player: yolo_detect()
splits the result into (mobs, player_box) so the loop pays for a single predict() per frame
instead of a separate detector + anchor. YoloPlayerAnchor exposes the player box as a
.locate(frame)->(x, feet_y) anchor (feet = bottom-center of the box).
"""
MOB_CLASSES = (0, 1)        # fishhouse, goby
PLAYER_CLASS = 2

_MODEL_CACHE = {}


def load_yolo(path="models/mob_yolo.pt"):
    if path not in _MODEL_CACHE:
        from ultralytics import YOLO           # lazy import (heavy)
        _MODEL_CACHE[path] = YOLO(path)
    return _MODEL_CACHE[path]


def _predict(model, frame_bgr, roi, conf, imgsz):
    """Run inference, return (result_boxes, ox, oy) or (None, 0, 0) if roi too small."""
    sub, ox, oy = frame_bgr, 0, 0
    if roi is not None:
        x0, y0, x1, y1 = roi
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None, 0, 0
        sub, ox, oy = frame_bgr[y0:y1, x0:x1], x0, y0
    r = model.predict(sub, imgsz=imgsz, conf=conf, verbose=False)[0]
    return r.boxes, ox, oy


def _box_tuple(b, ox, oy):
    x0f, y0f, x1f, y1f = b.xyxy[0].tolist()
    return (float(b.conf[0]), int(x0f) + ox, int(y0f) + oy,
            int(x1f - x0f), int(y1f - y0f))


def yolo_boxes(model, frame_bgr, roi=None, conf=0.3, imgsz=640):
    """Detected MOB boxes [(score, x, y, w, h)] in full-frame coords (classes 0,1 only).
    `roi`=(x0,y0,x1,y1) restricts + speeds up inference (offset added back)."""
    boxes, ox, oy = _predict(model, frame_bgr, roi, conf, imgsz)
    if boxes is None:
        return []
    return [_box_tuple(b, ox, oy) for b in boxes if int(b.cls[0]) in MOB_CLASSES]


def yolo_detect(model, frame_bgr, roi=None, conf=0.3, imgsz=640):
    """ONE inference -> (mobs, player). mobs=[(score,x,y,w,h)] (classes 0,1); player=the
    highest-confidence class-2 box as (score,x,y,w,h) or None. Use when you want both the
    mobs and the player from a single predict() call per frame."""
    boxes, ox, oy = _predict(model, frame_bgr, roi, conf, imgsz)
    if boxes is None:
        return [], None
    mobs, player = [], None
    for b in boxes:
        cls = int(b.cls[0])
        t = _box_tuple(b, ox, oy)
        if cls in MOB_CLASSES:
            mobs.append(t)
        elif cls == PLAYER_CLASS and (player is None or t[0] > player[0]):
            player = t
    return mobs, player


class YoloPlayerAnchor:
    """Player anchor backed by the class-2 (player) box of the mob YOLO. locate(frame) ->
    (x, feet_y) or None, where x is the box center and feet_y its BOTTOM edge (the player
    stands there). The training box includes the red HP bar, so YOLO tracks the player
    through attack VFX that break the template/HP-bar anchors.

    Feed it the ALREADY-COMPUTED player box from yolo_detect() (set via .push) so the loop
    runs a single inference per frame; if none was pushed, it runs its own predict()."""

    def __init__(self, model, roi=None, conf=0.3, imgsz=640, foot_offset=0):
        self.model = model
        self.roi = roi
        self.conf = conf
        self.imgsz = imgsz
        self.foot_offset = foot_offset
        self.last = None
        self._pushed = False
        self._box = None

    def push(self, player_box):
        """Supply the player box from a shared yolo_detect() call (None if not detected)."""
        self._pushed = True
        self._box = player_box

    def _to_pos(self, box):
        _s, x, y, w, h = box
        return (x + w // 2, y + h + self.foot_offset)

    def locate(self, frame_bgr):
        if self._pushed:
            box = self._box
            self._pushed = False
        else:
            _mobs, box = yolo_detect(self.model, frame_bgr, self.roi, self.conf, self.imgsz)
        if box is None:
            return None
        self.last = self._to_pos(box)
        return self.last
