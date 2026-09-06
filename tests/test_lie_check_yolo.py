"""Tests for the YOLO lie-check inference wrapper (no model needed -- uses stubs)."""
import numpy as np
import lie_check_yolo as ly


def test_returns_empty_when_model_absent(tmp_path):
    ly.reset_cache()
    frame = np.zeros((100, 100, 3), np.uint8)
    assert ly.detect_lie_check_yolo(frame, model_path=str(tmp_path / "nope.pt")) == []


def test_returns_empty_on_bad_frame():
    ly.reset_cache()
    assert ly.detect_lie_check_yolo(None) == []


def test_parses_model_output(monkeypatch):
    ly.reset_cache()

    class _B:
        def __init__(self, cls, conf, xyxy):
            self.cls = [cls]; self.conf = [conf]; self.xyxy = [xyxy]

    class _R:
        boxes = [_B(0, 0.9, (10.0, 20.0, 110.0, 80.0))]

    class _M:
        def predict(self, *a, **k):
            return [_R()]

    monkeypatch.setattr(ly, "_load", lambda path=ly._MODEL_PATH: _M())
    frame = np.zeros((100, 100, 3), np.uint8)
    out = ly.detect_lie_check_yolo(frame)
    assert out == [("monster_check", 0.9, (10.0, 20.0, 110.0, 80.0))]


def test_returns_empty_on_predict_exception(monkeypatch):
    ly.reset_cache()

    class _M:
        def predict(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(ly, "_load", lambda path=ly._MODEL_PATH: _M())
    assert ly.detect_lie_check_yolo(np.zeros((10, 10, 3), np.uint8)) == []
