"""Auto-escape: click Free Market when an enemy alarm is left un-acknowledged."""
import recovery


class _Alarm:
    def __init__(self, active): self.active = active


def _reset():
    recovery._enemy_alarm_since[0] = None
    recovery._fm_clicked[0] = False


def test_escalates_after_timeout_clicks_once(monkeypatch):
    clicks = []
    monkeypatch.setattr(recovery, "click_free_market", lambda: clicks.append(1))
    t = [1000.0]
    monkeypatch.setattr(recovery.time, "time", lambda: t[0])
    monkeypatch.setattr(recovery, "_enemy_alarm", _Alarm(True))
    recovery._FM_ESCAPE[0] = True
    recovery._FM_AFTER[0] = 20.0
    _reset()

    recovery._maybe_escalate_to_free_market()                 # first sight -> arm the timer
    assert clicks == [] and recovery._enemy_alarm_since[0] == 1000.0
    t[0] = 1015.0
    recovery._maybe_escalate_to_free_market()                 # 15s < 20 -> wait
    assert clicks == []
    t[0] = 1021.0
    recovery._maybe_escalate_to_free_market()                 # 21s >= 20 -> click once
    assert clicks == [1]
    t[0] = 1030.0
    recovery._maybe_escalate_to_free_market()                 # already clicked -> no repeat
    assert clicks == [1]


def test_silencing_before_timeout_cancels(monkeypatch):
    clicks = []
    monkeypatch.setattr(recovery, "click_free_market", lambda: clicks.append(1))
    t = [500.0]
    monkeypatch.setattr(recovery.time, "time", lambda: t[0])
    alarm = _Alarm(True)
    monkeypatch.setattr(recovery, "_enemy_alarm", alarm)
    recovery._FM_ESCAPE[0] = True
    recovery._FM_AFTER[0] = 20.0
    _reset()

    recovery._maybe_escalate_to_free_market()                 # arm
    t[0] = 510.0
    alarm.active = False                                      # user pressed 'm' -> alarm off
    recovery._maybe_escalate_to_free_market()                 # -> reset, no click
    assert clicks == [] and recovery._enemy_alarm_since[0] is None and recovery._fm_clicked[0] is False
    t[0] = 999.0                                              # even long after: still no click
    recovery._maybe_escalate_to_free_market()
    assert clicks == []


def test_disabled_never_clicks(monkeypatch):
    clicks = []
    monkeypatch.setattr(recovery, "click_free_market", lambda: clicks.append(1))
    monkeypatch.setattr(recovery.time, "time", lambda: 5000.0)
    monkeypatch.setattr(recovery, "_enemy_alarm", _Alarm(True))
    recovery._FM_ESCAPE[0] = False
    recovery._enemy_alarm_since[0] = 1.0                       # even with an old arm time
    recovery._fm_clicked[0] = False
    for _ in range(3):
        recovery._maybe_escalate_to_free_market()
    assert clicks == []


def test_click_free_market_uses_window_box_offset_then_silences(monkeypatch):
    # screen click point = window box origin + the button's frame offset (survives window moves),
    # and after the click the alarm is silenced (the escape is done -> no human ack needed).
    monkeypatch.setattr(recovery, "_window_box", lambda: {"left": 61, "top": 935, "width": 1942, "height": 1136})
    monkeypatch.setattr(recovery, "focus", lambda: True)
    monkeypatch.setattr(recovery.notify, "send", lambda *a, **k: None)
    recovery._FM_XY[0] = (1452, 1092)
    got, silenced = [], []
    monkeypatch.setattr(recovery, "_click_screen", lambda x, y: got.append((x, y)))
    monkeypatch.setattr(recovery, "enemy_alarm_silence", lambda: silenced.append(1))
    recovery.click_free_market()
    assert got == [(61 + 1452, 935 + 1092)]                   # (1513, 2027)
    assert silenced == [1]                                    # alarm auto-silenced after escaping
