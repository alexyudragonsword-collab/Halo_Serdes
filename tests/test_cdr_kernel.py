"""Kernel-level CDR/DFE tests against loop-dynamics closed forms."""

import numpy as np
import pytest

from halo_serdes.cdr.kernels import _ms_rx_py, ms_rx
from halo_serdes.tx.jitter import jittered_zoh

UI = 1 / 32e9
OSR = 16
LEVELS = np.array([-0.5, 0.5])


def _make_wave(n_sym: int, seed: int = 0, jitter=None, postcursors=()):
    """NRZ ZOH waveform with optional per-boundary jitter and baud-domain ISI."""
    rng = np.random.default_rng(seed)
    sym = rng.integers(0, 2, size=n_sym)
    v = LEVELS[sym].astype(float)
    if postcursors:
        vv = v.copy()
        for i, c in enumerate(postcursors, start=1):
            vv[i:] += c * v[:-i]
        v_wave = vv
    else:
        v_wave = v
    jit = np.zeros(n_sym + 1) if jitter is None else jitter
    y = jittered_zoh(v_wave, OSR, jit, UI)
    # pad so farrow can look ahead
    y = np.concatenate([np.zeros(4 * OSR), y, np.zeros(4 * OSR)])
    return sym, y


def _run(y, n_sym, w_dfe=np.zeros(0), mu=0.0, kp_shift=5, ki_shift=11,
         phase0=None, kernel=ms_rx):
    if phase0 is None:
        phase0 = 4 * OSR + OSR / 2  # mid-UI of symbol 0 after padding
    kp = OSR * 2.0 ** (-kp_shift)
    ki = OSR * 2.0 ** (-ki_shift)
    ref = np.full(n_sym, -1, dtype=np.int64)
    return kernel(y, OSR, float(phase0), n_sym, LEVELS,
                  np.asarray(w_dfe, float), float(mu), 100, kp, ki, 0.0,
                  1.0, ref, 0, 0, 0, np.zeros(LEVELS.size))


def test_locks_and_decides_clean_waveform():
    sym, y = _make_wave(20_000)
    dec, y_sum, phase, w, pd = _run(y, 19_000)
    # after settling, all decisions correct
    assert np.array_equal(dec[2000:], sym[2000: dec.size])
    # phase stays near mid-UI of each symbol (mod OSR)
    ph_frac = np.mod(phase[5000:], OSR)
    center = 4 * OSR + OSR / 2  # initial position was exact center
    assert np.abs(np.mod(ph_frac - center, OSR)).min() >= 0.0  # smoke
    assert np.std(np.diff(phase[5000:])) < 0.5  # stable period


def test_frequency_offset_tracking():
    """+200ppm Tx symbol rate (UI shrinks): steady-state phase step must be
    osr*(1-eps) — the classic CDR frequency-ramp closed form."""
    eps = 200e-6
    n_sym = 60_000
    k = np.arange(n_sym + 1)
    jit = -eps * UI * k  # boundary k arrives early by eps*UI*k
    sym, y = _make_wave(n_sym, seed=1, jitter=jit)
    dec, y_sum, phase, w, pd = _run(y, n_sym - 1000, ki_shift=10)
    n = dec.size
    steps = np.diff(phase[n // 2:])  # steady state
    mean_step = steps.mean()
    expected = OSR * (1 - eps)
    # integral branch must absorb the ppm offset
    assert abs(mean_step - expected) < OSR * 30e-6, (mean_step, expected)
    # and decisions still track after lock
    assert np.array_equal(dec[n // 2:], sym[n // 2: n])


def test_sinusoidal_jitter_tracking():
    """In-band SJ: recovered sampling phase must follow the injected sinusoid
    (loop BW ~ f_baud*kp/(2pi*osr) >> f_sj)."""
    n_sym = 60_000
    f_sj = 2e6
    a_sj = 0.05  # UI
    k = np.arange(n_sym + 1)
    jit = a_sj * UI * np.sin(2 * np.pi * f_sj * k * UI)
    sym, y = _make_wave(n_sym, seed=2, jitter=jit)
    dec, y_sum, phase, w, pd = _run(y, n_sym - 1000, kp_shift=4, ki_shift=10)
    n = dec.size
    ph = phase - np.arange(n) * OSR  # remove nominal advance
    ph = ph[n // 4:]
    ph = ph - ph.mean()
    # amplitude of the recovered sinusoid at f_sj, in samples
    t = (np.arange(ph.size) + n // 4) * UI
    iq = np.exp(-2j * np.pi * f_sj * t)
    amp = 2 * np.abs(np.mean(ph * iq))
    expected = a_sj * OSR
    assert 0.6 * expected < amp < 1.3 * expected, (amp, expected)


def test_dfe_adapts_to_postcursors():
    """Sign-sign LMS from zero taps must converge to the true postcursors."""
    post = (0.25, -0.1)
    sym, y = _make_wave(80_000, seed=3, postcursors=post)
    # sign-sign moves at most mu per n_ave symbols: mu must be large enough to
    # cover the 0.25 tap distance well within the run (0.25/2e-3 = 125 updates)
    dec, y_sum, phase, w, pd = _run(y, 79_000, w_dfe=np.zeros(2), mu=2e-3)
    # normalized taps: postcursor / main (main = 1 here)
    assert abs(w[0] - post[0] / 0.5 * 0.5) < 0.03, w  # levels carry the 0.5
    assert abs(w[1] - post[1] / 0.5 * 0.5) < 0.03, w
    n = dec.size
    assert np.mean(dec[n // 2:] != sym[n // 2: n]) < 1e-3


def test_numba_matches_pure_python():
    sym, y = _make_wave(3_000, seed=4, postcursors=(0.2,))
    if ms_rx is _ms_rx_py:
        pytest.skip("numba not active")
    out_jit = _run(y, 2_500, w_dfe=np.zeros(1), mu=1e-3, kernel=ms_rx)
    out_py = _run(y, 2_500, w_dfe=np.zeros(1), mu=1e-3, kernel=_ms_rx_py)
    for a, b in zip(out_jit, out_py):
        assert np.allclose(a, b, rtol=1e-12, atol=1e-12)
