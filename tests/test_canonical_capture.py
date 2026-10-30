"""The game sometimes renders a smaller client area (1920x1080 -> e.g. 1893x1065, everything
shrunk ~1.4%) inside an unchanged window border (11/45/11/11 at 150% DPI). capture() normalises
every frame to the CANONICAL layout: the client resized to 1920x1080 inside the same border, so the
whole bot always sees the same pixels. Clicks map canonical frame points back to the real screen."""
import cv2
import numpy as np
import pytest

import capture

BORDER = (11, 45, 11, 11)                          # left, top, right, bottom


def _window(client_w, client_h, wx=14, wy=900):
    """Window box + client rect (screen coords) for a client of the given size."""
    l, t, r, b = BORDER
    box = {"left": wx, "top": wy, "width": client_w + l + r, "height": client_h + t + b}
    client = (wx + l, wy + t, client_w, client_h)
    return box, client


def _scene(w=1920, h=1080):
    """A canonical-size client with landmarks."""
    img = np.full((h, w, 3), 30, np.uint8)
    cv2.rectangle(img, (100, 200), (300, 260), (0, 200, 255), -1)
    cv2.circle(img, (1700, 1000), 25, (255, 255, 255), -1)
    return img


def _grab(scene, client_w, client_h):
    """What mss grabs: the border (grey) + the scene drawn at the client's actual size."""
    l, t, r, b = BORDER
    win = np.full((client_h + t + b, client_w + l + r, 3), 90, np.uint8)
    win[t:t + client_h, l:l + client_w] = cv2.resize(scene, (client_w, client_h))
    return win


def test_canonical_size_frame_is_returned_untouched():
    box, client = _window(1920, 1080)
    g = capture.canon_geometry(box, client)
    raw = _grab(_scene(), 1920, 1080)
    assert capture.canonicalize(raw, g) is raw                       # fast path: no resize


@pytest.mark.parametrize("cw,ch", [(1893, 1065), (1888, 1062), (1918, 1079)])
def test_shrunk_client_is_restored_to_the_canonical_layout(cw, ch):
    box, client = _window(cw, ch)
    g = capture.canon_geometry(box, client)
    out = capture.canonicalize(_grab(_scene(), cw, ch), g)
    ref = _grab(_scene(), 1920, 1080)
    assert out.shape == ref.shape == (1136, 1942, 3)
    l, t = BORDER[0], BORDER[1]
    diff = np.abs(out[t:t + 1080, l:l + 1920].astype(int) - ref[t:t + 1080, l:l + 1920].astype(int))
    assert np.median(diff) == 0 and diff.mean() < 6                  # same picture, resampling only
    assert tuple(out[t + 230, l + 200]) == (0, 200, 255)              # landmark where it belongs


@pytest.mark.parametrize("cw,ch", [(1920, 1080), (1893, 1065)])
def test_frame_points_map_to_the_real_screen_and_back(cw, ch):
    box, client = _window(cw, ch)
    g = capture.canon_geometry(box, client)
    for fx, fy in ((11 + 200, 45 + 230), (11 + 1700, 45 + 1000), (884, 630)):
        sx, sy = capture.frame_to_screen(fx, fy, g)
        bx, by = capture.screen_to_frame(sx, sy, g)
        assert abs(bx - fx) <= 1 and abs(by - fy) <= 1
    # a canonical-frame landmark lands on the same scene point on the real (shrunk) screen
    sx, sy = capture.frame_to_screen(11 + 1700, 45 + 1000, g)
    k = cw / 1920
    assert abs(sx - (client[0] + 1700 * k)) <= 1 and abs(sy - (client[1] + 1000 * k)) <= 1
