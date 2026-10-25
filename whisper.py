"""Whisper detection: someone messaged you -> the chat bar's latest line turns blue/cyan.

The one-line chat bar at the bottom of the window (left of the + button) shows the newest
message: white = public chat, pink = system (活動硬幣 ...), cyan = whisper. Only that bar is
scanned (not the expanded chat log) and a pixel counts only on a row that is mostly the bar's
neutral dark gray -- so cyan scenery (water maps) right above the bar can't fire it.
Your own cyan messages fire too (same color); that's accepted.

Pure numpy/cv2, no game I/O: safety.lie_check_fast_tick feeds it the frame it already grabbed.
"""
import cv2
import numpy as np

MIN_PIXELS = 40            # cyan text pixels for a line (a short "x" reply is ~200)
_BAND = 220                # the bar lives in the bottom 220 px at every window size seen
_X0, _X1 = 0.08, 0.64      # bar text area, as fractions of the frame width
_NEW_IOU = 0.5             # dilated-mask overlap below this = a different line


def _cyan_mask(frame):
    """Bool mask (over the bottom band / bar x-range) of whisper-cyan pixels on bar rows."""
    h, w = frame.shape[:2]
    roi = frame[max(0, h - _BAND):, int(w * _X0):int(w * _X1)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray_bg = (hsv[..., 1] < 45) & (hsv[..., 2] >= 45) & (hsv[..., 2] <= 95)
    bar_rows = gray_bg.mean(axis=1) > 0.6                     # rows that are mostly the bar
    b, g, r = (roi[..., i].astype(np.int16) for i in range(3))
    m = (r < 70) & (g > 200) & (b > 170) & (g - r > 150)      # whisper cyan, BGR ~(216,248,0)
    m[~bar_rows] = False
    return m


def whisper_pixels(frame):
    """Number of whisper-cyan pixels in the chat bar (>= MIN_PIXELS = a whisper is showing)."""
    if frame is None or not hasattr(frame, "shape"):
        return 0
    return int(_cyan_mask(frame).sum())


class WhisperWatch:
    """Edge detector: update(frame) is True only when a NEW cyan line appears -- the same line
    sitting in the bar for minutes reports once; a different cyan line replacing it reports again."""

    def __init__(self):
        self._last = None                  # dilated mask of the line already reported

    def update(self, frame):
        if frame is None or not hasattr(frame, "shape"):
            return False
        m = _cyan_mask(frame)
        if m.sum() < MIN_PIXELS:
            self._last = None
            return False
        d = cv2.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        last, self._last = self._last, d
        if last is None or last.shape != d.shape:
            return True
        union = np.logical_or(last, d).sum()
        return bool(np.logical_and(last, d).sum() / union < _NEW_IOU)
