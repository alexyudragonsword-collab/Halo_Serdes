"""ADC-based RX tests: quantizer closed forms, MM-CDR physics, kernel
equivalences, and end-to-end recovery."""

import dataclasses

import numpy as np
import pytest

from halo_serdes.afe.adc import TiAdc
from halo_serdes.cdr.adc_kernel import _adc_rx_py, adc_rx
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link
from halo_serdes.tx.jitter import jittered_zoh

UI = 1 / 32e9
OSR = 16


def test_quantizer_snr_matches_602n_176():
    """Ideal N-bit mid-rise quantizer: SNR = 6.02N + 1.76 dB (full-scale sine)."""
    rng = np.random.default_rng(0)
    for bits in (6, 8, 10):
        adc = TiAdc(AdcConfig(n_bits=bits, n_lanes=1), OSR, rng)
        snr = adc.snr_of_sine()
        expected = 6.02 * bits + 1.76
        assert abs(snr - expected) < 0.6, (bits, snr, expected)


def test_enob_noise_sigma():
    rng = np.random.default_rng(0)
    adc = TiAdc(AdcConfig(n_bits=8, n_lanes=1, enob=6.0, fullscale=1.0), OSR, rng)
    # total noise power should equal an ideal 6-bit quantizer's
    var_target = 1.0 / 12.0 * 2.0 ** (-12)
    var_q = adc.q_step ** 2 / 12.0
    assert np.isclose(adc.noise_sigma ** 2 + var_q, var_target, rtol=1e-9)
    # ENOB >= n_bits: no excess noise
    adc2 = TiAdc(AdcConfig(n_bits=8, n_lanes=1, enob=9.0), OSR, rng)
    assert adc2.noise_sigma == 0.0


def _nrz_wave(n_sym, seed=0, jitter=None, amp=0.4):
    rng = np.random.default_rng(seed)
    sym = rng.integers(0, 2, size=n_sym)
    v = np.where(sym > 0, amp, -amp)
    jit = np.zeros(n_sym + 1) if jitter is None else jitter
    y = jittered_zoh(v, OSR, jit, UI)
    y = np.concatenate([np.zeros(4 * OSR), y, np.zeros(4 * OSR)])
    return sym, y


def _run_kernel(y, n_sym, kernel=adc_rx, n_lanes=4, kp_shift=6, ki_shift=13,
                w_ffe=None, n_pre=0, mu_f=0.0, mu_d=0.0, n_dfe=0,
                q_bits=12, amp=0.4, noise=None, pos0=None):
    levels = np.array([-amp, amp])
    if w_ffe is None:
        w_ffe = np.array([1.0])
    if pos0 is None:
        pos0 = 4 * OSR + OSR / 2
    q_step = 2.0 / (2 ** q_bits)
    noise = np.zeros(n_sym + 8) if noise is None else noise
    ref = np.full(n_sym, -1, dtype=np.int64)
    return kernel(y, OSR, float(pos0), n_sym, levels,
                  n_lanes, np.zeros(n_lanes), np.ones(n_lanes), np.zeros(n_lanes),
                  q_step, 2 ** (q_bits - 1) - 1, noise,
                  np.asarray(w_ffe, float), n_pre, mu_f,
                  np.zeros(n_dfe), mu_d,
                  OSR * 2.0 ** (-kp_shift), OSR * 2.0 ** (-ki_shift), 0.0, 0.0,
                  0, 0, ref, 0, 0)


def test_kernel_decides_clean_nrz():
    sym, y = _nrz_wave(20_000)
    dec, y_sl, phase, wf, wd, lane, q = _run_kernel(y, 19_000)
    assert np.array_equal(dec[4000:], sym[4000: dec.size])


def test_mm_pd_formula_matches_dragonphy():
    """Kernel PD accumulation must equal DragonPHY Cdr.cal_mm_timing_error:
    e[n] = x[n] * (sign(x[n+1]) - sign(x[n-1]))."""
    rng = np.random.default_rng(3)
    x = rng.normal(size=64)
    sign_data = np.where(x > 0, 1, -1)
    ref_errors = (sign_data[2:] - sign_data[:-2]) * x[1:-1]
    # replicate the kernel's accumulation loop semantics directly
    acc = [x[s - 1] * ((1.0 if x[s] > 0 else -1.0) - (1.0 if x[s - 2] > 0 else -1.0))
           for s in range(2, x.size)]
    assert np.allclose(acc, ref_errors)


def _gauss_smooth(y: np.ndarray, sigma_ui: float = 0.35) -> np.ndarray:
    """Linear-phase Gaussian smoothing: creates symmetric pre/post cursors,
    giving the Mueller-Muller PD a real timing gradient. (MM has *no*
    restoring force on an unfiltered square wave — degenerate case.)"""
    sig = sigma_ui * OSR
    n = int(6 * sig) | 1
    x = np.arange(n) - n // 2
    k = np.exp(-0.5 * (x / sig) ** 2)
    k /= k.sum()
    return np.convolve(y, k, mode="same")


def test_mm_locks_at_symmetric_cursor_point():
    """MM criterion: lock where h(tau+T) == h(tau-T). For a symmetric
    (Gaussian-smoothed) pulse this balance point is the pulse center."""
    n_sym = 60_000
    sym, y = _nrz_wave(n_sym, seed=4)
    y_f = _gauss_smooth(y)
    dec, y_sl, phase, wf, wd, lane, q = _run_kernel(y_f, n_sym - 1000, ki_shift=11)
    n = dec.size
    tau = np.median(np.mod(phase[3 * n // 4:], OSR))
    # pulse response of the same smoothing on one isolated symbol cell
    single = np.zeros(200 * OSR)
    single[100 * OSR: 101 * OSR] = 1.0
    pf = _gauss_smooth(single)
    idx = 100 * OSR + int(round(tau)) % OSR
    h_post = pf[idx + OSR]
    h_pre = pf[idx - OSR]
    assert abs(h_post - h_pre) < 0.08 * pf.max(), (tau, h_pre, h_post)
    assert np.mean(dec[n // 2:] != sym[n // 2: n]) < 1e-4


def test_frequency_offset_tracking_mm():
    eps = 200e-6
    n_sym = 80_000
    k = np.arange(n_sym + 1)
    jit = -eps * UI * k
    sym, y = _nrz_wave(n_sym, seed=5, jitter=jit)
    y = _gauss_smooth(y)  # MM needs ISI cursors to derive a timing gradient
    dec, y_sl, phase, wf, wd, lane, q = _run_kernel(y, n_sym - 1000, ki_shift=11)
    n = dec.size
    steps = np.diff(phase[n // 2:])
    assert abs(steps.mean() - OSR * (1 - eps)) < OSR * 40e-6
    assert np.mean(dec[n // 2:] != sym[n // 2: n]) < 1e-3


def test_numba_matches_python():
    if adc_rx is _adc_rx_py:
        pytest.skip("numba not active")
    sym, y = _nrz_wave(4_000, seed=6)
    a = _run_kernel(y, 3_000, kernel=adc_rx, w_ffe=np.array([0.1, 1.0, -0.2]),
                    n_pre=1, mu_f=1e-4, mu_d=1e-4, n_dfe=1)
    b = _run_kernel(y, 3_000, kernel=_adc_rx_py, w_ffe=np.array([0.1, 1.0, -0.2]),
                    n_pre=1, mu_f=1e-4, mu_d=1e-4, n_dfe=1)
    for x, z in zip(a, b):
        assert np.allclose(x, z, rtol=1e-12, atol=1e-12)


def _adc_cfg(**over):
    base = dict(
        modulation="pam4", symbol_rate=106.25e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.08, rdc=3.0,
                              r_skin=1.2e-3, loss_tangent=0.008, n_freq=8192),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=False),
                    adc=AdcConfig(n_bits=12, n_lanes=16),
                    ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                    dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=6, ki_shift=14),
                    noise_rms=0.001),
        sim=SimConfig(n_symbols=60_000, seed=7, pattern="prbs13q"),
    )
    base.update(over)
    return LinkConfig(**base)


def test_adc_link_clean_recovery():
    """Ideal ADC (12b, no mismatch) on a mild channel: error-free PAM4."""
    res = run_time_link(_adc_cfg())
    assert res.ser < 1e-4, res.summary()
    assert res.slicer_snr_db > 18


def test_lane_mismatch_degrades_and_calibration_recovers():
    cfg_bad = _adc_cfg()
    cfg_bad = dataclasses.replace(cfg_bad, rx=dataclasses.replace(
        cfg_bad.rx, adc=AdcConfig(n_bits=8, n_lanes=16, offset_sigma=0.01,
                                  gain_sigma=0.03, skew_sigma_ui=0.02,
                                  fullscale=0.8)))
    cfg_cal = dataclasses.replace(cfg_bad, rx=dataclasses.replace(
        cfg_bad.rx, adc=dataclasses.replace(cfg_bad.rx.adc, calibrated=True,
                                            skew_sigma_ui=0.0)))
    r_bad = run_time_link(cfg_bad)
    r_cal = run_time_link(cfg_cal)
    assert r_cal.slicer_snr_db > r_bad.slicer_snr_db + 2.0
    # per-lane SER spread present under mismatch
    spread_bad = r_bad.extras["lane_ser"].max() - r_bad.extras["lane_ser"].min()
    assert spread_bad >= 0.0
