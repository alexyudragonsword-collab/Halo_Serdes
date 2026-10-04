"""Statistical engine tests: closed forms and time-domain cross-validation.

The cross-validation test is the Phase 3 acceptance criterion: on a pure
LTI + AWGN + ideal-DFE configuration, the statistical engine and Monte-Carlo
must agree in the 1e-4..1e-2 overlap region within 2x.
"""

import numpy as np
import pytest

from halo_serdes.analysis.metrics import nrz_ber_awgn, pam4_ser_awgn, qfunc
from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CtleConfig, DfeConfig, FfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_static_link, run_time_link
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


def test_equal_level_kernels_reproduce_the_single_kernel_engine():
    """``level_sigma`` of zeros makes every level's kernel the receiver's
    own: the per-level path must then be bit-identical to the electrical
    engine -- that is what keeps the electrical results frozen while the
    optical topology shares the loop."""
    ch = _one_post_channel(0.25, 0.06)
    for mod, pattern in (("nrz", "prbs31"), ("pam4", "prbs13q")):
        cfg = _cfg(mod, 0.03, pattern=pattern)
        n_levels = 2 if mod == "nrz" else 4
        a = run_statistical(cfg, channel=ch)
        b = run_statistical(cfg, channel=ch, level_sigma=np.zeros(n_levels))
        assert np.array_equal(a.ber_phi, b.ber_phi)
        assert np.array_equal(a.ser_phi, b.ser_phi)
        assert np.array_equal(a.eye_pdf, b.eye_pdf)
        assert a.best_phi == b.best_phi and a.ber == b.ber


def test_level_dependent_noise_hits_the_noisier_level():
    """PAM4 with noise only on the top level: SER must be the top level's
    share of an AWGN SER at that sigma plus nothing from the quiet levels
    (one neighbour threshold, 1/4 of the symbols) -- and not change when the
    same sigma is moved to the bottom level."""
    sigma = (0.25 / 3.0) / 4.0
    cfg = _cfg("pam4", 0.0, pattern="prbs13q")
    top = run_statistical(cfg, channel=_flat_channel(),
                          level_sigma=np.array([0.0, 0.0, 0.0, sigma]))
    bottom = run_statistical(cfg, channel=_flat_channel(),
                             level_sigma=np.array([sigma, 0.0, 0.0, 0.0]))
    expected = 0.25 * qfunc((0.25 / 3.0) / sigma)
    assert abs(top.ser / expected - 1.0) < 0.10, (top.ser, expected)
    # the upper and lower tails are read off the grid with opposite half-bin
    # bias (searchsorted on each side), so the two differ at the bin level
    assert abs(bottom.ser / top.ser - 1.0) < 0.10


@pytest.mark.parametrize("length_m,noise", [(0.15, 0.03), (0.25, 0.02), (0.35, 0.012)])
def test_dac_quantisation_cross_check_within_2x(length_m, noise):
    """Invariant #3 with only the Tx DAC added: the statistical engine's
    noise_rms (+) sigma_q against the static engine's quantised waveform, at
    three channel losses. A 4-bit DAC so the quantisation visibly moves BER."""
    import dataclasses

    cfg = LinkConfig(
        modulation="pam4", symbol_rate=32e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=2.0,
                              r_skin=1.5e-3, loss_tangent=0.01),
        tx=TxConfig(swing=1.0, dac_bits=4),
        rx=RxConfig(ctle=CtleConfig(enable=False), ffe=FfeConfig(n_pre=2, n_post=6),
                    dfe=DfeConfig(n_taps=2), noise_rms=noise),
        sim=SimConfig(n_symbols=300_000, seed=6, pattern="prbs13q"))
    mc = run_static_link(cfg, collect_eye=False)
    stat = run_statistical(cfg, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = stat.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (length_m, stat.ber, mc.ber.ber, ratio)
    ideal = run_static_link(dataclasses.replace(cfg, tx=TxConfig(swing=1.0)), collect_eye=False)
    assert mc.ber.ber > 1.2 * ideal.ber.ber          # the DAC is not a bystander here


@pytest.mark.parametrize("length_m,noise", [(0.15, 0.012), (0.25, 0.012), (0.35, 0.012)])
@pytest.mark.parametrize("dac_bits", [None, 5])
def test_tx_ffe_cross_check_within_2x(length_m, noise, dac_bits):
    """Invariant #3 with a strong Tx FFE: the static engine's receiver solves
    for the pulse it actually sees (TxPipeline.receiver_view). Before that, it
    equalised the channel without the Tx FFE and this configuration read
    5e-2 against a statistical 1e-11 (ROADMAP P1 1c)."""
    cfg = LinkConfig(
        modulation="pam4", symbol_rate=32e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=2.0,
                              r_skin=1.5e-3, loss_tangent=0.01),
        tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.78, -0.14), fir_n_pre=1, dac_bits=dac_bits),
        rx=RxConfig(ctle=CtleConfig(enable=False), ffe=FfeConfig(n_pre=2, n_post=6),
                    dfe=DfeConfig(n_taps=2), noise_rms=noise),
        sim=SimConfig(n_symbols=300_000, seed=6, pattern="prbs13q"))
    mc = run_static_link(cfg, collect_eye=False)
    assert mc.ber.n_errors > 50, mc.ber.n_errors
    stat = run_statistical(cfg, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = stat.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (length_m, dac_bits, stat.ber, mc.ber.ber, ratio)


@pytest.mark.parametrize("mod,pattern,noise", [("nrz", "prbs13", 0.03), ("pam4", "prbs13q", 0.012)])
def test_tx_driver_pole_cross_check_within_2x(mod, pattern, noise):
    """Invariant #3 with the Tx driver's single pole at half the baud rate.
    The statistical engine used to leave ``tx.bw`` out entirely, and the
    receivers solved their starting FFE, phase and slicer scale from a pulse
    without it: 2e-2 / 0.15 time-domain BER against an unchanged statistical
    5e-5 / 5e-4 (ratio 0.003)."""
    cfg = LinkConfig(
        modulation=mod, symbol_rate=32e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.25, rdc=2.0,
                              r_skin=1.5e-3, loss_tangent=0.01),
        tx=TxConfig(swing=1.0, bw=16e9),
        rx=RxConfig(ctle=CtleConfig(enable=False), ffe=FfeConfig(n_pre=2, n_post=6),
                    dfe=DfeConfig(n_taps=2), noise_rms=noise),
        sim=SimConfig(n_symbols=300_000, seed=6, pattern=pattern))
    mc = run_static_link(cfg, collect_eye=False)
    assert mc.ber.n_errors > 50, mc.ber.n_errors
    stat = run_statistical(cfg, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = stat.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (mod, stat.ber, mc.ber.ber, ratio)


@pytest.mark.parametrize("mod,length_m,noise,n_dfe", [
    ("pam4", 0.3, 0.009, 2), ("pam4", 0.3, 0.012, 2), ("pam4", 0.4, 0.016, 2),
    ("pam4", 0.3, 0.012, 0), ("nrz", 0.4, 0.045, 2), ("nrz", 0.15, 0.06, 2)])
def test_mixed_signal_cross_check_within_2x(mod, length_m, noise, n_dfe):
    """Invariant #3 on the mixed-signal receiver, PAM4 included. The bang-bang
    CDR locks where its edge samples balance (near the pulse peak), not at
    the bathtub minimum, and with a DFE the two are 0.2 UI apart: reading the
    statistical BER at the minimum made it 0.19-0.70 of the time engine's
    (0.12 with the CDR frozen at the peak), and DFE error propagation was
    not the cause (frozen loop, BER read at the peak: 0.92-1.0).

    The 0.15 m NRZ case locks 0.2 UI late, between samples: with per-sample
    white noise the interpolating sampler saw only ~0.87 sigma there and the
    time engine read 2.3x below the statistical one (engine/lti.receiver_awgn)."""
    cfg = LinkConfig(
        modulation=mod, symbol_rate=16e9, osr=32,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=2.0,
                              r_skin=1.5e-3, loss_tangent=0.01),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=False),
                    dfe=DfeConfig(n_taps=n_dfe), noise_rms=noise),
        sim=SimConfig(n_symbols=400_000, seed=3, pattern="prbs13q" if mod == "pam4" else "prbs13"))
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    stat = run_statistical(cfg, channel=cm)
    ratio = stat.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (mod, length_m, noise, n_dfe, stat.ber, mc.ber.ber, ratio)


@pytest.mark.parametrize("enob,length_m", [(5.0, 0.15), (6.5, 0.22), (8.0, 0.28)])
def test_adc_noise_is_in_the_statistical_engine(enob, length_m):
    """ADC receiver with 0.5 mV of receiver noise, so the ADC's own noise
    (fullscale^2/12 * 2^(-2 ENOB)) carries the BER: statistical within 2x of
    the time engine. Without it the statistical engine read 0 at ENOB 5,
    where the time engine counts 1.5e-4."""
    from halo_serdes.engine.statistical import adc_noise_sigma

    cfg = _adc_link_for_noise(length_m, enob)
    assert adc_noise_sigma(cfg) == pytest.approx(0.6 / np.sqrt(12) * 2.0 ** -enob)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 30, mc.ber.n_errors
    st = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    assert 0.5 < st.ber / mc.ber.ber < 2.0, (enob, st.ber, mc.ber.ber)


def _adc_link_for_noise(length_m, enob):
    from halo_serdes.config.schema import AdcConfig, CdrConfig, CtleConfig, FfeConfig, RxConfig

    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0, r_skin=2.0e-3,
                              loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=10, n_lanes=16, enob=enob, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=14, adapt="lms", mu=3e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0005),
        sim=SimConfig(n_symbols=400_000, seed=3, pattern="prbs13q"))
