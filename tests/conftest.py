"""Test-run side-effect guards (applied to every test automatically).

The alarm and Discord code paths are exercised by tests (lie-check, another player, Free Market).
Without these guards a test run BEEPS through the speakers (alarm.Alarm -> winsound.Beep on a
background thread) and can post REAL messages to the Discord webhook in notify.json via the shared
notify.send() singleton. Tests that check the alarm/notifier inject their own beep/poster and are
unaffected.
"""
import pytest

try:
    import winsound
    winsound.Beep = lambda freq, dur_ms: None          # silence every alarm (module-level: before
except Exception:                                     # alarm.py's threads ever call it)
    pass


@pytest.fixture(autouse=True)
def _no_real_discord(monkeypatch):
    """The shared notifier never posts during tests (a disabled instance -> silent no-op)."""
    import notify
    monkeypatch.setattr(notify, "_singleton", notify.DiscordNotifier(url=""))
