"""measure_cast_time.cast_period: finds the repeat interval of a pulsing frame-diff signal."""
import numpy as np
import pytest

from measure_cast_time import cast_period


@pytest.mark.parametrize("period", [0.35, 0.52, 0.8])
def test_finds_the_cast_period_from_jittery_frames(period):
    rng = np.random.default_rng(0)
    ts = np.cumsum(rng.uniform(0.02, 0.05, 200))          # ~30 fps, uneven like real grabs
    sig = 1.0 + 4.0 * (np.mod(ts, period) < 0.08) + rng.normal(0, 0.3, ts.size)
    got, strength = cast_period(ts, sig)
    assert abs(got - period) < 0.03 and strength > 0.3


def test_flat_signal_has_no_rhythm():
    ts = np.linspace(0, 5, 150)
    assert cast_period(ts, np.ones_like(ts)) == (None, 0.0)


def test_prefers_the_fundamental_over_its_double():
    # a pulse every 0.3 s also repeats every 0.6 s; noise can make 0.6 the single highest peak
    rng = np.random.default_rng(3)
    ts = np.cumsum(rng.uniform(0.025, 0.04, 160))
    period = 0.3
    sig = 1.0 + 4.0 * (np.mod(ts, period) < 0.07) + 1.5 * (np.mod(ts, 2 * period) < 0.07) \
        + rng.normal(0, 0.3, ts.size)                      # every 2nd cast a bit brighter
    got, _ = cast_period(ts, sig)
    assert abs(got - period) < 0.03
