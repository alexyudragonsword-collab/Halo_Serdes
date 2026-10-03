"""TxPipeline: the one place the transmitter is assembled.

Stage 0 contract: with no DAC and no driver model the pipeline is exactly the
hand-assembled FIR + ZOH + single pole every engine used to build, value for
value, with the random draws in the same order.
"""

import numpy as np
import pytest

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import ChannelConfig, ClockConfig, RxConfig, SimConfig, TxConfig
from halo_serdes.core.sampler import hold, upsampled_taps
from halo_serdes.core.waveform import Waveform
from halo_serdes.engine.static_link import make_pattern
from halo_serdes.tx.builder import apply_single_pole, symbols_to_voltages, tx_fir
from halo_serdes.tx.jitter import edge_jitter_seq, jittered_zoh
from halo_serdes.tx.pipeline import TxPipeline

PROFILE = "data/clock_profiles/bench_markulic16_sspll_40m_10p24g.yaml"


def _cfg(clock: ClockConfig, taps=(-0.08, 0.72, -0.2), bw=40e9, mod="pam4") -> LinkConfig:
    return LinkConfig(modulation=mod, symbol_rate=26.5625e9, osr=16,
                      channel=ChannelConfig(kind="analytic", length_m=0.1),
                      tx=TxConfig(swing=1.0, fir_taps=taps, fir_n_pre=1, bw=bw, clock=clock),
                      rx=RxConfig(arch="adc_dsp"),
                      sim=SimConfig(n_symbols=3000, seed=5,
                                    pattern="prbs13q" if mod == "pam4" else "prbs13"))


def _by_hand(cfg, symbols, rng):
    """The assembly every engine carried before the pipeline existed."""
    v = symbols_to_voltages(symbols, cfg)
    if len(cfg.tx.fir_taps) > 1:
        v = tx_fir(v, cfg.tx.fir_taps, cfg.tx.fir_n_pre)
    jit = edge_jitter_seq(v.size, cfg, rng, v)
    wave = Waveform(jittered_zoh(v, cfg.osr, jit, cfg.ui), cfg.dt)
    if cfg.tx.bw is not None:
        wave = apply_single_pole(wave, cfg.tx.bw)
    return wave, jit


CLOCKS = [ClockConfig(), ClockConfig(rj_ui=0.01, sj_ui=0.02, sj_freq=5e6, dcd_ui=0.02),
          ClockConfig(kind="profile", file=PROFILE, f0_hz=26.5625e9, rj_ui=0.005)]


@pytest.mark.parametrize("clock", CLOCKS, ids=["ideal", "white", "profile"])
@pytest.mark.parametrize("taps", [(1.0,), (-0.08, 0.72, -0.2)], ids=["no_ffe", "ffe"])
def test_pipeline_is_the_hand_assembly_byte_for_byte(clock, taps):
    cfg = _cfg(clock, taps)
    sym = make_pattern(cfg)
    ref, ref_jit = _by_hand(cfg, sym, np.random.default_rng(9))
    pipe = TxPipeline.from_config(cfg)
    rng = np.random.default_rng(9)
    wave, jit = pipe.waveform_and_edges(pipe.symbol_stage(sym), rng)
    assert np.array_equal(wave.y, ref.y)
    assert np.array_equal(jit, ref_jit)
    # the generator is left where the old assembly left it: later draws
    # (receiver noise, ADC mismatch) are unchanged
    rng_ref = np.random.default_rng(9)
    _by_hand(cfg, sym, rng_ref)
    assert rng.random() == rng_ref.random()


def test_ideal_edges_are_a_plain_hold():
    cfg = _cfg(ClockConfig(), bw=None)
    sym = make_pattern(cfg)
    pipe = TxPipeline.from_config(cfg)
    v = pipe.symbol_stage(sym)
    assert np.array_equal(pipe.waveform(v).y, hold(v, cfg.osr))


def test_equivalent_symbol_response_is_the_upsampled_fir():
    cfg = _cfg(ClockConfig(), bw=None)
    assert np.array_equal(TxPipeline.from_config(cfg).equivalent_symbol_response(),
                          upsampled_taps(cfg.tx.fir_taps, cfg.osr))
    assert TxPipeline.from_config(_cfg(ClockConfig(), taps=(1.0,), bw=None)).equivalent_symbol_response() is None


@pytest.mark.parametrize("taps", [(1.0,), (-0.08, 0.72, -0.2)], ids=["no_ffe", "ffe"])
def test_equivalent_symbol_response_carries_the_driver_pole(taps):
    """With ``tx.bw`` the response is the FFE convolved with the pole's
    sampled impulse, and filtering the held symbols with it reproduces the
    pipeline's own waveform (the rFFT pole) away from the run's ends."""
    from halo_serdes.engine.lti import fft_filter

    cfg = _cfg(ClockConfig(), taps=taps, bw=20e9)
    pipe = TxPipeline.from_config(cfg)
    drv, pre = pipe.driver_response()
    assert drv.sum() == pytest.approx(1.0, abs=1e-4)          # unit DC gain, less the 1/n tail
    resp = pipe.equivalent_symbol_response()
    expected = drv if len(taps) == 1 else np.convolve(upsampled_taps(cfg.tx.fir_taps, cfg.osr), drv)
    assert np.array_equal(resp, expected)
    assert pipe.symbol_response_lead() == pre + (cfg.tx.fir_n_pre * cfg.osr if len(taps) > 1 else 0)

    sym = make_pattern(cfg)[:2000]
    levels = pipe.levels(sym)
    ref = pipe.waveform(pipe.symbol_stage(sym)).y
    via_impulse = fft_filter(hold(levels, cfg.osr), resp)
    lead = pipe.symbol_response_lead()
    mid = slice(200 * cfg.osr, 1800 * cfg.osr)
    assert np.max(np.abs(via_impulse[lead:][mid] - ref[mid])) < 1e-3 * cfg.tx.swing


def test_profile_clock_edges_through_the_pipeline_match_the_tracking_replay():
    from halo_serdes.analysis.cdr_tracking import tx_edge_offsets_s
    from halo_serdes.engine.timedomain import _tx_symbols

    cfg = _cfg(CLOCKS[2])
    rng = np.random.default_rng(cfg.sim.seed)
    v = symbols_to_voltages(_tx_symbols(cfg, make_pattern(cfg)), cfg)
    v = tx_fir(v, cfg.tx.fir_taps, cfg.tx.fir_n_pre)
    assert np.array_equal(tx_edge_offsets_s(cfg), edge_jitter_seq(v.size, cfg, rng, v))


# ------------------------------------------------------------ stage 1: driver

def test_driver_curve_is_the_e_o_curve_bit_for_bit():
    """drv_compression and optical li_compression are one parameter: at the
    same c the driver and the laser bend the same input identically."""
    from halo_serdes.config.schema import OpticalConfig
    from halo_serdes.optical import static_curve

    u = np.linspace(-1.0, 1.0, 2001)
    for c in (0.05, 0.2, 0.45):
        laser = static_curve(OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, li_compression=c))
        cfg = LinkConfig(tx=TxConfig(swing=2.0, drv_nl="curve", drv_compression=c))
        drv = TxPipeline.from_config(cfg).driver_nl
        assert drv.fullscale == 1.0
        assert np.array_equal(drv(u), laser(u))


def test_cubic_driver_hd3_matches_the_closed_form():
    from halo_serdes.tx.driver import DriverNl, hd3_cubic_db

    n, k = 4096, 17
    x = np.sin(2 * np.pi * np.arange(n) * k / n)
    for amp, oip3 in ((0.3, 1.5), (0.5, 1.2), (0.4, 0.9)):
        y = np.abs(np.fft.rfft(DriverNl("cubic", oip3_v=oip3)(amp * x)))
        measured = 20 * np.log10(y[3 * k] / y[k])
        assert abs(measured - hd3_cubic_db(amp, oip3)) < 0.5


def test_tanh_driver_is_one_db_compressed_at_p1db():
    from halo_serdes.tx.driver import DriverNl

    n, k, p1db = 4096, 13, 0.35
    x = p1db * np.sin(2 * np.pi * np.arange(n) * k / n)
    y = np.fft.rfft(DriverNl("tanh", p1db_v=p1db)(x))
    gain_db = 20 * np.log10(np.abs(y[k]) / (n / 2) / p1db)
    assert abs(gain_db + 1.0) < 0.02


def test_measured_rlm_falls_monotonically_with_compression():
    from halo_serdes.analysis.tx_metrics import tx_report

    rlms = []
    for c in (0.0, 0.1, 0.2, 0.3, 0.4):
        tx = dict(swing=1.0) if c == 0.0 else dict(swing=1.0, drv_nl="curve", drv_compression=c)
        cfg = LinkConfig(modulation="pam4", symbol_rate=53.125e9, osr=16, tx=TxConfig(**tx),
                         sim=SimConfig(n_symbols=8000, seed=2, pattern="prbs13q"))
        rlms.append(tx_report(cfg).rlm)
    assert rlms[0] == pytest.approx(1.0, abs=1e-9)
    assert np.all(np.diff(rlms) < 0), rlms


def test_driver_compression_is_hammerstein_and_warns_in_the_statistical_engine():
    """The curve acts on the held waveform before the driver pole: pole after
    curve, not curve after pole."""
    from halo_serdes.engine.statistical import run_statistical
    from halo_serdes.tx.driver import apply_single_pole

    cfg = LinkConfig(modulation="pam4", symbol_rate=53.125e9, osr=16,
                     channel=ChannelConfig(kind="analytic", length_m=0.1),
                     tx=TxConfig(swing=1.0, bw=30e9, drv_nl="curve", drv_compression=0.3),
                     sim=SimConfig(n_symbols=2000, seed=2, pattern="prbs13q"))
    pipe = TxPipeline.from_config(cfg)
    v = pipe.symbol_stage(make_pattern(cfg))
    held = Waveform(hold(v, cfg.osr), cfg.dt)
    expected = apply_single_pole(Waveform(pipe.driver_nl(held.y), cfg.dt), cfg.tx.bw)
    assert np.array_equal(pipe.waveform(v).y, expected.y)
    with pytest.warns(UserWarning, match="drv_nl"):
        run_statistical(cfg)


# ------------------------------------------- receivers see the Tx FFE (P1 1c)

def test_receiver_view_folds_the_ffe_in_and_reports_its_lead():
    cfg = _cfg(ClockConfig(), bw=None)
    h = np.random.default_rng(0).normal(size=400)
    h_rx, lead = TxPipeline.from_config(cfg).receiver_view(h)
    assert lead == cfg.tx.fir_n_pre * cfg.osr
    assert np.array_equal(h_rx, np.convolve(h, upsampled_taps(cfg.tx.fir_taps, cfg.osr)))
    one = TxPipeline.from_config(_cfg(ClockConfig(), taps=(1.0,), bw=None))
    same, zero = one.receiver_view(h)
    assert same is h and zero == 0          # single-tap links: untouched, byte for byte


@pytest.mark.parametrize("arch", ["adc_dsp", "mixed_signal"])
def test_time_engine_equalises_the_pulse_with_the_tx_ffe(arch):
    """A Tx with a 0.78 main tap: the receiver's starting equaliser, DFE seeds,
    slicer scale and symbol delay now come from the pulse it receives."""
    from halo_serdes.config.schema import AdcConfig, CdrConfig, CtleConfig, DfeConfig, FfeConfig
    from halo_serdes.engine import run_time_link

    if arch == "adc_dsp":
        rx = RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=3.0),
                      adc=AdcConfig(n_bits=10, n_lanes=16, fullscale=0.3),
                      ffe=FfeConfig(n_pre=3, n_post=8, adapt="lms", mu=3e-5), dfe=DfeConfig(n_taps=0),
                      cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15), noise_rms=1e-4)
        cfg = LinkConfig(modulation="pam4", symbol_rate=53.125e9, osr=16,
                         channel=ChannelConfig(kind="analytic", length_m=0.1, rdc=5.0, r_skin=2e-3,
                                               loss_tangent=0.012, n_freq=4096),
                         tx=TxConfig(swing=1.0, fir_taps=(-0.1, 0.75, -0.15), fir_n_pre=1), rx=rx,
                         sim=SimConfig(n_symbols=50_000, seed=11, pattern="prbs13q"))
        floor = 14.0                       # 9.5 dB (BER 0.10) before the fix
    else:
        rx = RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=3.0),
                      dfe=DfeConfig(n_taps=2), noise_rms=1e-4)
        cfg = LinkConfig(modulation="nrz", symbol_rate=26.5625e9, osr=16,
                         channel=ChannelConfig(kind="analytic", length_m=0.4, rdc=5.0, r_skin=2e-3,
                                               loss_tangent=0.012, n_freq=4096),
                         tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.78, -0.14), fir_n_pre=1), rx=rx,
                         sim=SimConfig(n_symbols=100_000, seed=11, pattern="prbs13"))
        floor = 9.0                        # 1.6e-4 BER before the fix
    res = run_time_link(cfg)
    assert res.slicer_snr_db > floor, res.slicer_snr_db
    assert res.ber.n_errors < 20, res.ber.n_errors
