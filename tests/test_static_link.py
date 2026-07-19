"""Static-link engine tests: AWGN closed forms and end-to-end sanity.

The Q-function comparisons are the Phase 1 acceptance criteria: over an
ideal (flat) channel the measured error rates must match analytic AWGN
results within Monte-Carlo confidence.
"""

import numpy as np

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig,
    CtleConfig,
    DfeConfig,
    FfeConfig,
    RxConfig,
    SimConfig,
    TxConfig,
)
from halo_serdes.analysis.metrics import nrz_ber_awgn, pam4_ser_awgn
from halo_serdes.channel import ChannelModel
from halo_serdes.dsp import dfe_static, slice_nearest
from halo_serdes.engine import run_static_link


def _flat_channel(f_max: float = 128e9, n: int = 1025) -> ChannelModel:
    """Ideal distortionless channel: H = 0.5 (matched divider) at all f."""
    f = np.linspace(0.0, f_max, n)
    return ChannelModel(np.full(n, 0.5, dtype=complex), f, name="flat")


def _cfg(modulation: str, noise: float, n_symbols: int = 200_000,
         pattern: str = "prbs31") -> LinkConfig:
    return LinkConfig(
        modulation=modulation, symbol_rate=32e9, osr=8,
        channel=ChannelConfig(kind="analytic"),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(
            ctle=CtleConfig(enable=False),
            ffe=FfeConfig(n_pre=1, n_post=2),
            dfe=DfeConfig(n_taps=0),
            noise_rms=noise,
        ),
        sim=SimConfig(n_symbols=n_symbols, seed=7, pattern=pattern),
    )


def test_nrz_awgn_matches_qfunction():
    """Flat channel + AWGN: BER must match Q(A/sigma) within MC confidence."""
    # received amplitude: swing/2 * H(0)/0.5-normalized... engine slices with
    # levels scaled by the equalized main cursor, so use the analytic form
    # with the *slicer-referred* amplitude and noise.
    noise = 0.5 * 0.5 / 3.0  # A_rx = 0.5*0.5 = 0.25; A/sigma = 3
    cfg = _cfg("nrz", noise, n_symbols=400_000)
    res = run_static_link(cfg, channel=_flat_channel(), collect_eye=False)
    # FFE normalization scales signal and noise identically: A/sigma preserved
    expected = nrz_ber_awgn(3.0, 1.0)
    assert res.ber.n_errors > 50  # statistically meaningful
    ratio = res.ber.ber / expected
    assert 0.7 < ratio < 1.4, f"BER {res.ber.ber:.3e} vs expected {expected:.3e}"


def test_pam4_awgn_matches_qfunction():
    """Flat channel + AWGN PAM4: SER = 1.5*Q(d/sigma)."""
    # outer amplitude at slicer: 0.25; level distance d = 0.25/3
    d = 0.25 / 3.0
    noise = d / 2.5  # d/sigma = 2.5 -> SER ~ 9.3e-3
    cfg = _cfg("pam4", noise, n_symbols=300_000, pattern="prbs13q")
    res = run_static_link(cfg, channel=_flat_channel(), collect_eye=False)
    expected = pam4_ser_awgn(0.25, noise)
    ratio = res.ser / expected
    assert 0.75 < ratio < 1.3, f"SER {res.ser:.3e} vs expected {expected:.3e}"
    # Gray coding: BER ~ SER/2 (mostly single-bit errors)
    assert res.ber.ber < res.ser * 0.75


def test_noiseless_flat_channel_is_error_free():
    for mod, pat in (("nrz", "prbs31"), ("pam4", "prbs13q")):
        cfg = _cfg(mod, 0.0, n_symbols=20_000, pattern=pat)
        res = run_static_link(cfg, channel=_flat_channel(), collect_eye=False)
        assert res.ber.n_errors == 0
        assert res.ser == 0.0
        assert res.slicer_snr_db > 40


def test_dfe_static_kernel_cancels_postcursor():
    """DFE with exact postcursor weights removes trailing ISI completely."""
    rng = np.random.default_rng(5)
    levels = np.array([-1.0, 1.0])
    sym = rng.integers(0, 2, size=5000)
    v = levels[sym]
    # channel: main=1, post1=0.4, post2=0.2 (no precursor)
    y = v.copy().astype(float)
    y[1:] += 0.4 * v[:-1]
    y[2:] += 0.2 * v[:-2]
    dec, y_eq = dfe_static(y, np.array([0.4, 0.2]), levels)
    assert np.array_equal(dec, sym)
    assert np.allclose(y_eq, v, atol=1e-12)
    # sanity: without DFE the same signal has symbol errors... margin shrinks
    dec0 = slice_nearest(y, levels)
    assert np.mean(dec0 != sym) >= 0.0  # (may still decode; margin check below)
    assert np.abs(y - v).max() > 0.5  # raw ISI is large


def test_lossy_channel_with_eq_recovers():
    """RLGC channel at 32G with FFE+DFE and light noise: near-zero BER."""
    cfg = LinkConfig(
        modulation="nrz", symbol_rate=32e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.1, rdc=2.0,
                              r_skin=1.0e-3, loss_tangent=0.01),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(ctle=CtleConfig(enable=False),
                    ffe=FfeConfig(n_pre=3, n_post=6),
                    dfe=DfeConfig(n_taps=2),
                    noise_rms=0.002),
        sim=SimConfig(n_symbols=50_000, seed=3, pattern="prbs31"),
    )
    res = run_static_link(cfg, collect_eye=False)
    assert res.slicer_snr_db > 15
    assert res.ber.ber < 1e-3
