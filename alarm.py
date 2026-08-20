"""Audible alarm for 'needs a human' screens (lie-check / captcha / curse).

`Alarm` runs a repeating beep on a background thread until stopped.
`AlertController` turns a per-check boolean into start/stop calls with debounce:
it waits for a couple of consecutive detections before sounding (kills the odd
false positive) and silences as soon as the screen is gone.
"""
import threading
import time

try:
    import winsound

    def _default_beep(freq, dur_ms):
        winsound.Beep(freq, dur_ms)
except Exception:  # non-Windows / winsound unavailable
    def _default_beep(freq, dur_ms):
        time.sleep(dur_ms / 1000.0)


class Alarm:
    """Repeating beep on a daemon thread. start()/stop() are idempotent."""

    def __init__(self, freq=1000, beep_ms=350, gap_ms=150, beep=None):
        self.freq = freq
        self.beep_ms = beep_ms
        self.gap_ms = gap_ms
        self._beep = beep or _default_beep
        self._thread = None
        self._stop = threading.Event()

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._beep(self.freq, self.beep_ms)
            except Exception as e:
                print(f"alarm beep 失敗: {e}")
                self._stop.wait(0.5)
            self._stop.wait(self.gap_ms / 1000.0)

    def start(self):
        if self.active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None


class AlertController:
    """Debounce per-check detections into alarm start/stop.

    update(detected) each check. The alarm starts after `trigger_consecutive`
    detections in a row and stops after `clear_consecutive` misses in a row.
    """

    def __init__(self, alarm, trigger_consecutive=2, clear_consecutive=1):
        self.alarm = alarm
        self.trigger_consecutive = trigger_consecutive
        self.clear_consecutive = clear_consecutive
        self._hits = 0
        self._misses = 0

    def update(self, detected):
        if detected:
            self._hits += 1
            self._misses = 0
            if self._hits >= self.trigger_consecutive and not self.alarm.active:
                self.alarm.start()
        else:
            self._misses += 1
            self._hits = 0
            if self._misses >= self.clear_consecutive and self.alarm.active:
                self.alarm.stop()
        return self.alarm.active
