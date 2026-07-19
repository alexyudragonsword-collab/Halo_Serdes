"""FFE solver tests against hand-computed / closed-form results."""

import numpy as np

from halo_serdes.core.waveform import Waveform
from halo_serdes.dsp import apply_ffe, channel_cursors, mmse_ffe, zf_ffe
from halo_serdes.dsp.ffe import equalized_cursors


def test_zf_3tap_hand_example():
    """Channel cursors [0.2, 1.0, 0.4] (1 pre, 1 post), 3-tap FFE with 1 pre:
    the ZF solve must null the covered pre/post cursors exactly."""
    c = np.array([0.2, 1.0, 0.4])
    w = zf_ffe(c, c_pre=1, n_taps=3, tap_pre=1, normalize=False)
    eq, eq_pre = equalized_cursors(c, w, 1, 1)
    # positions -1, 0, +1 relative to main: pre/post nulled, main = 1
    assert np.isclose(eq[eq_pre - 1], 0.0, atol=1e-12)
    assert np.isclose(eq[eq_pre + 1], 0.0, atol=1e-12)
    assert np.isclose(eq[eq_pre], 1.0, atol=1e-12)
    # hand check: A = [[1, .2, 0], [.4, 1, .2], [0, .4, 1]], A w = [0,1,0]
    A = np.array([[1.0, 0.2, 0.0], [0.4, 1.0, 0.2], [0.0, 0.4, 1.0]])
    assert np.allclose(A @ w, [0.0, 1.0, 0.0], atol=1e-12)


def test_mmse_reduces_to_zf_at_zero_noise():
    c = np.array([0.05, 0.2, 1.0, 0.4, 0.1])
    n_taps, tap_pre = 9, 4
    w_mmse = mmse_ffe(c, 2, n_taps, tap_pre, noise_var=0.0, normalize=False)
    # zero-noise MMSE = least squares; residual ISI must be tiny with enough taps
    eq, eq_pre = equalized_cursors(c, w_mmse, 2, tap_pre)
    isi = np.abs(eq).sum() - abs(eq[eq_pre])
    assert isi < 0.05 * abs(eq[eq_pre])


def test_mmse_regularization_shrinks_taps():
    c = np.array([0.05, 0.2, 1.0, 0.4, 0.1])
    w0 = mmse_ffe(c, 2, 7, 3, noise_var=0.0, normalize=False)
    w1 = mmse_ffe(c, 2, 7, 3, noise_var=0.5, normalize=False)
    assert np.abs(w1).sum() < np.abs(w0).sum()


def test_apply_ffe_alignment():
    """A pure-delay 'channel' with an identity FFE must keep symbol alignment."""
    rng = np.random.default_rng(4)
    y = rng.normal(size=100)
    w = np.zeros(5)
    w[2] = 1.0  # identity with tap_pre = 2
    out = apply_ffe(y, w, tap_pre=2)
    assert np.allclose(out, y)


def test_channel_cursors_extraction():
    osr = 8
    y = np.zeros(40 * osr)
    peak = 20 * osr
    y[peak] = 1.0
    y[peak - osr] = 0.25
    y[peak + osr] = 0.5
    p = Waveform(y, 1e-12)
    c = channel_cursors(p, osr, 2, 3)
    assert np.allclose(c, [0.0, 0.25, 1.0, 0.5, 0.0, 0.0])
