"""Jitter tolerance: the tolerated SJ amplitude must roll off above the CDR BW."""

import numpy as np

from halo_serdes.analysis import jitter_tolerance, jtol_mask
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CdrConfig, CtleConfig, DfeConfig, FfeConfig, RxConfig,
    SimConfig, ClockConfig, TxConfig,
)


def _cfg():
    return LinkConfig(
        modulation="nrz", symbol_rate=28e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.28, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.85, -0.05), fir_n_pre=1, clock=ClockConfig(rj_ui=0.004)),
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


def test_several_thresholds_share_their_runs(monkeypatch):
    """``jitter_tolerance_levels`` on a BER known in closed form: a Gaussian
    margin the tone eats, and a slip above an amplitude at low frequency
    (read 0.5). Each threshold lands within its ``rtol``, the slip caps the
    tolerance, and the runs one search made bracket the next."""
    from scipy.special import erfc

    from halo_serdes.analysis import jitter_tolerance_levels
    from halo_serdes.analysis import jtol as jt

    margin = {1e6: 3.0, 1e8: 0.40, 1e9: 0.25}       # UI the tone may take
    slip = {1e6: 1.5}
    runs = []

    def ber(cfg, f, a, *rest):
        runs.append((f, a))
        if a > slip.get(f, np.inf):
            return 0.5
        return 0.5 * erfc(max(margin[f] - a, 0.0) / 0.02 / np.sqrt(2.0))

    monkeypatch.setattr(jt, "_ber_at", ber)
    thr = (1e-6, 1e-12)
    res = jitter_tolerance_levels(None, list(margin), thr, channel=object(), rtol=0.05)
    for r, t in zip(res, thr):
        z = {1e-6: 4.753, 1e-12: 7.034}[t]           # Q^-1(t)
        want = np.array([1.5, 0.40 - 0.02 * z, 0.25 - 0.02 * z])
        assert r.ber_threshold == t
        assert np.all(r.tol_ui <= want + 1e-9) and np.all(r.tol_ui >= want * 0.95), (t, r.tol_ui)
    assert res[1].tol_ui[1] < res[0].tol_ui[1]
    assert len(runs) == len(set(runs)) and len(runs) <= 30       # 28 on 2026-11-01
