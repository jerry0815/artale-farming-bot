"""channel_hop: screen recognition on synthetic frames (templates pasted at their recorded boxes)
and the arrival check's empty / occupied / unreadable decisions. No game needed."""
import cv2
import numpy as np
import pytest

import channel_hop as ch
import core


def _rec_size():
    import json, os
    with open(os.path.join(ch.TMPL_DIR, "templates.json"), encoding="utf-8") as fh:
        w, h = json.load(fh)["_window"]
    return w, h


def _frame(*names, dim=1.0):
    w, h = _rec_size()                                # at the recording's window size: unscaled
    f = np.full((h, w, 3), 40, np.uint8)
    for n in names:
        t, (x0, y0, x1, y1), _, _ = ch.templates()[n]
        f[y0:y0 + t.shape[0], x0:x0 + t.shape[1]] = (t * dim).astype(np.uint8)
    return f


@pytest.mark.parametrize("name", ch.ORDER)
def test_each_screen_found_at_its_click_point(name):
    got, p = ch.screen(_frame(name))
    _, (x0, y0, _, _), (dx, dy), _ = ch.templates()[name]
    assert got == name and p == (x0 + dx, y0 + dy)


def test_dialog_wins_over_hud_under_it():
    assert ch.screen(_frame("menu", "channel"))[0] == "channel"
    assert ch.screen(_frame("menu", "random", "confirm"))[0] == "confirm"
    # character select: info scroll up (character clicked) -> 選擇角色, else click the character
    assert ch.screen(_frame("charsel", "select"))[0] == "select"
    assert ch.screen(_frame("charsel"))[0] == "charsel"


def test_fading_in_button_is_not_clicked():
    # the login screen fades in over ~5 s; a 登入 click at ~83% brightness was ignored live
    assert ch.find(_frame("login", dim=0.8), "login") is None
    assert ch.find(_frame("login", dim=0.97), "login") is not None


def test_loading_screen_is_nothing():
    assert ch.screen(np.full((1135, 1940, 3), 245, np.uint8)) == (None, None)


def _check(monkeypatch, reads):
    it = iter(reads)
    monkeypatch.setattr(core, "capture", lambda: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(ch, "other_players", lambda f: next(it))
    monkeypatch.setattr(ch.time, "sleep", lambda s: None)
    core.STOP.clear()
    n = sum(r is not None for r in reads)
    return ch.check_empty(settle=0, samples=max(n, 1), gap=0, max_reads=len(reads))


def test_check_empty(monkeypatch):
    assert _check(monkeypatch, [[], [], []]) is True
    assert _check(monkeypatch, [[], [(5, 5)], []]) is False      # anyone, any sample -> occupied
    assert _check(monkeypatch, [None, None]) is None              # minimap never drawn -> unknown


def _minimap_frame(border=True):
    f = np.zeros((1135, 1940, 3), np.uint8)
    if border:
        f[171:173, 15:400] = 255                     # the minimap's white top border
    return f


def test_minimap_drawn_needs_the_white_border():
    assert ch.minimap_drawn(_minimap_frame(True)) is True
    assert ch.minimap_drawn(_minimap_frame(False)) is False
    assert ch.minimap_drawn(np.full((1135, 1940, 3), 40, np.uint8)) is False


def test_other_players_uses_the_general_box(monkeypatch):
    seen = {}
    monkeypatch.setattr(core, "detect_red_dots",
                        lambda mm, **k: seen.setdefault("shape", mm.shape) and [])
    assert ch.other_players(_minimap_frame(False)) is None      # minimap not drawn -> unknown
    assert ch.other_players(_minimap_frame(True)) == []
    assert seen["shape"][:2] == (ch.MM_BOX[3], ch.MM_BOX[2])


def test_check_empty_retries_unreadable_reads(monkeypatch):
    # minimap drawn late: two unreadable grabs, then 3 clean reads -> still judged empty
    assert _check(monkeypatch, [None, None, [], [], []]) is True


def _run(monkeypatch, empties, max_hops=3):
    sent = []
    monkeypatch.setattr(core.notify, "send", lambda key, msg: sent.append((key, msg)))
    it = iter(empties)
    monkeypatch.setattr(ch, "check_empty", lambda settle: next(it))
    monkeypatch.setattr(ch, "hop_once", lambda: True)
    return ch.run(max_hops=max_hops, settle=0), sent


def test_discord_on_empty_channel(monkeypatch):
    ok, sent = _run(monkeypatch, [False, False, True])
    assert ok is True and len(sent) == 1
    key, msg = sent[0]
    assert key == "channel_hop_found" and "empty channel" in msg and "2 hop" in msg


def test_discord_when_giving_up(monkeypatch):
    ok, sent = _run(monkeypatch, [False] * 4, max_hops=3)
    assert ok is False and [k for k, _ in sent] == ["channel_hop_gave_up"]
    assert "3 hops" in sent[0][1]


def test_no_discord_when_stopped(monkeypatch):
    sent = []
    monkeypatch.setattr(core.notify, "send", lambda key, msg: sent.append(key))
    def _stop(settle):
        core.STOP.set(); return None
    monkeypatch.setattr(ch, "check_empty", _stop)
    assert ch.run(max_hops=3, settle=0) is False and sent == []


@pytest.mark.parametrize("width", [1700, 2100])
def test_templates_follow_the_window_size(width):
    # the game redraws its UI scaled to the window; a resized window must still be recognised
    k = width / _rec_size()[0]
    f = cv2.resize(_frame("confirm"), None, fx=k, fy=k)
    name, p = ch.screen(f)
    _, (x0, y0, _, _), (dx, dy), _ = ch.templates()["confirm"]
    assert name == "confirm"
    assert abs(p[0] - (x0 + dx) * k) <= 3 and abs(p[1] - (y0 + dy) * k) <= 3
