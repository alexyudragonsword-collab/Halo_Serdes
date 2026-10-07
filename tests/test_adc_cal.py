"""Background calibration of the TI-ADC lanes (``adc.cal``, ROADMAP P3 #7).

``adc.calibrated`` is the ideal model: the mismatch is drawn, then zeroed.
``adc.cal.mode = "background"`` is an algorithm: each lane's offset and gain
are estimated from the data and corrected after the quantizer, so what is
left is a residual that depends on the step. What has to hold:

* the correction itself is right (true estimates, step 0 = the ideal model);
* the estimates converge to the lanes' actual offsets and gain ratios, with a
  residual that shrinks with the step, and the link gets back most of what
  the mismatch cost -- all of it but a fraction of a dB at a small step;
* skew (``mu_skew``): each lane's delay trim converges to the lane's
  drawn skew less the lanes' mean, and with all three on the link is back
  within a fraction of a dB of one without mismatch;
* the state is carried across chunks and streamed windows bit for bit, and
  the JIT and the pure-Python kernel agree;
* off means off: the other modes never touch the new code.
"""

import dataclasses

import numpy as np
import pytest

import halo_serdes.cdr.adc_kernel as ak
from halo_serdes.channel import ChannelModel
from halo_serdes.config.schema import (
    AdcCalConfig,
    AdcConfig,
    CdrConfig,
    ChannelConfig,
    CtleConfig,
    DfeConfig,
    FfeConfig,
    LinkConfig,
    RxConfig,
    SimConfig,
    TxConfig,
)
from halo_serdes.engine import run_time_link
from test_chunked_kernels import _chunked, _same

needs_jit = pytest.mark.skipif(ak._adc_rx_core is ak._adc_rx_core_py,
                               reason="hundreds of thousands of symbols through the Python kernel")

MISMATCH = dict(offset_sigma=0.01, gain_sigma=0.03)


def _cfg(n_sym=20_000, warmup=None, **adc):
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.12, rdc=5.0, r_skin=2.0e-3,
                              loss_tangent=0.012, n_freq=4096),
        tx=TxConfig(swing=1.0, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6, **adc),
                    ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                    dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q", warmup_discard=warmup))


def _bg(mu_off, mu_gain=None, mu_skew=0.0):
    return AdcCalConfig("background", mu_off, mu_off if mu_gain is None else mu_gain, mu_skew)


def _residual(r):
    """(offset rms [V], gain ratio rms) left after the correction."""
    a, cal = r.extras["adc"], r.extras["adc_cal"]
    g = a.gains * cal[2]
    return float(np.std(a.offsets - cal[0])), float(np.std(g / g.mean()))


def test_the_correction_with_the_true_values_is_the_ideal_model(monkeypatch):
    """Seed the estimates with the drawn mismatch and freeze them (step 0):
    the link is the one ``calibrated=True`` gives, up to the rounding of
    (q - o) / g against a quantizer that never saw the mismatch."""
    init = ak.AdcRxRun.__init__

    def seeded(self, *a, **k):
        init(self, *a, **k)
        self.cal[0] = self.params[2]                 # offsets
        self.cal[2] = 1.0 / self.params[3]           # 1 / gains

    cm = ChannelModel.from_config(_cfg())
    ideal = run_time_link(_cfg(calibrated=True, **MISMATCH), channel=cm)
    raw = run_time_link(_cfg(**MISMATCH), channel=cm)
    monkeypatch.setattr(ak.AdcRxRun, "__init__", seeded)
    fixed = run_time_link(_cfg(cal=_bg(0.0), **MISMATCH), channel=cm)
    assert raw.slicer_snr_db < ideal.slicer_snr_db - 6.0
    assert abs(fixed.slicer_snr_db - ideal.slicer_snr_db) < 0.3, (fixed.slicer_snr_db,
                                                                 ideal.slicer_snr_db)


def test_offsets_converge_to_the_lane_means():
    """Short and Python-friendly: offsets only, a fast step."""
    r = run_time_link(_cfg(n_sym=20_000, offset_sigma=0.01, cal=_bg(2.0 ** -7, 0.0)))
    a, cal = r.extras["adc"], r.extras["adc_cal"]
    assert np.corrcoef(a.offsets, cal[0])[0, 1] > 0.9
    assert np.std(a.offsets - cal[0]) < 0.5 * np.std(a.offsets)
    assert np.all(cal[2] == 1.0)                     # no gain step: no gain correction
    assert np.all(cal[3] == cal[3][0]) or np.ptp(cal[3]) <= 1   # every lane converted alike


@needs_jit
def test_background_calibration_recovers_the_link_and_the_step_sets_the_residual():
    """Steady state (the first 3/4 of the run not scored): the mismatch costs
    ~10 dB here; a step of 2^-12 gets all but a fraction of a dB back, 2^-8
    leaves a residual ~4x larger (it goes as sqrt(mu)) and the SNR below."""
    n = 400_000
    cm = ChannelModel.from_config(_cfg())
    run = {k: run_time_link(_cfg(n, 3 * n // 4, **kw, **MISMATCH), channel=cm)
           for k, kw in {"raw": {}, "ideal": {"calibrated": True},
                         "fast": {"cal": _bg(2.0 ** -8)}, "slow": {"cal": _bg(2.0 ** -12)}}.items()}
    snr = {k: r.slicer_snr_db for k, r in run.items()}
    assert snr["raw"] < snr["ideal"] - 6.0, snr
    assert snr["slow"] > snr["ideal"] - 0.8, snr
    assert snr["fast"] < snr["slow"] - 1.0, snr
    off_f, g_f = _residual(run["fast"])
    off_s, g_s = _residual(run["slow"])
    a = run["raw"].extras["adc"]
    assert off_s < 0.15 * np.std(a.offsets) and g_s < 0.2 * np.std(a.gains / a.gains.mean())
    assert off_f > 2.0 * off_s and g_f > 2.0 * g_s, (off_f, off_s, g_f, g_s)


@pytest.mark.parametrize("stream", [False, True], ids=["chunked", "streamed"])
def test_calibration_state_carries_across_chunks(stream):
    cfg = _cfg(12_000, cal=_bg(2.0 ** -6, mu_skew=2.0 ** -6), skew_sigma_ui=0.04, **MISMATCH)
    if stream:
        cfg = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, stream=True))
    cm = ChannelModel.from_config(cfg)
    whole = run_time_link(_chunked(cfg, cfg.sim.n_symbols), channel=cm)
    for chunk in (997, 1):
        part = run_time_link(_chunked(cfg, chunk), channel=cm)
        _same(whole, part)
        assert np.array_equal(whole.extras["adc_cal"], part.extras["adc_cal"])


@pytest.mark.skipif(ak._adc_rx_core is ak._adc_rx_core_py,
                    reason="compares the JIT with the pure-Python kernel")
def test_jit_and_python_agree(monkeypatch):
    cfg = _cfg(6_000, cal=_bg(2.0 ** -6, mu_skew=2.0 ** -6), skew_sigma_ui=0.04, **MISMATCH)
    cm = ChannelModel.from_config(cfg)
    jit = run_time_link(cfg, channel=cm)
    monkeypatch.setattr(ak, "_adc_rx_core", ak._adc_rx_core_py)
    py = run_time_link(cfg, channel=cm)
    _same(jit, py)
    assert np.allclose(jit.extras["adc_cal"], py.extras["adc_cal"], rtol=0, atol=1e-12)


def test_off_leaves_no_state_and_the_modes_exclude_each_other():
    r = run_time_link(_cfg(4_000, **MISMATCH))
    assert r.extras["adc_cal"] is None
    with pytest.raises(ValueError, match="exclude each other"):
        AdcConfig(calibrated=True, cal=_bg(2.0 ** -10))
    with pytest.raises(ValueError, match="adc.cal.mu_gain"):
        AdcCalConfig("background", 0.01, 1.5)
    with pytest.raises(ValueError, match="adc.cal.mu_skew"):
        AdcCalConfig("background", 0.01, 0.01, -1.0)
    with pytest.raises(ValueError, match="adc.cal.mode"):
        AdcCalConfig("foreground")


def _skew_left(r):
    """(drawn skew less its mean, trim) [samples]."""
    a, cal = r.extras["adc"], r.extras["adc_cal"]
    return a.skews - a.skews.mean(), cal[4]


@needs_jit
def test_skew_trims_converge_to_the_lane_skews():
    """Skew alone, 0.05 UI rms: the trims track the drawn skews (their common
    part is the CDR's phase, so only the differences are measurable) and
    stay zero-mean; the link gets back what the skew cost."""
    n = 200_000
    cm = ChannelModel.from_config(_cfg())
    none = run_time_link(_cfg(n, 3 * n // 4), channel=cm)
    raw = run_time_link(_cfg(n, 3 * n // 4, skew_sigma_ui=0.05), channel=cm)
    cal = run_time_link(_cfg(n, 3 * n // 4, skew_sigma_ui=0.05, cal=_bg(0.0, 0.0, 2.0 ** -8)),
                        channel=cm)
    true, trim = _skew_left(cal)
    assert abs(np.mean(trim)) < 1e-9           # zero-mean up to rounding
    assert np.corrcoef(true, trim)[0, 1] > 0.95
    assert np.std(true - trim) < 0.35 * np.std(true)
    assert raw.slicer_snr_db < none.slicer_snr_db - 1.0
    assert cal.slicer_snr_db > none.slicer_snr_db - 0.4, (cal.slicer_snr_db, none.slicer_snr_db)
    assert cal.extras["cycle_slips"] == 0


@needs_jit
def test_with_skew_present_the_three_loops_together_recover_the_link():
    """Offset, gain and skew mismatch together: with all three loops the
    link is within half a dB of one without mismatch, and clearly above
    offset + gain alone, which leave the skew in -- and get to the ideal
    offset / gain model on the same link (it was once read as 'slow
    convergence': the ideal model drew another link, see the next test)."""
    n = 400_000
    mm = dict(skew_sigma_ui=0.04, **MISMATCH)
    cm = ChannelModel.from_config(_cfg())
    none = run_time_link(_cfg(n, 3 * n // 4), channel=cm)
    og = run_time_link(_cfg(n, 3 * n // 4, cal=_bg(2.0 ** -12), **mm), channel=cm)
    full = run_time_link(_cfg(n, 3 * n // 4, cal=_bg(2.0 ** -12, mu_skew=2.0 ** -8), **mm),
                         channel=cm)
    assert full.slicer_snr_db > none.slicer_snr_db - 0.5, (full.slicer_snr_db, none.slicer_snr_db)
    assert full.slicer_snr_db > og.slicer_snr_db + 1.0, (full.slicer_snr_db, og.slicer_snr_db)


def test_ideal_calibration_is_the_same_link_instance():
    """``calibrated=True`` zeroes the drawn offsets and gains instead of not
    drawing them: everything drawn after (the skews, the ENOB noise, the Rx
    clock) is the uncalibrated run's. Skipping the draws once made the ideal
    model a different skew instance -- 2 dB apart on the same config, read
    as a calibration loop converging slowly."""
    mm = dict(skew_sigma_ui=0.04, **MISMATCH)
    cm = ChannelModel.from_config(_cfg())
    raw = run_time_link(_cfg(4_000, **mm), channel=cm).extras["adc"]
    ideal = run_time_link(_cfg(4_000, calibrated=True, **mm), channel=cm).extras["adc"]
    assert np.array_equal(raw.skews, ideal.skews)
    assert not ideal.offsets.any() and np.all(ideal.gains == 1.0)


@needs_jit
def test_background_offset_gain_reaches_the_ideal_model_with_skew_present():
    n = 400_000
    mm = dict(skew_sigma_ui=0.04, **MISMATCH)
    cm = ChannelModel.from_config(_cfg())
    ideal = run_time_link(_cfg(n, 3 * n // 4, calibrated=True, **mm), channel=cm)
    og = run_time_link(_cfg(n, 3 * n // 4, cal=_bg(2.0 ** -12), **mm), channel=cm)
    assert abs(og.slicer_snr_db - ideal.slicer_snr_db) < 0.3, (og.slicer_snr_db,
                                                              ideal.slicer_snr_db)
