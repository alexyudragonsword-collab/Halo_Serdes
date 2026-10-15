"""A bang-bang detector's data-set output is not white noise to its loop.

ROADMAP P2 #30. PAM4's sign changes between unequal magnitudes (+-1 <->
-+1/3) cross zero away from the lock point, so the Alexander detector's
output on them is all but set by the data: +1 from the larger magnitude to
the smaller, -1 back. The loop model counted that output as white noise of
its full power; consecutive transitions share a symbol and largely cancel,
and on a repeating pattern it sits on lines with nothing at zero frequency.
Counted white, the model's hunting on the PAM4 32G preset read the time
engine's 1.11-1.21x (1.23x on the preset itself); taken through the
pattern's lines, 0.97-1.01x.
"""

import numpy as np
import pytest

from halo_serdes.cdr.linear import LoopParams, bb_data_noise, bb_loop_fixed_point

_PAM4 = np.array([-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0])


def _ideal_mean_output(levels=_PAM4):
    """+1 from the larger magnitude to the smaller across a sign change, -1 back."""
    n = levels.size
    m = np.zeros((n, n))
    for i, a in enumerate(levels):
        for j, b in enumerate(levels):
            if np.sign(a) != np.sign(b) and abs(a) != abs(b):
                m[i, j] = 1.0 if abs(a) > abs(b) else -1.0
    return m


def test_on_random_pam4_half_the_power_reaches_low_frequency():
    """Variance 1/4, lag-1 covariance -1/16: a density of 1/8 at zero
    frequency, against a simulated sequence's block sums."""
    m = _ideal_mean_output()
    counted, seen = bb_data_noise(m)
    assert counted == pytest.approx(0.25)
    assert seen.white_var == pytest.approx(0.125)
    rng = np.random.default_rng(7)
    a = rng.integers(0, 4, 2_000_000)
    g = m[a[:-1], a[1:]]
    block = 256
    sums = g[: g.size // block * block].reshape(-1, block).sum(axis=1)
    assert sums.var() / block == pytest.approx(0.125, rel=0.03)


def test_a_repeating_pattern_puts_it_on_lines():
    """One period's sequence on lines at multiples of the repetition rate:
    their power is its variance (Parseval), nothing at zero frequency."""
    rng = np.random.default_rng(3)
    period = rng.integers(0, 4, 8191)
    m = _ideal_mean_output()
    counted, seen = bb_data_noise(m, period)
    g = m[np.roll(period, 1), period]
    assert seen.white_var == 0.0
    assert seen.line_freq[0] == pytest.approx(1.0 / period.size)
    assert seen.line_freq.max() <= 0.5
    assert float(np.sum(seen.line_power)) == pytest.approx(g.var(), rel=1e-9)
    assert counted == pytest.approx(0.25)


def test_nrz_has_no_data_set_output():
    """Every NRZ sign change crosses at the lock: nothing to reshape, and the
    loop's operating point is the one without it, to the bit."""
    m = np.zeros((2, 2))
    counted, seen = bb_data_noise(m)
    assert counted == 0.0 and seen.white_var == 0.0
    loop = LoopParams(f_update=16e9, kp_ui=2.0 ** -5, ki_ui=2.0 ** -13)
    classes = ((0.5, 0.0, 0.02),)
    a = bb_loop_fixed_point(loop, lambda err_fn: 0.0, transitions=classes)
    b = bb_loop_fixed_point(loop, lambda err_fn: 0.0, transitions=classes,
                            mean_output=lambda s: m, period=np.array([0, 1, 1, 0, 1, 0, 0]))
    assert (a.k_pd, a.sigma_self_ui) == (b.k_pd, b.sigma_self_ui)


def test_the_loop_hunts_less_once_the_data_noise_is_shaped():
    """PAM4's classes, the off-lock pair 0.14 UI out: with the data-set
    output shaped the hunting falls, by about the square root of the noise
    it no longer counts."""
    loop = LoopParams(f_update=16e9, kp_ui=2.0 ** -6, ki_ui=2.0 ** -13)
    classes = ((0.125, 0.0, 0.02), (0.125, 0.0, 0.06), (0.125, 0.14, 0.04), (0.125, -0.14, 0.04))
    m = _ideal_mean_output()
    white = bb_loop_fixed_point(loop, lambda err_fn: 0.0, transitions=classes)
    shaped = bb_loop_fixed_point(loop, lambda err_fn: 0.0, transitions=classes,
                                 mean_output=lambda s: m)
    assert 0.75 < shaped.sigma_self_ui / white.sigma_self_ui < 0.95
