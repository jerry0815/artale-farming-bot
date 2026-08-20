import time
import threading
import alarm as alarm_mod


def _silent_alarm():
    beeps = []
    a = alarm_mod.Alarm(beep_ms=1, gap_ms=1, beep=lambda f, d: beeps.append((f, d)))
    return a, beeps


def test_alarm_start_stop_beeps_then_silences():
    a, beeps = _silent_alarm()
    assert not a.active
    a.start()
    assert a.active
    time.sleep(0.05)
    a.stop()
    assert not a.active
    assert len(beeps) > 0
    count = len(beeps)
    time.sleep(0.03)
    assert len(beeps) == count  # no more beeps after stop
    assert threading.active_count() >= 1


def test_alarm_start_is_idempotent():
    a, _ = _silent_alarm()
    a.start()
    t1 = a._thread
    a.start()
    assert a._thread is t1  # no second thread
    a.stop()


def test_controller_requires_two_consecutive_to_start():
    a, _ = _silent_alarm()
    c = alarm_mod.AlertController(a, trigger_consecutive=2)
    c.update(True)
    assert not a.active          # one hit is not enough
    c.update(True)
    assert a.active              # second consecutive hit arms it
    c.update(False)
    assert not a.active          # cleared on miss
    a.stop()


def test_controller_single_false_positive_never_sounds():
    a, _ = _silent_alarm()
    c = alarm_mod.AlertController(a, trigger_consecutive=2)
    c.update(True)
    c.update(False)   # broke the streak
    c.update(True)
    assert not a.active
    a.stop()
