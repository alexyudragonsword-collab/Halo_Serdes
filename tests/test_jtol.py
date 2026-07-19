"""Jitter tolerance: the tolerated SJ amplitude must roll off above the CDR BW."""

import numpy as np

from halo_serdes.analysis import jitter_tolerance, jtol_mask
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CdrConfig, CtleConfig, DfeConfig, FfeConfig, RxConfig,
    SimConfig, TxConfig,
)


def _cfg():
    return LinkConfig(
        modulation="nrz", symbol_rate=28e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.28, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.85, -0.05), fir_n_pre=1,
                    rj_ui=0.004),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                    ffe=FfeConfig(n_pre=4, n_post=12), dfe=DfeConfig(n_taps=2),
                    cdr=CdrConfig(kind="bang_bang", kp_shift=5, ki_shift=12),
                    noise_rms=0.003),
        sim=SimConfig(n_symbols=100000, seed=4, pattern="prbs13"))


def test_jtol_rolls_off_at_high_frequency():
    cfg = _cfg()
    freqs = np.array([5e6, 1e8, 4e8])   # below BW, near, well above
    jt = jitter_tolerance(cfg, freqs, ber_threshold=1e-3, amp_lo=0.05,
                          amp_hi=4.0, iters=5, n_symbols=15000)
    assert jt.tol_ui.shape == (3,)
    assert np.all(np.isfinite(jt.tol_ui))
    # low-frequency SJ is tracked (large tolerance); high-frequency is not
    assert jt.tol_ui[0] > jt.tol_ui[-1]
    assert jt.tol_ui[-1] < 1.0            # high-f tolerance ~ eye half-width
    assert "JTOL" in jt.summary()


def test_jtol_mask_shape():
    f = np.logspace(5, 9, 24)             # up to 1 GHz so the floor engages
    m = jtol_mask(f, lf_max_ui=5.0, f_corner=4e6, hf_floor_ui=0.1)
    assert m[0] == 5.0                    # flat max at low frequency
    assert np.isclose(m[-1], 0.1)         # floor at high frequency
    assert np.all(np.diff(m) <= 1e-9)     # monotone non-increasing
