"""Farthest-point diversity selection: pick frames that are maximally different from those
already chosen, so near-duplicate (sequential 8fps) frames are skipped."""
import numpy as np
import pytest

sd = pytest.importorskip("select_diverse_frames")


def test_fps_picks_spread_not_duplicates():
    # three tight clusters of near-duplicates + noise; FPS with n=3 should hit all 3 clusters
    rng = np.random.default_rng(0)
    a = np.zeros((10, 4)) + [0, 0, 0, 0] + rng.normal(0, 0.01, (10, 4))
    b = np.zeros((10, 4)) + [100, 0, 0, 0] + rng.normal(0, 0.01, (10, 4))
    c = np.zeros((10, 4)) + [0, 100, 0, 0] + rng.normal(0, 0.01, (10, 4))
    feats = np.vstack([a, b, c]).astype("float32")
    idx, gaps = sd.farthest_point_select(feats, 3)
    clusters = {i // 10 for i in idx}
    assert clusters == {0, 1, 2}                    # one from each cluster, no duplicates
    assert len(idx) == 3


def test_fps_returns_all_when_n_exceeds():
    feats = np.array([[0, 0], [1, 1], [2, 2]], dtype="float32")
    idx, gaps = sd.farthest_point_select(feats, 10)
    assert sorted(idx) == [0, 1, 2]                 # can't pick more than exist


def test_fps_gaps_are_nonincreasing():
    # each successive pick is no farther than the previous -> gaps monotone non-increasing,
    # which is the "diminishing returns" curve we report to decide if more data is needed
    rng = np.random.default_rng(1)
    feats = rng.normal(0, 1, (60, 8)).astype("float32")
    idx, gaps = sd.farthest_point_select(feats, 20)
    assert all(gaps[i] >= gaps[i + 1] - 1e-6 for i in range(1, len(gaps) - 1))


def test_frame_feature_shape_and_dedup_signal():
    img = (np.random.default_rng(2).integers(0, 255, (200, 300, 3))).astype("uint8")
    f = sd.frame_feature(img, size=16)
    assert f.shape == (16 * 16,) and f.dtype == np.float32
    # a near-identical image (small noise) is much closer than a very different one
    import numpy as _np
    near = img.copy(); near[0, 0, 0] = min(255, int(near[0, 0, 0]) + 1)
    far = (255 - img).astype("uint8")
    d_near = _np.linalg.norm(f - sd.frame_feature(near, 16))
    d_far = _np.linalg.norm(f - sd.frame_feature(far, 16))
    assert d_near < d_far
