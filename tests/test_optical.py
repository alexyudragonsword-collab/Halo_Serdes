"""Optical interconnect, stage 1: cascaded optical blocks + level-dependent noise.

Closed forms for every block, the channel cascade, the noise scale, and the
invariant-#3 cross-check on an optical topology: the statistical engine's
per-level noise kernels against the time engine's per-sample noise, at
three extinction ratios, on both receiver architectures.
"""

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    OpticalConfig, RxConfig, SimConfig, TopologyConfig, TxConfig,
)
from halo_serdes.engine import run_time_link
from halo_serdes.engine.optical_stage import slicer_sigma_per_level, split_impulses
from halo_serdes.engine.static_link import make_pattern
from halo_serdes.engine.statistical import run_statistical
from halo_serdes.optical import OpticalNoise, eo, fiber, oe

F = np.linspace(0.0, 128e9, 2049)


def _flat(h=0.5, name="flat"):
    return ChannelModel(np.full(F.size, h, dtype=complex), F, name=name)


# ------------------------------------------------------------- cascade ---

def test_cascade_of_two_ideal_matched_channels_is_a_quarter():
    c = ChannelModel.cascade(_flat(name="a"), _flat(name="b"))
    assert np.allclose(c.H, 0.25)
    assert c.ref_gain == 0.25                      # two dividers, zero loss
    assert c.loss_at(50e9) == pytest.approx(0.0)
    assert c.name == "a x b"


def test_cascade_rejects_a_different_grid():
    other = ChannelModel(np.full(1025, 0.5, dtype=complex), np.linspace(0, 128e9, 1025))
    with pytest.raises(ValueError, match="grids differ"):
        ChannelModel.cascade(_flat(), other)


def test_touchstone_cascaded_with_itself_doubles_its_loss():
    pytest.importorskip("skrf")
    from pathlib import Path

    s4p = Path(__file__).resolve().parents[1] / "data/channels/peters_01_0605_T20_thru.s4p"
    if not s4p.exists():
        pytest.skip("bundled s4p missing")
    one = ChannelModel.from_touchstone(str(s4p), f_max=20e9, n_freq=1025)
    two = ChannelModel.cascade(one, one)
    for f in (5e9, 10e9, 14e9):
        assert two.loss_at(f) == pytest.approx(2.0 * one.loss_at(f), abs=1e-9)


# ------------------------------------------------------------- blocks ---

def test_vcsel_response_peaks_near_f_r_and_bandwidth_matches_closed_form():
    f_r, damping = 22e9, 8e9                       # lightly damped: a visible resonance
    f = np.linspace(0.0, 60e9, 60001)
    H = np.abs(eo.vcsel_response(f, f_r, damping))
    assert H[0] == pytest.approx(1.0)
    f_peak_analytic = np.sqrt(f_r ** 2 - damping ** 2 / 2.0)
    assert f[np.argmax(H)] == pytest.approx(f_peak_analytic, rel=1e-3)
    assert f[np.argmax(H)] == pytest.approx(f_r, rel=0.05)     # "peaks at f_r" for light damping
    # 3 dB point: first crossing of 1/sqrt(2) above the peak vs the closed form
    above = np.where((f > f[np.argmax(H)]) & (H < 1 / np.sqrt(2)))[0][0]
    assert f[above] == pytest.approx(eo.vcsel_bandwidth_hz(f_r, damping), rel=0.01)
    # heavily damped (the shipped default): resonance under 0.1 dB, bandwidth still right
    H2 = np.abs(eo.vcsel_response(f, 22e9, 30e9))
    assert H2.max() < 10 ** (0.1 / 20)
    above = np.where(H2 < 1 / np.sqrt(2))[0][0]
    assert f[above] == pytest.approx(eo.vcsel_bandwidth_hz(22e9, 30e9), rel=0.01)


def test_eml_single_pole():
    assert abs(eo.eml_response(np.array([40e9]), 40e9))[0] == pytest.approx(1 / np.sqrt(2))


def test_om4_100m_has_its_3dBo_point_at_47GHz():
    """EMB 4700 MHz.km over 100 m: the optical -3 dB (|H| = 0.5) point is
    47 GHz; electrically that reads -6 dB on the channel's loss axis."""
    assert fiber.modal_bandwidth_hz(4700.0, 100.0) == pytest.approx(47e9)
    assert abs(fiber.modal_response(np.array([47e9]), 4700.0, 100.0))[0] == pytest.approx(0.5)
    opt = OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, length_m=100.0)
    m = ChannelModel.from_optical(opt, F, "fiber")
    assert m.ref_gain == 1.0
    assert m.loss_at(47e9) == pytest.approx(20 * np.log10(0.5), abs=1e-6)
    assert m.loss_at(0.0) == pytest.approx(0.0)
    # zero length is a wire
    assert np.allclose(fiber.modal_response(F, 4700.0, 0.0), 1.0)


def test_smf_dispersion_first_fade_at_theta_half_pi():
    d, length, lam = -1.9, 2000.0, 1310.0
    f0 = fiber.first_fade_hz(d, length, lam)
    assert abs(fiber.dispersion_response(np.array([f0]), d, length, lam))[0] < 1e-9
    assert fiber.dispersion_response(np.array([0.0]), d, length, lam)[0] == pytest.approx(1.0)
    # chirp moves the null: cos(theta) - alpha sin(theta) = 0 at tan(theta) = 1/alpha,
    # i.e. earlier when D and alpha have the same sign, later when they differ
    alpha = 1.0
    theta = np.arctan(1.0 / alpha)
    f_alpha = f0 * np.sqrt(theta / (np.pi / 2))
    assert abs(fiber.dispersion_response(np.array([f_alpha]), -d, length, lam, alpha))[0] < 1e-9
    assert abs(fiber.dispersion_response(np.array([f_alpha]), d, length, lam, alpha))[0] > 1.0
    assert fiber.first_fade_hz(0.0, length, lam) == np.inf


def test_oe_butterworth_3db_and_level_powers():
    opt = OpticalConfig(kind="eml_smf", dispersion_ps_nm_km=0.0, tia_bw_hz=40e9,
                        oma_dbm=0.0, er_db=4.5)
    assert abs(oe.response(opt, np.array([40e9])))[0] == pytest.approx(1 / np.sqrt(2))
    assert abs(oe.response(opt, np.array([0.0])))[0] == pytest.approx(1.0)
    p = oe.level_powers(opt, "pam4")
    assert p.size == 4 and np.all(np.diff(p) > 0)
    assert p[-1] - p[0] == pytest.approx(1e-3)                 # OMA 0 dBm
    assert p[-1] / p[0] == pytest.approx(10 ** 0.45)           # ER 4.5 dB
    assert np.allclose(np.diff(p), np.diff(p)[0])              # ideal thirds
    assert oe.level_powers(opt, "nrz").tolist() == [p[0], p[-1]]
    assert oe.output_swing_v(opt) == pytest.approx(0.7 * 1e-3 * 2000.0)


# --------------------------------------------------------------- noise ---

def _noise(er_db=4.5, oma_dbm=0.0, rin=-140.0, tia=12.0, swing_v=0.5, dt=1 / (53.125e9 * 16)):
    opt = OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, er_db=er_db,
                        oma_dbm=oma_dbm, rin_db_hz=rin, tia_noise_pa_sqrthz=tia)
    return OpticalNoise.from_config(opt, "pam4", swing_v, dt), opt


def test_noise_densities_are_the_textbook_terms():
    n, opt = _noise()
    q, r = 1.602176634e-19, 0.7
    p = n.level_powers_w
    expect = 2 * q * r * p + 1e-14 * (r * p) ** 2 + (12e-12) ** 2
    assert np.allclose(n.current_psd_a2_hz(p), expect)
    # scale: the outer-level separation at the PD node is R * OMA
    assert n.volts_per_amp * r * opt.oma_w == pytest.approx(0.5)
    assert np.all(np.diff(n.sigma_per_level()) > 0)       # upper level noisier


def test_no_isi_four_level_sample_variance_matches_sigma_of_level_power():
    """Each level held steady at the PD node: the injected noise variance
    equals sigma^2(P_k) within 3 % over 1e5 samples, upper level noisiest."""
    n, _ = _noise()
    rng = np.random.default_rng(3)
    a = n.signal_swing_v / 2.0
    sig = n.sigma_per_level()
    for k, lv in enumerate(np.array([-1.0, -1 / 3, 1 / 3, 1.0]) * a):
        y = np.full(100_000, lv)
        out = n.inject(y, rng)
        # the waveform value maps back to that level's power exactly
        assert n.power_w(np.array([lv]))[0] == pytest.approx(n.level_powers_w[k])
        assert np.var(out - y) == pytest.approx(sig[k] ** 2, rel=0.03)
    assert sig[3] > sig[0]


def _topology(er_db, oma_dbm, seg_len=0.08, rin=-140.0, length_m=100.0):
    seg = dict(kind="analytic", length_m=seg_len, rdc=5.0, r_skin=2e-3,
               loss_tangent=0.012, n_freq=4096)
    return TopologyConfig(
        seg_a=ChannelConfig(**seg),
        optical=OpticalConfig(kind="vcsel_mmf", f_r_hz=22e9, damping_hz=30e9, er_db=er_db,
                              oma_dbm=oma_dbm, rin_db_hz=rin, length_m=length_m,
                              modal_bw_mhz_km=4700.0, responsivity_a_w=0.7,
                              tia_bw_hz=40e9, tia_noise_pa_sqrthz=12.0),
        seg_b=ChannelConfig(**seg))


def _adc_cfg(er_db, oma_dbm, n_sym=300_000, mod="pam4"):
    return LinkConfig(
        modulation=mod, symbol_rate=53.125e9, osr=16, tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=3.0),
                    adc=AdcConfig(n_bits=10, n_lanes=16, enob=None, fullscale=0.3),
                    ffe=FfeConfig(n_pre=3, n_post=8, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=1e-4),
        sim=SimConfig(n_symbols=n_sym, seed=11, pattern="prbs13q" if mod == "pam4" else "prbs13"),
        topology=_topology(er_db, oma_dbm))


def _ms_cfg(er_db, oma_dbm, n_sym=400_000):
    return LinkConfig(
        modulation="nrz", symbol_rate=26.5625e9, osr=16, tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=3.0),
                    dfe=DfeConfig(n_taps=2), noise_rms=1e-4),
        sim=SimConfig(n_symbols=n_sym, seed=11, pattern="prbs13"),
        topology=_topology(er_db, oma_dbm))


def test_topology_builds_the_cascade_and_carries_the_split():
    cfg = _adc_cfg(4.5, -6.0)
    cm = ChannelModel.from_config(cfg)
    assert cm.optical is not None
    assert cm.ref_gain == pytest.approx(0.25)
    # the full response is the product of the two halves, on one grid
    assert np.allclose(cm.H, cm.optical.pre_pd.H * cm.optical.post_pd.H)
    assert cm.optical.noise.level_powers_w.size == 4
    # the electrical link does not carry one, and is the plain model
    plain = LinkConfig(channel=ChannelConfig(kind="analytic", length_m=0.1, rdc=5.0))
    pm = ChannelModel.from_config(plain)
    assert pm.optical is None and pm.ref_gain == 0.5
    assert np.array_equal(pm.H, ChannelModel.from_rlgc(plain.channel, 2 * plain.symbol_rate,
                                                        plain.channel.n_freq).H)


def test_time_engine_per_level_slicer_noise_matches_the_second_moment_model():
    """The statistical engine's per-level sigma is the conditional second
    moment of the photodiode power along the receive filter's memory. On
    the time engine's own slicer stream, grouped by transmitted level, it
    must land within 10 % -- and the memoryless nominal-power sigma must
    not (it is what the 2x check first tripped over)."""
    cfg = _adc_cfg(6.0, -4.0, n_sym=120_000)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    y, warm, lv = mc.y_slicer, mc.extras["warmup"], mc.extras["levels"]
    ref = make_pattern(cfg)[warm: warm + y.size]
    measured = np.array([np.std(y[ref == k] - lv[k]) for k in range(4)])
    h1, h2 = split_impulses(cfg, cm)
    model = slicer_sigma_per_level(cfg, cm, h1, h2, mc.ffe_taps)
    nominal = slicer_sigma_per_level(cfg, cm, h1, h2, mc.ffe_taps, nominal=True)
    assert np.all(np.abs(model / measured - 1.0) < 0.10), (model, measured)
    assert np.all(np.diff(measured) > 0)                      # upper level noisier, end to end
    assert nominal[0] < 0.8 * measured[0] and nominal[3] > 1.1 * measured[3]


@pytest.mark.parametrize("er_db", [3.0, 4.5, 6.0])
def test_invariant3_optical_adc_within_2x(er_db):
    """LTI optical path + level-dependent noise: statistical vs time BER
    within 2x on the ADC receiver (invariant #3 -- the stage-1 core assertion)."""
    cfg = _adc_cfg(er_db, -6.0)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    st = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = st.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (er_db, st.ber, mc.ber.ber, ratio)


@pytest.mark.parametrize("er_db", [3.0, 4.5, 6.0])
def test_invariant3_optical_mixed_signal_within_2x(er_db):
    """Same assertion on the mixed-signal receiver (invariant #2: both
    architectures run the optical topology). NRZ, where the mixed-signal
    electrical cross-check itself holds."""
    cfg = _ms_cfg(er_db, -16.0)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    st = run_statistical(cfg, channel=cm)
    ratio = st.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (er_db, st.ber, mc.ber.ber, ratio)


def test_noise_free_optics_are_pure_lti():
    """With RIN and TIA noise off and a huge OMA (shot noise negligible) the
    two-stage time path is the one-stage LTI path: the same link run on the
    cascade as a plain electrical channel lands on the same slicer SNR."""
    cfg = _adc_cfg(4.5, +30.0, n_sym=60_000)
    cfg = LinkConfig(**{**cfg.__dict__, "topology": TopologyConfig(
        seg_a=cfg.topology.seg_a, seg_b=cfg.topology.seg_b,
        optical=OpticalConfig(**{**cfg.topology.optical.__dict__,
                                 "rin_db_hz": -300.0, "tia_noise_pa_sqrthz": 0.0}))})
    cm = ChannelModel.from_config(cfg)
    # only shot noise is left, and at 1 W it is below 1e-3 of the swing per sample
    assert np.all(cm.optical.noise.sigma_per_level() < 1e-3 * cm.optical.noise.signal_swing_v)
    two_stage = run_time_link(cfg, channel=cm)
    plain = ChannelModel(cm.H, cm.f, name="plain", ref_gain=cm.ref_gain)   # no .optical
    one_stage = run_time_link(cfg, channel=plain)
    # residual-ISI-only SNRs; they differ by the energy each path's impulse
    # trimming keeps (the two-stage path trims each half, the plain path the
    # product), which is a dB at 28 dB, not a model difference
    assert abs(two_stage.slicer_snr_db - one_stage.slicer_snr_db) < 1.5
    assert two_stage.slicer_snr_db > 25.0


def test_ami_is_refused_with_a_topology():
    from halo_serdes.io.ami import NativeFirAmi

    cfg = _adc_cfg(4.5, -6.0, n_sym=2_000)
    with pytest.raises(ValueError, match="AMI"):
        run_time_link(cfg, tx_ami=NativeFirAmi([1.0]))
