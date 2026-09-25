"""EXP-bar crop DPI-offset resolution (see exp_tracking._dpi_offsets).

Regression: the game window grabs a few px larger than the 1920x1080 reference at
150% DPI (window chrome). The old `ow % 10 == 0` heuristic guessed whether to strip
that border -- but a 2px-smaller launch (1940 instead of 1942) made the width divisible
by 10, so the border was NOT stripped, the EXP crop slid onto the 'EXP' label, and the
number+percent OCR fused into one digit run that parse_exp_data could not split -> EXP
tracking silently produced nothing. _dpi_offsets computes the border as the actual overage
above the reference, so a couple px of window jitter no longer flips the crop.
"""
import core  # noqa: F401  -- import first so exp_tracking's core<->exp_tracking cycle resolves
import exp_tracking


def test_dpi_offsets_dpi_bordered_windows():
    # 150% DPI capture, with +/-px launch jitter: border = overage above 1920x1080.
    assert exp_tracking._dpi_offsets(1942, 1136) == (22, 56)   # the historically-working size
    assert exp_tracking._dpi_offsets(1940, 1135) == (20, 55)   # the 2px-smaller launch that broke EXP


def test_dpi_offsets_native_has_no_border():
    assert exp_tracking._dpi_offsets(1920, 1080) == (0, 0)      # exact reference -> nothing to strip


def test_dpi_offsets_ignores_genuinely_different_resolution():
    # A truly larger resolution is scaled proportionally, not treated as a border to strip.
    assert exp_tracking._dpi_offsets(2560, 1440) == (0, 0)


def test_dpi_offsets_never_negative_when_smaller_than_reference():
    assert exp_tracking._dpi_offsets(1918, 1078) == (0, 0)      # slightly under ref -> no negative offset
