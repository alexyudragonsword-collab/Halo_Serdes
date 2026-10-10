"""fec.reach / fec.refine: reach read where the pre-FEC BER meets the FEC's
threshold, never from a point with no errors, and -- refined -- the same
whatever grid the sweep started on."""

import numpy as np
import pytest

from halo_serdes.fec import (
    concatenated_post_fec_ber,
    fec_threshold,
    pre_to_post_fec_ber,
    reach,
    refine,
)


def test_thresholds_put_the_post_fec_ber_at_the_target():
    kp4, bch = fec_threshold("kp4"), fec_threshold("kp4", inner=(255, 5))
    assert kp4 == pytest.approx(2.19e-4, rel=0.01)
    assert bch == pytest.approx(6.86e-3, rel=0.01)
    assert pre_to_post_fec_ber(kp4, "kp4") == pytest.approx(1e-15, rel=0.01)
    assert concatenated_post_fec_ber(bch, 255, 5) == pytest.approx(1e-15, rel=0.01)
    assert fec_threshold("kr4") < kp4                      # the weaker code tolerates less


def test_reach_interpolates_log_ber_between_the_bracketing_points():
    r = reach([30.0, 31.0, 32.0], [1e-5, 1e-4, 1e-3], 10 ** -3.5)
    assert r.value == pytest.approx(31.5) and (r.lo, r.hi) == (31.0, 32.0)
    assert reach([30.0, 31.0], [1e-3, 1e-2], 1e-4).note == "below sweep"
    assert reach([30.0, 31.0], [1e-6, 1e-5], 1e-4).note == "beyond sweep"
    # no errors just below the crossing: the BER there is only bounded, and a
    # stand-in for it (1e-9, half a count) would decide the answer
    r = reach([30.3, 33.3], [0.0, 4.25e-4], 2.19e-4)
    assert r.value is None and r.note == "no errors at lo" and (r.lo, r.hi) == (30.3, 33.3)


def _curved(x):
    """A log BER that bends (as a real one does): 3.2 decades per dB at 30 dB,
    0.8 at 34."""
    return 10.0 ** (-6.0 + 3.2 * (x - 30.0) - 0.3 * (x - 30.0) ** 2)


def test_refined_reach_does_not_depend_on_the_grid():
    thr = 2.19e-4
    truth = next(x for x in np.arange(30.0, 36.0, 1e-4) if _curved(x) > thr)
    plain, refined = [], []
    for grid in ([29.0, 32.0, 35.0], [29.0, 30.5, 32.0, 33.5, 35.0], [28.0, 31.0, 34.0]):
        plain.append(reach(grid, [_curved(x) for x in grid], thr).value)
        pts = {x: (x, _curved(x)) for x in grid}
        r = refine(pts, lambda x: (x, _curved(x)), thr, tol=0.1)
        assert r.hi - r.lo <= 0.1 and len(pts) > len(grid)      # the new points are kept
        refined.append(r.value)
    assert max(plain) - min(plain) > 0.15                       # a coarse grid moves it
    assert max(refined) - min(refined) < 0.02
    assert refined[0] == pytest.approx(truth, abs=0.02)


def test_refine_bisects_the_swept_parameter_and_resolves_a_no_error_point():
    # the parameter is a length, x the loss it maps to; the coarse grid goes
    # from no errors straight past the threshold, as example 21's did
    def measure(L):
        x = 120.0 * L + 6.0
        return x, (0.0 if x < 30.5 else _curved(x))
    pts = {L: measure(L) for L in (0.20, 0.225, 0.25)}
    assert reach(*zip(*sorted(pts.values())), 2.19e-4).note == "no errors at lo"
    r = refine(pts, measure, 2.19e-4, tol=0.4)
    assert r.value is not None and r.hi - r.lo <= 0.4
    assert all(0.20 <= L <= 0.25 for L in pts)
    # a budget it cannot meet stops at max_runs
    n = len(pts)
    refine(pts, measure, 2.19e-4, tol=1e-6, max_runs=2)
    assert len(pts) == n + 2
