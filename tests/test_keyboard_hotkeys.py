"""The pynput listener is GLOBAL: it hears keys typed in any window. The n/m/F10 hotkeys must only
act while the game is the foreground window -- typing 'n' in a browser/chat was toggling pause."""
import keyboard as kb


class _K:
    def __init__(self, char):
        self.char = char


def test_hotkeys_ignored_when_game_not_focused(monkeypatch):
    monkeypatch.setattr(kb, "pause", False)
    monkeypatch.setattr(kb, "focus_check", lambda: False)
    hits = []
    monkeypatch.setattr(kb, "f9_callback", lambda: hits.append("m"))
    kb.on_press(_K("n")); kb.on_press(_K("m"))
    assert kb.pause is False and hits == []


def test_hotkeys_work_when_game_focused(monkeypatch):
    monkeypatch.setattr(kb, "pause", False)
    monkeypatch.setattr(kb, "focus_check", lambda: True)
    hits = []
    monkeypatch.setattr(kb, "f9_callback", lambda: hits.append("m"))
    kb.on_press(_K("n")); kb.on_press(_K("m"))
    assert kb.pause is True and hits == ["m"]


def test_core_wires_the_focus_gate():
    import core
    assert kb.focus_check is core.is_focused
