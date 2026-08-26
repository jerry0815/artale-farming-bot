"""Tests for the synthetic-data geometry (no game, no sprites needed)."""
import random
import numpy as np
import synth_data


def test_paste_composites_and_bounds():
    bg = np.zeros((100, 100, 3), np.uint8)
    sprite = np.full((10, 10, 3), (0, 0, 255), np.uint8)     # red
    mask = np.full((10, 10), 255, np.uint8)
    assert synth_data.paste(bg, sprite, mask, 20, 30) is True
    assert tuple(bg[35, 25]) == (0, 0, 255)                  # pasted
    assert tuple(bg[0, 0]) == (0, 0, 0)                      # untouched
    # out of bounds -> rejected, no change
    assert synth_data.paste(bg, sprite, mask, 95, 95) is False


def test_paste_respects_mask():
    bg = np.zeros((40, 40, 3), np.uint8)
    sprite = np.full((10, 10, 3), (0, 0, 255), np.uint8)
    mask = np.zeros((10, 10), np.uint8)
    mask[0:5, :] = 255                                       # only top half opaque
    synth_data.paste(bg, sprite, mask, 0, 0)
    assert tuple(bg[2, 2]) == (0, 0, 255)                    # top half painted
    assert tuple(bg[8, 2]) == (0, 0, 0)                      # bottom half untouched


def test_synth_image_labels_match_pastes():
    bg = np.zeros((200, 300, 3), np.uint8)
    cutouts = {0: [(np.full((20, 20, 3), (0, 0, 255), np.uint8), np.full((20, 20), 255, np.uint8))]}
    rng = random.Random(1)
    img, labels = synth_data.synth_image(bg, cutouts, rng, n_range=(3, 3), scale_range=(1.0, 1.0))
    assert len(labels) == 3
    for ci, xc, yc, w, h in labels:
        assert ci == 0
        assert 0 < xc < 1 and 0 < yc < 1                     # normalized center in-frame
        assert abs(w - 20 / 300) < 1e-6 and abs(h - 20 / 200) < 1e-6


def test_generate_writes_dataset(tmp_path, monkeypatch):
    import cv2
    bg_dir = tmp_path / "bg"; bg_dir.mkdir()
    cv2.imwrite(str(bg_dir / "b.jpg"), np.zeros((120, 160, 3), np.uint8))
    # fake sprites so we don't depend on the monster folder
    sp = {0: [(np.full((16, 16, 3), (0, 0, 255), np.uint8), np.full((16, 16), 255, np.uint8))],
          1: [(np.full((12, 12, 3), (0, 255, 0), np.uint8), np.full((12, 12), 255, np.uint8))]}
    monkeypatch.setattr(synth_data, "load_cutouts", lambda base=None: sp)
    out = tmp_path / "ds"
    yaml = synth_data.generate(str(bg_dir), str(out), n=10, val_frac=0.2, seed=0)
    import os
    assert os.path.exists(yaml)
    assert len(list((out / "images" / "train").glob("*.jpg"))) == 8
    assert len(list((out / "images" / "val").glob("*.jpg"))) == 2
    assert len(list((out / "labels" / "train").glob("*.txt"))) == 8
    txt = (out / "data.yaml").read_text()
    assert "nc: 2" in txt and "fishhouse" in txt
