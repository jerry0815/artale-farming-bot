"""Whisper detection: blue/cyan text in the chat bar -> one Discord note per new message."""
import os

import cv2
import numpy as np

import whisper

DATA = os.path.join(os.path.dirname(__file__), "data")


def _load(name):
    im = cv2.imread(os.path.join(DATA, name))
    assert im is not None, name
    return im


def _bar_frame(text=None, color=(216, 248, 0), h=1124, w=1920):
    """Synthetic frame: the dark-gray chat bar sits 125..80 px above the bottom; optional text."""
    f = np.full((h, w, 3), (200, 160, 90), np.uint8)              # sky-ish game scene
    f[h - 125:h - 80, 150:1240] = 62                               # chat bar (neutral gray)
    if text:
        cv2.putText(f, text, (170, h - 92), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
    return f


def test_real_cyan_line_in_bar_is_detected():
    assert whisper.whisper_pixels(_load("whisper_bar_cyan.png")) >= whisper.MIN_PIXELS


def test_real_non_whisper_bars_are_ignored():
    # pink system line, white public chat, cyan water map right above the bar
    for name in ("whisper_none_system.png", "whisper_none_white.png", "whisper_none_water.png"):
        assert whisper.whisper_pixels(_load(name)) < whisper.MIN_PIXELS, name


def test_cyan_scenery_outside_the_bar_is_ignored():
    f = _bar_frame()
    f[f.shape[0] - 220:f.shape[0] - 126, :1200] = (216, 248, 0)    # cyan block just above the bar
    assert whisper.whisper_pixels(f) == 0


def test_white_text_in_bar_is_ignored():
    assert whisper.whisper_pixels(_bar_frame("someone : hello", color=(255, 255, 255))) == 0


def test_watch_fires_once_per_new_message():
    w = whisper.WhisperWatch()
    quiet, a, b = _bar_frame(), _bar_frame("bob : hi there"), _bar_frame("amy : can I join the map?")
    assert w.update(quiet) is False
    assert w.update(a) is True                  # new whisper
    assert w.update(a) is False                 # same line still showing -> no repeat
    assert w.update(b) is True                  # a different cyan line replaced it
    assert w.update(quiet) is False             # a normal message pushed it out
    assert w.update(a) is True                  # the same text arriving again counts as new


def test_watch_ignores_none_frames():
    assert whisper.WhisperWatch().update(None) is False


def test_fast_tick_sends_one_discord_note_per_whisper(monkeypatch):
    import core
    sent = []
    frames = iter([_bar_frame("bob : hi"), _bar_frame("bob : hi"), _bar_frame("bob : hi")])
    monkeypatch.setattr(core, "_lie_enabled", False)
    monkeypatch.setattr(core, "_enemy_dots", lambda f: [])
    monkeypatch.setattr(core, "capture", lambda: next(frames))
    monkeypatch.setattr(core.notify, "send", lambda key, msg: sent.append(key) or True)
    monkeypatch.setattr(core, "_whisper_watch", whisper.WhisperWatch())
    for mode, want in (("notify", ["whisper"]), ("notify", []), ("off", [])):
        sent.clear()
        monkeypatch.setattr(core, "_WHISPER_MODE", [mode])
        monkeypatch.setattr(core, "_last_fast_tick", [0.0])
        core.lie_check_fast_tick()
        assert sent == want, mode


def test_panel_whisper_mode(monkeypatch):
    import core, panel
    from test_panel import _make
    _, c = _make()
    try:
        assert c.set_whisper_mode("off")[0] is True
        assert core._WHISPER_MODE[0] == "off" and panel._status_dict(c)["whisper_mode"] == "off"
        assert c.set_whisper_mode("notify")[0] is True
        assert panel._status_dict(c)["whisper_mode"] == "notify"
        assert c.set_whisper_mode("bogus")[0] is False
    finally:
        core._WHISPER_MODE[0] = "notify"


def test_fast_tick_saves_several_frames_after_a_whisper(monkeypatch, tmp_path):
    """On a whisper the trigger frame + the next ticks' frames are saved (for review/tuning)."""
    import core
    frames = [_bar_frame("bob : hi")] * 8
    it = iter(frames)
    monkeypatch.setattr(core, "_lie_enabled", False)
    monkeypatch.setattr(core, "_enemy_dots", lambda f: [])
    monkeypatch.setattr(core, "capture", lambda: next(it))
    monkeypatch.setattr(core.notify, "send", lambda key, msg: True)
    monkeypatch.setattr(core, "_WHISPER_MODE", ["notify"])
    monkeypatch.setattr(core, "_whisper_watch", whisper.WhisperWatch())
    monkeypatch.setattr(core, "_whisper_dumps_left", [0])
    monkeypatch.setattr(core, "_WHISPER_CAP_DIR", str(tmp_path))
    for _ in range(8):
        monkeypatch.setattr(core, "_last_fast_tick", [0.0])
        core.lie_check_fast_tick()
    saved = sorted(p.name for p in tmp_path.iterdir())
    assert len(saved) == core._WHISPER_DUMP_FRAMES
    assert all("whisper" in n for n in saved)
