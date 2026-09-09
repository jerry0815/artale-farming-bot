"""Locate the player on screen via the red HP bar above the character's head.

Monsters also show red HP bars when hit, so we CENTER-BIAS the pick: the camera keeps
the player near screen-center, so among red horizontal bars we prefer the one closest to
center. Returns the player's screen position as (x, feet_y) -- x from the bar center,
feet_y estimated a fixed offset below the bar (the character stands there). Pure w.r.t.
the game (operates on a passed frame); the geometry filters are unit-testable.

Tunables live in a map config under "player" (all optional):
  {"foot_offset":135, "hue_lo":120, "region":[x0,y0,x1,y1],
   "min_w":18,"max_w":140,"min_h":2,"max_h":16}
"""
import time as _t
import cv2
import numpy as np

DEFAULTS = dict(foot_offset=135, hue_lo=120, min_w=18, max_w=140, min_h=2, max_h=16)


def _red_mask(bgr, sat_val_lo=120):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lo, hi = sat_val_lo, 255
    m1 = cv2.inRange(hsv, (0, lo, lo), (10, hi, hi))
    m2 = cv2.inRange(hsv, (170, lo, lo), (180, hi, hi))
    return cv2.bitwise_or(m1, m2)


def bar_candidates(bgr, region=None, cfg=None):
    """Red horizontal-bar blobs in `region`: list of (cx, cy, w, h) in FULL-frame coords.
    Filters by bar-like geometry (wide, thin)."""
    c = {**DEFAULTS, **(cfg or {})}
    H, W = bgr.shape[:2]
    x0, y0, x1, y1 = region or (int(W * 0.05), 55, int(W * 0.95), int(H * 0.93))
    sub = bgr[y0:y1, x0:x1]
    mask = _red_mask(sub, c["hue_lo"])
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if c["min_w"] <= bw <= c["max_w"] and c["min_h"] <= bh <= c["max_h"] and bw >= bh * 2:
            out.append((int(cent[i][0]) + x0, int(cent[i][1]) + y0, bw, bh))
    return out


def pick_center(cands, frame_shape, y_weight=0.4, y_focus=0.45):
    """Choose the candidate nearest screen center (x), lightly weighting an upper-mid y.
    Returns (cx, cy, w, h) or None."""
    if not cands:
        return None
    H, W = frame_shape[:2]
    return min(cands, key=lambda b: abs(b[0] - W / 2) + y_weight * abs(b[1] - H * y_focus))


def _white_mask(frame_bgr):
    """Bright/near-white pixels (the name-tag TEXT), background-independent -- the KenYu
    trick that beats the semi-transparent name-tag box."""
    g = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    return cv2.inRange(g, 150, 255)


def load_nametag(path):
    """Load a name-tag crop (BGR) and return its white-mask template."""
    im = cv2.imread(path, cv2.IMREAD_COLOR)
    if im is None:
        raise RuntimeError(f"nametag template not found: {path}")
    return _white_mask(im)


class NametagAnchor:
    """Locate the player by the (character-specific) name tag, KenYu-style: white-mask the
    text (background-independent), match with SQDIFF searching LOCALLY around last frame's
    location first (temporal lock; never jumps to UI text), split into halves (survives a
    mob covering part of the name). locate(frame) -> (player_x, feet_y) or None."""

    def __init__(self, tag_white, feet_offset=6, split_width=45, local_radius=60,
                 accept_thres=0.55, y_lo_frac=0.22, y_hi_frac=0.86, stale_grace=3):
        self.tag = tag_white
        self.h, self.w = tag_white.shape
        self.feet_offset = feet_offset
        self.local_radius = local_radius
        self.accept_thres = accept_thres
        self.y_lo_frac, self.y_hi_frac = y_lo_frac, y_hi_frac
        self.stale_grace = stale_grace
        self._miss = 0
        self.last = None                                  # top-left (x,y) of last good match
        n = max(1, self.w // max(8, split_width))
        ws = self.w // n
        self.splits = [(i * ws, tag_white[:, i * ws:((i + 1) * ws if i < n - 1 else self.w)])
                       for i in range(n)]

    def _match(self, fw, split, x0, region):
        rx0, ry0, rx1, ry1 = region
        sw = split.shape[1]
        sub = fw[ry0:ry1, rx0:rx1]
        if sub.shape[0] < self.h or sub.shape[1] < sw:
            return None, 1.0
        r = cv2.matchTemplate(sub, split, cv2.TM_SQDIFF_NORMED)
        mn, _mx, ml, _ = cv2.minMaxLoc(r)
        return (rx0 + ml[0] - x0, ry0 + ml[1]), float(mn)   # -> nametag top-left

    def locate(self, frame_bgr):
        fw = _white_mask(frame_bgr)
        H, W = fw.shape
        best = None                                       # (score, ntx, nty)
        for x0, split in self.splits:
            loc, score = None, 1.0
            if self.last is not None:                     # local search around last lock
                lx, ly = self.last[0] + x0, self.last[1]
                region = (max(0, lx - self.local_radius), max(0, ly - self.local_radius),
                          min(W, lx + self.local_radius + split.shape[1]),
                          min(H, ly + self.local_radius + self.h))
                loc, score = self._match(fw, split, x0, region)
            if loc is None or score >= self.accept_thres:  # (re)acquire: global, play-area only
                region = (0, int(H * self.y_lo_frac), W, int(H * self.y_hi_frac))
                loc, score = self._match(fw, split, x0, region)
            if loc is not None and (best is None or score < best[0]):
                best = (score, loc[0], loc[1])
        if best is None or best[0] >= self.accept_thres:
            # brief occlusion (VFX/mob over the tag): keep the last lock for a few frames
            if self.last is not None and self._miss < self.stale_grace:
                self._miss += 1
                return (self.last[0] + self.w // 2, self.last[1] - self.feet_offset)
            return None                                   # lost for real
        self._miss = 0
        self.last = (best[1], best[2])
        return (best[1] + self.w // 2, best[2] - self.feet_offset)


class CompositeAnchor:
    """Try several anchors in order (e.g. name tag first, then the 稱號/title banner) and
    return the first that locates the player -- so if one is covered (attack VFX, a mob),
    the other still finds you. `which` names the anchor that last succeeded (for debug)."""

    def __init__(self, anchors, max_jump=None):
        self.anchors = anchors
        self.which = None
        self.last = None
        self.max_jump = max_jump      # reject a fallback that leaps > this (screen px) from the
        self._good = None             # last ACCEPTED pos -- guards a phantom nametag match
        self._none = 0                # straight None returns -> drop _good to re-acquire (below)
        self._last_reject_log = 0.0   # throttle for the "fallback rejected by cap" diagnostic

    def push(self, player_box):
        """Forward a shared-pass player box to the FIRST anchor if it supports .push (the
        YoloPlayerAnchor). locate() then uses it for anchor 0 and still falls through to the
        nametag/title anchors when the pushed box is None (player missed)."""
        a0 = self.anchors[0] if self.anchors else None
        if a0 is not None and hasattr(a0, "push"):
            a0.push(player_box)

    def locate(self, frame_bgr):
        for i, a in enumerate(self.anchors):
            p = a.locate(frame_bgr)
            if p is None:
                continue
            # Phantom guard: a fallback anchor (e.g. the nametag false-locked on a static blob /
            # another player's name) can report a spot far from where the player actually is.
            # Reject a leap > max_jump from the last accepted position -> treat as "not found"
            # so the caller waits for a real re-lock instead of chasing the phantom.
            #
            # BUT skip this for a TRUSTED anchor (YoloPlayerAnchor): whenever YOLO detects the
            # class-2 box we USE it. Guarding it here lets a fallback's phantom _good reject the
            # primary's REAL move: if a pinned nametag phantom holds _good at x=1485 while the
            # player really walked to x=510, every valid YOLO box is 900px away -> rejected forever,
            # and since the nametag keeps "succeeding" _none never hits the re-acquire release. So
            # the low-priority phantom permanently locks out the high-priority real anchor. The
            # trusted anchor self-limits (near-pick + re-acquire), so accept its box and reset _good.
            # The guard still protects phantom-prone fallbacks (nametag/title), which set no flag.
            trusted = getattr(a, "trusted", False)
            if (not trusted and self.max_jump is not None and self._good is not None
                    and abs(p[0] - self._good[0]) > self.max_jump):
                now = _t.time()                        # DIAGNOSTIC (throttled): a fallback located
                if now - self._last_reject_log > 3.0:  # her but the guard rejected it as a far jump
                    self._last_reject_log = now
                    print(f"[anchor] fallback #{i} @x={p[0]} REJECTED: {abs(p[0] - self._good[0])}px "
                          f"> cap {self.max_jump} from last x={self._good[0]}")
                continue
            self.which = i
            self.last = getattr(a, "last", None)
            self._good = p
            self._none = 0
            return p
        self.which = None
        self._none += 1
        if self._none >= 3:           # rejecting/missing too long -> drop the guard so a real
            self._good = None         # detection can re-acquire (else max_jump traps her lost)
        return None


class HPBarAnchor:
    """The red HP-bar anchor with sticky tracking (fallback). locate -> (x, feet) or None."""

    def __init__(self, cfg=None):
        self.cfg = cfg
        self.last = None

    def locate(self, frame_bgr):
        p = find_player(frame_bgr, cfg=self.cfg, near=self.last)
        if p:
            self.last = p
        return p


def pick_near(cands, near, foot_offset):
    """Candidate whose implied feet (cx, cy+foot_offset) is nearest `near`=(x,feet)."""
    nx, ny = near
    return min(cands, key=lambda b: (b[0] - nx) ** 2 + ((b[1] + foot_offset) - ny) ** 2)


def find_player(bgr, region=None, cfg=None, debug=False, near=None, near_tol=130):
    """Player screen position (x, feet_y), or None. If `near`=(x,feet) (last known player)
    is given, LOCK onto the candidate nearest it (within near_tol) so transient monster HP
    bars near screen-center don't steal the anchor; else fall back to center-bias. With
    debug=True returns {player, bar, candidates}."""
    c = {**DEFAULTS, **(cfg or {})}
    fo = c["foot_offset"]
    cands = bar_candidates(bgr, region=region, cfg=c)
    bar = None
    if near is not None and cands:                    # sticky: prefer the bar nearest last pos
        b = pick_near(cands, near, fo)
        if abs(b[0] - near[0]) <= near_tol and abs((b[1] + fo) - near[1]) <= near_tol:
            bar = b
    if bar is None:                                   # re-acquire: nearest screen center
        bar = pick_center(cands, bgr.shape)
    player = None if bar is None else (bar[0], bar[1] + fo)
    if debug:
        return {"player": player, "bar": bar, "candidates": cands}
    return player
