"""Statistical engine tests: closed forms and time-domain cross-validation.

The cross-validation test is the Phase 3 acceptance criterion: on a pure
LTI + AWGN + ideal-DFE configuration, the statistical engine and Monte-Carlo
must agree in the 1e-4..1e-2 overlap region within 2x.
"""

import numpy as np

from halo_serdes.analysis.metrics import nrz_ber_awgn, pam4_ser_awgn, qfunc
from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CtleConfig, DfeConfig, FfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_static_link
from halo_serdes.engine.statistical import gaussian_kernel, isi_pdf, run_statistical


def _flat_channel(f_max=128e9, n=1025):
    f = np.linspace(0.0, f_max, n)
    return ChannelModel(np.full(n, 0.5, dtype=complex), f, name="flat")


def _cfg(mod, noise, **kw):
    return LinkConfig(
        modulation=mod, symbol_rate=32e9, osr=8,
        channel=ChannelConfig(kind="analytic"),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(ctle=CtleConfig(enable=False),
                    ffe=FfeConfig(n_pre=0, n_post=0),
                    dfe=DfeConfig(n_taps=kw.get("n_dfe", 0)),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=kw.get("n_symbols", 100_000), seed=5,
                      pattern=kw.get("pattern", "prbs31")),
    )


def test_pdf_normalization():
    v = np.linspace(-2, 2, 4097)
    pdf = isi_pdf(np.array([0.3, -0.15, 0.07]), np.array([-1.0, 1.0]), v)
    assert np.isclose(pdf.sum(), 1.0, atol=1e-12)
    k = gaussian_kernel(0.05, v[1] - v[0])
    assert np.isclose(k.sum(), 1.0, atol=1e-12)


def test_flat_channel_nrz_matches_q():
    """No ISI: statistical BER must equal Q(A/sigma) to discretization error."""
    # received amplitude 0.25 (swing/2 * 0.5); target BER ~1e-6: A/sigma = 4.75
    sigma = 0.25 / 4.75
    cfg = _cfg("nrz", sigma)
    res = run_statistical(cfg, channel=_flat_channel())
    expected = nrz_ber_awgn(0.25, sigma)
    assert abs(res.ber / expected - 1.0) < 0.05, (res.ber, expected)


def test_flat_channel_pam4_matches_q():
    sigma = (0.25 / 3.0) / 4.0
    cfg = _cfg("pam4", sigma, pattern="prbs13q")
    res = run_statistical(cfg, channel=_flat_channel())
    expected_ser = pam4_ser_awgn(0.25, sigma)
    assert abs(res.ser / expected_ser - 1.0) < 0.05
    # Gray: BER = SER * (1 bit / 2 bits)
    assert abs(res.ber / (expected_ser / 2.0) - 1.0) < 0.05


def _one_post_channel(m_amp: float, c_amp: float, swing: float = 1.0) -> ChannelModel:
    """Synthetic channel whose baud cursors at the slicer are exactly
    [m_amp, c_amp] volts for outer levels (received amp = pulse * swing/2)."""
    f = np.linspace(0, 128e9, 1025)
    ui = 1 / 32e9
    scale = 1.0 / (swing / 2.0)
    H = scale * (m_amp + c_amp * np.exp(-2j * np.pi * f * ui))
    return ChannelModel(H, f, name="1post")


def test_single_postcursor_closed_form():
    """main + one postcursor c: BER = 0.5[Q((m-c)/s) + Q((m+c)/s)] (NRZ)."""
    m_amp, c_amp = 0.25, 0.06
    sigma = 0.05  # (m-c)/sigma = 3.8 -> BER ~7e-5, well above bin/kernel floors
    ch = _one_post_channel(m_amp, c_amp)
    cfg = _cfg("nrz", sigma)
    res = run_statistical(cfg, channel=ch)
    expected = 0.5 * (qfunc((m_amp - c_amp) / sigma) + qfunc((m_amp + c_amp) / sigma))
    assert abs(res.ber / expected - 1.0) < 0.10, (res.ber, expected)


def test_ideal_dfe_removes_postcursor_in_stat():
    """With 1-tap ideal DFE the postcursor must vanish: BER -> Q(m/sigma)."""
    m_amp, c_amp = 0.25, 0.08
    sigma = 0.06  # keeps both configs' BER in the resolvable 1e-5..1e-2 band
    ch = _one_post_channel(m_amp, c_amp)
    cfg0 = _cfg("nrz", sigma, n_dfe=0)
    cfg1 = _cfg("nrz", sigma, n_dfe=1)
    r0 = run_statistical(cfg0, channel=ch)
    r1 = run_statistical(cfg1, channel=ch)
    exp0 = 0.5 * (qfunc((m_amp - c_amp) / sigma) + qfunc((m_amp + c_amp) / sigma))
    exp1 = qfunc(m_amp / sigma)
    assert abs(r0.ber / exp0 - 1.0) < 0.10
    assert abs(r1.ber / exp1 - 1.0) < 0.10
    assert r1.ber < r0.ber


def test_crossover_statistical_vs_monte_carlo():
    """Lossy channel, MC-measurable BER: engines must agree within 2x."""
    cfg = LinkConfig(
        modulation="nrz", symbol_rate=32e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.15, rdc=2.0,
                              r_skin=1.5e-3, loss_tangent=0.01),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(ctle=CtleConfig(enable=False),
                    ffe=FfeConfig(n_pre=2, n_post=6),
                    dfe=DfeConfig(n_taps=2),
                    noise_rms=0.05),
        sim=SimConfig(n_symbols=400_000, seed=6, pattern="prbs31"),
    )
    mc = run_static_link(cfg, collect_eye=False)
    assert 30 < mc.ber.n_errors, f"MC errors too few for comparison: {mc.ber.n_errors}"
    # statistical engine with the same FFE weights the MC engine solved
    stat = run_statistical(cfg, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = stat.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, f"stat {stat.ber:.3e} vs mc {mc.ber.ber:.3e}"


def test_extrapolates_below_mc_floor():
    """Low-noise config: statistical BER reaches < 1e-15 without underflow."""
    sigma = 0.25 / 9.0  # Q(9) ~ 1e-19
    cfg = _cfg("nrz", sigma)
    res = run_statistical(cfg, channel=_flat_channel())
    assert 0.0 <= res.ber < 1e-15
    assert np.isfinite(res.ber_phi).all()
