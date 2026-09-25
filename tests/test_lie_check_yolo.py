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


def _fake_model(monkeypatch, boxes):
    ly.reset_cache()

    class _B:
        def __init__(self, cls, conf, xyxy):
            self.cls = [cls]; self.conf = [conf]; self.xyxy = [xyxy]

    class _R:
        pass
    _R.boxes = [_B(*b) for b in boxes]

    class _M:
        def predict(self, *a, **k):
            return [_R()]
    monkeypatch.setattr(ly, "_load", lambda path=ly._MODEL_PATH: _M())


def test_narrow_monster_box_is_a_building_not_the_popup(monkeypatch):
    # flat_sweep false alarms: tall skyscrapers boxed as monster_check at 0.84-0.97, 5-12% of the
    # frame wide; real monster popups are >= 18% wide (111 labels, median 23%).
    _fake_model(monkeypatch, [(0, 0.97, (620.0, 299.0, 727.0, 616.0)),       # 5.5% wide
                              (0, 0.92, (10.0, 344.0, 248.0, 844.0))])       # 12.3% wide
    assert ly.detect_lie_check_yolo(np.zeros((1135, 1940, 3), np.uint8)) == []


def test_real_width_monster_popup_still_alarms(monkeypatch):
    _fake_model(monkeypatch, [(0, 0.98, (700.0, 250.0, 1150.0, 760.0))])    # 23% wide
    out = ly.detect_lie_check_yolo(np.zeros((1135, 1940, 3), np.uint8))
    assert [c for c, _, _ in out] == ["monster_check"]


def test_width_guard_only_applies_to_monster_check(monkeypatch):
    _fake_model(monkeypatch, [(1, 0.95, (100.0, 100.0, 200.0, 200.0))])     # narrow curse box
    out = ly.detect_lie_check_yolo(np.zeros((1135, 1940, 3), np.uint8))
    assert [c for c, _, _ in out] == ["curse"]
