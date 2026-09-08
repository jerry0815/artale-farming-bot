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


def yolo_boxes(model, frame_bgr, roi=None, conf=0.3, imgsz=640, class_conf=None,
               with_class=False):
    """Detected MOB boxes [(score, x, y, w, h)] in full-frame coords (classes 0,1 only).
    `roi`=(x0,y0,x1,y1) restricts + speeds up inference (offset added back).

    `class_conf` = optional {class_id: threshold} for a PER-CLASS confidence floor -- e.g.
    {0: 0.3} keeps a weak fishhouse detected through attack VFX so she doesn't skip the mob
    she's hitting. Inference runs at the LOWEST of (conf, class thresholds) so weak boxes are
    returned by the model, then each class is filtered by its own threshold.

    `with_class=True` appends the class NAME as a 6th element: (score, x, y, w, h, cls_name)
    where cls_name = model.names[cls] (e.g. 'fishhouse', 'goby') -- used for per-mob attack
    skill selection. Default False keeps the 5-tuple, so every existing caller is unchanged."""
    run_conf = min([conf, *(class_conf or {}).values()]) if class_conf else conf
    boxes, ox, oy = _predict(model, frame_bgr, roi, run_conf, imgsz)
    if boxes is None:
        return []
    out = []
    for b in boxes:
        c = int(b.cls[0])
        if c in MOB_CLASSES and float(b.conf[0]) >= (class_conf or {}).get(c, conf):
            t = _box_tuple(b, ox, oy)
            out.append(t + (model.names[c],) if with_class else t)
    return out


def _pick_player(players, near=None, max_jump=None):
    """Choose ONE player box from candidates [(score,x,y,w,h)]. When `near`=(x,y) is given
    (the last known player position), pick the box whose center-x is nearest it -- there is
    only ever one real player, so this keeps a flickering FALSE HP-bar box (on the attack VFX,
    a nametag, a buff icon) from hijacking the anchor and snapping it hundreds of px away.
    Falls back to highest-confidence when there's no prior position.

    `max_jump` (screen px): if even the NEAREST box is farther than this from `near`, the real
    player was missed this frame and only other players'/false boxes remain -- return None (no
    lock) so the caller rides its last position instead of the anchor drifting box-by-box to a
    distant phantom (the P3-crowd runaway to the map edge). None = no cap (legacy)."""
    if not players:
        return None
    if near is not None:
        nx = near[0]
        best = min(players, key=lambda t: abs((t[1] + t[3] // 2) - nx))
        if max_jump is not None and abs((best[1] + best[3] // 2) - nx) > max_jump:
            return None
        return best
    return max(players, key=lambda t: t[0])


def yolo_detect(model, frame_bgr, roi=None, conf=0.3, imgsz=640, near=None, max_jump=None,
                class_conf=None, with_class=False, player_conf=None):
    """ONE inference -> (mobs, player). mobs=[(score,x,y,w,h)] (classes 0,1); player = the
    class-2 box nearest `near`=(x,y) when given (temporal stability against multiple/false
    player detections), else the highest-confidence one. Use when you want both the mobs and
    the player from a single predict() call per frame (the shared-pass: feed the player box to
    YoloPlayerAnchor.push so the loop pays for ONE inference instead of two).

    Mob filtering matches yolo_boxes exactly: `class_conf` = per-class floor {cls: thr},
    `with_class` appends the class name. `player_conf` = the class-2 floor (defaults to `conf`).
    Inference runs at the LOWEST of all these floors so weak boxes are returned, then each is
    filtered by its own floor -- so the mobs equal yolo_boxes(..., class_conf, with_class) and
    the player equals a class-2 pick at player_conf, from a single predict."""
    pconf = conf if player_conf is None else player_conf
    run_conf = min([conf, pconf, *(class_conf or {}).values()])
    boxes, ox, oy = _predict(model, frame_bgr, roi, run_conf, imgsz)
    if boxes is None:
        return [], None
    mobs, players = [], []
    for b in boxes:
        cls = int(b.cls[0]); score = float(b.conf[0])
        t = _box_tuple(b, ox, oy)
        if cls in MOB_CLASSES:
            if score >= (class_conf or {}).get(cls, conf):
                mobs.append(t + (model.names[cls],) if with_class else t)
        elif cls == PLAYER_CLASS and score >= pconf:
            players.append(t)
    return mobs, _pick_player(players, near, max_jump)


class YoloPlayerAnchor:
    """Player anchor backed by the class-2 (player) box of the mob YOLO. locate(frame) ->
    (x, feet_y) or None, where x is the box center and feet_y = box bottom + foot_offset.
    The labeled box is the red HP BAR above the head (a clean, distinctive feature -> high
    precision, and YOLO tracks it through attack VFX that break the template anchor), so
    foot_offset is the bar-bottom-to-feet distance (~123px on deep_sea_2), NOT 0.

    Feed it the ALREADY-COMPUTED player box from yolo_detect() (set via .push) so the loop
    runs a single inference per frame; if none was pushed, it runs its own predict().

    Player recall is ~0.7 (the HP bar is occluded by VFX some frames), so `stale_grace`
    keeps the last position for a few missed frames -- the anchor stays put through a brief
    miss instead of vanishing (the same trick NametagAnchor uses)."""

    def __init__(self, model, roi=None, conf=0.3, imgsz=640, foot_offset=0, stale_grace=3,
                 max_jump=None):
        self.model = model
        self.roi = roi
        self.conf = conf
        self.imgsz = imgsz
        self.foot_offset = foot_offset
        self.stale_grace = stale_grace
        self.max_jump = max_jump          # reject a nearest-box leap farther than this (phantom guard)
        self.last = None
        self._miss = 0
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
            _mobs, box = yolo_detect(self.model, frame_bgr, self.roi, self.conf, self.imgsz,
                                     near=self.last, max_jump=self.max_jump)   # nearest box, capped jump
        if box is None:
            if self.last is not None and self._miss < self.stale_grace:  # ride a brief miss
                self._miss += 1
                return self.last
            return None
        self._miss = 0
        self.last = self._to_pos(box)
        return self.last
