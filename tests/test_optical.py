"""Optical interconnect, stage 1: cascaded optical blocks + level-dependent noise.

Closed forms for every block, the channel cascade, the noise scale, and the
invariant-#3 cross-check on an optical topology: the statistical engine's
per-level noise kernels against the time engine's per-sample noise, at
three extinction ratios, on both receiver architectures.
"""

import dataclasses

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


@pytest.mark.parametrize("er_db,oma_dbm", [(3.0, -4.0), (4.5, -4.0), (6.0, -4.0),
                                            (6.0, -2.0), (7.5, -4.0)])
def test_optical_adc_within_1p5x_with_pattern_binned_noise(er_db, oma_dbm):
    """The per-level noise is a scale mixture: which neighbours were sent
    moves the light along the receive filter's memory, and one
    matched-variance Gaussian per level is optimistic in the tails. With it
    these five points read 0.98 / 0.86 / 0.76 / 0.68 / 0.65 of the time
    engine (800k symbols, the last outside 1.5x and falling with ER); binning
    on the two nearest neighbours brings them to 0.99 / 0.97 / 0.96 / 0.96 /
    0.94 (ROADMAP P3 #11)."""
    cfg = _adc_cfg(er_db, oma_dbm, n_sym=800_000)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    st = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = st.ber / mc.ber.ber
    assert 1 / 1.5 < ratio < 1.5, (er_db, st.ber, mc.ber.ber, ratio)


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


# ------------------------------------------- stage 3: large-signal curve ---

def test_static_curve_keeps_oma_and_er_and_derives_rlm():
    """Outer levels pinned (OMA, ER unchanged), R_LM = 1 without compression
    and falling monotonically with it; the VCSEL curve has the closed form
    R_LM = 1 - 8 kappa / 3."""
    from halo_serdes.optical import optical_rlm, static_curve

    for kind, extra in (("vcsel_mmf", dict(modal_bw_mhz_km=4700.0)),
                        ("eml_smf", dict(dispersion_ps_nm_km=-1.9))):
        assert static_curve(OpticalConfig(kind=kind, **extra)) is None
        assert optical_rlm(OpticalConfig(kind=kind, **extra)) == pytest.approx(1.0)
        prev = 1.0
        for c in (0.1, 0.3, 0.5, 0.7):
            o = OpticalConfig(kind=kind, li_compression=c, **extra)
            g = static_curve(o)
            assert g(np.array([-1.0, 1.0])) == pytest.approx([-1.0, 1.0])
            u = np.linspace(-1, 1, 201)
            assert np.all(np.diff(g(u)) > 0)                    # monotonic over the drive range
            r = optical_rlm(o)
            assert r < prev
            prev = r
            if kind == "vcsel_mmf":
                assert r == pytest.approx(1 - 8 * g.kappa / 3, abs=1e-12)
                # VCSEL compresses the top, EAM the bottom
                assert g(np.array([1 / 3]))[0] > 1 / 3
            else:
                assert g(np.array([-1 / 3]))[0] < -1 / 3
    # zero optical power is a floor, overdrive past the rollover peak holds
    o = OpticalConfig(kind="vcsel_mmf", li_compression=0.5, modal_bw_mhz_km=4700.0, er_db=4.0)
    g = static_curve(o)
    er = 10 ** 0.4
    assert g(np.array([-10.0]))[0] == pytest.approx(-(er + 1) / (er - 1))
    assert g(np.array([1 / (2 * g.kappa) + 5.0]))[0] == pytest.approx(g(np.array([1 / (2 * g.kappa)]))[0])


def test_curve_moves_the_level_powers_and_the_receiver_levels():
    from halo_serdes.engine.static_link import _levels
    from halo_serdes.optical import oe, static_curve

    cfg = _adc_cfg(4.5, -6.0, n_sym=2_000)
    lin = oe.level_powers(cfg.topology.optical, "pam4")
    squeezed = dataclasses.replace(cfg.topology.optical, li_compression=0.3)
    nl = oe.level_powers(squeezed, "pam4")
    assert nl[0] == pytest.approx(lin[0]) and nl[3] == pytest.approx(lin[3])
    assert nl[1] > lin[1] and nl[2] > lin[2]                    # VCSEL: inner levels pushed up
    cfg_nl = dataclasses.replace(cfg, topology=dataclasses.replace(cfg.topology, optical=squeezed))
    a = cfg.tx.swing / 2
    assert np.allclose(_levels(cfg_nl), a * static_curve(squeezed)(_levels(cfg) / a))
    assert np.array_equal(_levels(cfg), np.array([-a, -a / 3, a / 3, a]))   # no curve: untouched


def test_identity_curve_on_the_three_stage_path_matches_two_stages():
    """Cutting the chain at the E/O and passing an identity curve is the
    linear link: same error count within Poisson noise, same SNR."""
    from halo_serdes.channel.model import OpticalStages  # noqa: F401  (the type being built)
    from halo_serdes.optical import StaticCurve

    cfg = _adc_cfg(4.5, -6.0, n_sym=80_000)
    cm = ChannelModel.from_config(cfg)
    two = run_time_link(cfg, channel=cm)
    seg_a = ChannelModel.from_channel_config(cfg.topology.seg_a, cfg.symbol_rate)
    eo_m = ChannelModel.from_optical(cfg.topology.optical, seg_a.f, "eo")
    fib = ChannelModel.from_optical(cfg.topology.optical, seg_a.f, "fiber")
    o = cm.optical
    cm.optical = dataclasses.replace(
        o, curve=StaticCurve("rollover", 0.0, -10.0),
        drive=ChannelModel.cascade(seg_a, eo_m).band_limited(), optics=fib.band_limited(),
        drive_amplitude=o.noise.signal_swing_v / 2)
    three = run_time_link(cfg, channel=cm)
    n2, n3 = two.ber.n_errors, three.ber.n_errors
    assert n2 > 100
    assert abs(n2 - n3) < 4 * np.sqrt(n2 + n3)
    assert three.slicer_snr_db == pytest.approx(two.slicer_snr_db, abs=0.3)


def _eml_cfg(oma_dbm, n_sym):
    """A 1310 nm EML over 500 m of SMF on the ADC receiver of ``_adc_cfg``."""
    cfg = _adc_cfg(4.5, oma_dbm, n_sym=n_sym)
    o = dataclasses.replace(cfg.topology.optical, kind="eml_smf", f_r_hz=30e9,
                            modal_bw_mhz_km=None, dispersion_ps_nm_km=-1.0,
                            length_m=500.0, rin_db_hz=-145.0)
    return dataclasses.replace(cfg, topology=dataclasses.replace(cfg.topology, optical=o))


def _compressed(cfg, c):
    return dataclasses.replace(cfg, topology=dataclasses.replace(
        cfg.topology, optical=dataclasses.replace(cfg.topology.optical, li_compression=c)))


@pytest.mark.parametrize("link", ["eml_adc", "vcsel_ms_nrz"])
def test_invariant3_holds_under_a_large_signal_curve(link):
    """The curve bends ISI as well as moving the levels: it sits after the
    E/O dynamics, so its input already carries the neighbours. Modelling
    only the curve's steady-state levels, the statistical engine read 0.42x
    (EML, ADC) and 0.57x (VCSEL, mixed-signal NRZ, whose two levels the
    curve does not move at all) of the time engine at c = 0.4, falling to
    0.30 / 0.40 at c = 0.5. The pattern-binned shift
    (``optical_stage.curve_pattern_offsets``) brings 24 points over four
    links and c = 0..0.5 to 0.86-1.28x."""
    base = _eml_cfg(-8.0, 400_000) if link == "eml_adc" else _ms_cfg(4.5, -16.0, n_sym=400_000)
    cfg = _compressed(base, 0.4)
    cm = ChannelModel.from_config(cfg)
    assert cm.optical.curve is not None
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 200, mc.ber.n_errors
    lin = run_time_link(dataclasses.replace(base, sim=dataclasses.replace(base.sim, n_symbols=80_000)))
    assert mc.ber.ber > 1.5 * lin.ber.ber                      # the compression costs BER
    taps = mc.ffe_taps if cfg.rx.arch == "adc_dsp" else None
    st = run_statistical(cfg, channel=cm, ffe_taps=taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = st.ber / mc.ber.ber
    assert 1 / 1.5 < ratio < 1.5, (link, st.ber, mc.ber.ber, ratio)


def test_curve_pattern_offsets_vanish_for_an_identity_curve():
    """The correction is the curve chain minus the linear model; through an
    identity curve the two are the same waveform."""
    from halo_serdes.engine.optical_stage import curve_pattern_offsets
    from halo_serdes.optical import StaticCurve

    cfg = _adc_cfg(4.5, -6.0, n_sym=2_000)
    cm = ChannelModel.from_config(cfg)
    seg_a = ChannelModel.from_channel_config(cfg.topology.seg_a, cfg.symbol_rate)
    eo_m = ChannelModel.from_optical(cfg.topology.optical, seg_a.f, "eo")
    fib = ChannelModel.from_optical(cfg.topology.optical, seg_a.f, "fiber")
    o = cm.optical
    cm.optical = dataclasses.replace(
        o, curve=StaticCurve("rollover", 0.0, -10.0),
        drive=ChannelModel.cascade(seg_a, eo_m).band_limited(), optics=fib.band_limited(),
        drive_amplitude=o.noise.signal_swing_v / 2)
    mu, var = curve_pattern_offsets(cfg, cm)
    assert mu.shape == var.shape == (cfg.osr, 4, 4, 4)
    assert np.abs(mu).max() < 1e-12 and var.max() < 1e-20


def test_curve_pattern_offsets_follow_the_neighbours():
    """Under compression the shift depends on the neighbours, not just the
    level (that part a level change could carry), and is largest where the
    curve bends hardest."""
    from halo_serdes.engine.optical_stage import curve_pattern_offsets

    cfg = _compressed(_eml_cfg(-8.0, 2_000), 0.4)
    cm = ChannelModel.from_config(cfg)
    mu, var = curve_pattern_offsets(cfg, cm)
    centre = mu[cfg.osr // 2]
    half_gap = 0.5 * float(np.min(np.diff(_levels_of(cfg)))) * _main_cursor(cfg, cm)
    spread = centre - centre.mean(axis=(1, 2), keepdims=True)  # neighbour part per level
    assert np.abs(spread).max() > 0.05 * half_gap
    # the remainder after the bins is small next to what the bins carry
    assert np.sqrt(var[cfg.osr // 2].mean()) < 0.5 * np.abs(spread).max()


def _levels_of(cfg):
    from halo_serdes.engine.static_link import _levels

    return _levels(cfg)


def _main_cursor(cfg, cm):
    h1, h2 = split_impulses(cfg, cm)
    p = np.convolve(np.convolve(h1, h2), np.ones(cfg.osr))
    return float(np.abs(p).max())


def test_explicit_level_sigma_with_a_curve_warns():
    cfg = _compressed(_adc_cfg(4.5, -6.0, n_sym=2_000), 0.3)
    cm = ChannelModel.from_config(cfg)
    with pytest.warns(UserWarning, match="explicit level_sigma"):
        run_statistical(cfg, channel=cm, level_sigma=np.full(4, 1e-3))


def _with_module_ctle(cfg, drv_db, tia_db):
    return dataclasses.replace(cfg, topology=dataclasses.replace(
        cfg.topology, optical=dataclasses.replace(cfg.topology.optical, drv_ctle_db=drv_db,
                                                  tia_ctle_db=tia_db)))


def test_module_ctle_sits_where_the_module_has_it():
    """An LPO module's driver CTLE is part of the drive, ahead of the
    photodiode; its TIA CTLE is behind it. Both at unity DC gain, so the
    OMA, the level powers and the noise scale are what the config says."""
    from halo_serdes.afe import Ctle

    cfg = _adc_cfg(4.5, -6.0, n_sym=2_000)
    a = ChannelModel.from_config(cfg)
    b = ChannelModel.from_config(_with_module_ctle(cfg, 6.0, 3.0))
    drv = Ctle(peak_db=6.0, fp1=cfg.symbol_rate / 2).transfer(a.f)
    tia = Ctle(peak_db=3.0, fp1=cfg.symbol_rate / 2).transfer(a.f)
    np.testing.assert_allclose(b.optical.pre_pd.H, a.optical.pre_pd.H * drv, atol=1e-12)
    np.testing.assert_allclose(b.optical.post_pd.H, a.optical.post_pd.H * tia, atol=1e-12)
    np.testing.assert_allclose(b.optical.noise.level_powers_w, a.optical.noise.level_powers_w)
    assert b.optical.noise.signal_swing_v == a.optical.noise.signal_swing_v
    # zero peaking is no block at all, not an all-pass with a second pole
    c = ChannelModel.from_config(_with_module_ctle(cfg, 0.0, 0.0))
    assert np.array_equal(c.H, a.H)


def test_the_tia_ctle_lifts_the_photodiode_noise_the_driver_ctle_does_not():
    """Where the CTLE sits is the whole point: behind the photodiode it
    boosts that node's noise with the signal, ahead of it it cannot."""
    cfg = _adc_cfg(4.5, -6.0, n_sym=2_000)

    def sigma(c):
        cm = ChannelModel.from_config(c)
        h1, h2 = split_impulses(c, cm)
        return slicer_sigma_per_level(c, cm, h1, h2)

    base = sigma(cfg)
    assert np.all(sigma(_with_module_ctle(cfg, 0.0, 6.0)) > 1.15 * base)
    np.testing.assert_allclose(sigma(_with_module_ctle(cfg, 6.0, 0.0)), base, rtol=0.05)


@pytest.mark.parametrize("drv_db,tia_db,oma_dbm", [(6.0, 0.0, -7.0), (3.0, 3.0, -4.0)])
def test_invariant3_holds_with_module_equalisation(drv_db, tia_db, oma_dbm):
    """The module CTLEs are LTI, so they ride in both engines' impulses; the
    cross-check holds as it does without them. (The driver CTLE buys enough
    that its point needs less light to make errors to count.)"""
    cfg = _with_module_ctle(_adc_cfg(4.5, oma_dbm, n_sym=400_000), drv_db, tia_db)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    st = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = st.ber / mc.ber.ber
    assert 1 / 1.5 < ratio < 1.5, (drv_db, tia_db, st.ber, mc.ber.ber, ratio)


def _with_host_tx_ffe(cfg):
    # example 32's host FFE: the light reaching the photodiode is pre-emphasised
    return dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, fir_taps=(-0.06, 0.68, -0.26),
                                                           fir_n_pre=1))


@pytest.mark.parametrize("er_db", [4.5, 6.0])
def test_optical_noise_kernels_carry_the_tx_ffe(er_db):
    """The light at the photodiode carries the Tx FFE, so the noise each level
    sees along the receiver's memory comes from that pulse. Checked exactly,
    without Monte Carlo: the per-sample variance the time engine draws at the
    power of a noiseless waveform, through the receiver's impulse, averaged
    per transmitted level. Both kernels match it to 1 %; built from stage 1
    alone (before 2026-10-09) they were 16-22 % low on the bottom level and
    19-22 % high on the top one."""
    from halo_serdes.core.sampler import hold
    from halo_serdes.engine.lti import fft_filter
    from halo_serdes.engine.optical_stage import slicer_sigma_binned
    from halo_serdes.engine.static_link import _levels
    from halo_serdes.tx.pipeline import TxPipeline

    cfg = _with_host_tx_ffe(_adc_cfg(er_db, -4.0))
    cm = ChannelModel.from_config(cfg)
    osr, noise = cfg.osr, cm.optical.noise
    h1, h2 = split_impulses(cfg, cm)
    tx = TxPipeline.from_config(cfg).equivalent_symbol_response(osr)
    sym = np.random.default_rng(3).integers(0, 4, size=20_000)
    y_pd = fft_filter(fft_filter(hold(_levels(cfg)[sym], osr), tx), h1)
    var = fft_filter(noise.sigma_v(noise.power_w(y_pd)) ** 2, h2 * h2)
    peak = int(np.argmax(np.abs(np.convolve(np.convolve(np.convolve(np.ones(osr), tx), h1), h2))))
    k = np.arange(200, sym.size - 200)
    exact = np.array([np.sqrt(var[k[sym[k] == lv] * osr + peak].mean()) for lv in range(4)])
    per_level = slicer_sigma_per_level(cfg, cm, h1, h2)
    binned = slicer_sigma_binned(cfg, cm, h1, h2)
    binned = np.sqrt((binned ** 2).reshape(4, -1).mean(axis=1))   # neighbours equally likely
    np.testing.assert_allclose(per_level, exact, rtol=0.01)
    np.testing.assert_allclose(binned, exact, rtol=0.01)


def test_invariant3_holds_with_a_host_tx_ffe():
    """End to end on a linear optical link: with the Tx FFE missing from the
    noise kernels the statistical engine read 3.0x the time engine here (the
    cross-check itself broken, not just loose); with it, 1.16x."""
    cfg = _with_host_tx_ffe(_adc_cfg(4.5, -10.0, n_sym=1_000_000))
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    st = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = st.ber / mc.ber.ber
    assert 1 / 1.5 < ratio < 1.5, (st.ber, mc.ber.ber, ratio)


def test_optical_package_imports_only_numpy():
    # optical/ is the device-physics layer; keeping it numpy-only keeps it
    # importable on the phone and free of engine dependencies.
    import ast
    from pathlib import Path

    import halo_serdes.optical as pkg

    allowed = {"numpy", "__future__", "dataclasses"}
    bad = []
    for path in sorted(Path(pkg.__file__).parent.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            bad += [f"{path.name}: {n}" for n in names if n.split(".")[0] not in allowed]
    assert not bad, bad
