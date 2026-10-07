"""Bit-true background ADC calibration in the fixed loop (``adc.cal`` with
``numeric.mode = "fixed"``; ROADMAP P3 #7 stage 3).

``_cal_word_py`` is the integer form of the float kernel's offset / gain
calibration, between the ADC word and the FFE; ``rtl/adc_dsp_loop.sv`` has it
too (``test_rtl_lockstep``). What has to hold:

* it agrees with an independent reference on every shift, rounding and
  saturation choice, including a step of 0 and a disabled estimate;
* the skew trims (``mu_skew``) move the lanes' PI codes, zero-mean exactly,
  and converge to the drawn skews as the float trims do;
* the digital replay from the raw words reproduces the closed loop, the
  calibration registers included, and JIT = Python;
* the integer loop gets what the float one gets: within a few tenths of a dB
  at every step, the true values frozen in giving the ideal model;
* off is off: the loop without calibration is the loop it was.
"""

import dataclasses

import numpy as np
import pytest

import halo_serdes.dsp.fixed_loop as fxl
from halo_serdes.channel import ChannelModel
from halo_serdes.config.schema import AdcCalConfig, NumericConfig
from halo_serdes.dsp.fixed_loop import replay_digital
from halo_serdes.engine import run_time_link
from test_adc_cal import MISMATCH, _cfg

needs_jit = pytest.mark.skipif(fxl._fixed_loop_closed is fxl._fixed_loop_closed_py,
                               reason="hundreds of thousands of symbols through the Python loop")


def _fixed(cfg):
    return dataclasses.replace(cfg, numeric=NumericConfig(mode="fixed"))


def _bg(mu, mu_skew=0.0):
    return AdcCalConfig("background", mu, mu, mu_skew)


def _reference(words, n_lanes, f, b, sh_o, sh_g, sh_p, wmax, pm0):
    """Plain Python ints, one lane dictionary; rounding written out."""
    def rnd(v, sh):
        return v if sh == 0 else (v + (1 << (sh - 1))) >> sh

    off = {lane: 0 for lane in range(n_lanes)}
    gain = {lane: 1 << b for lane in range(n_lanes)}
    pm = pm0
    out = []
    for k, x in enumerate(int(w) for w in words):
        lane = k % n_lanes
        d = x * 2 ** f - off[lane]
        y = (d * gain[lane]) >> b
        w = max(-wmax, min(wmax, (y + 2 ** (f - 1)) >> f))
        out.append(w)
        if sh_o >= 0:
            off[lane] += rnd(d, sh_o)
        if sh_g >= 0:
            gain[lane] += rnd((pm >> f) - w * w, sh_g)
        if sh_p >= 0:
            pm += rnd(w * w * 2 ** f - pm, sh_p)
    return out, [off[i] for i in range(n_lanes)], [gain[i] for i in range(n_lanes)], pm


@pytest.mark.parametrize("seed", range(6))
def test_cal_word_matches_an_independent_reference(seed):
    rng = np.random.default_rng(seed)
    n_lanes = int(rng.choice([2, 4, 8]))
    f, b = int(rng.integers(4, 18)), int(rng.integers(8, 16))
    sh_o, sh_g, sh_p = (int(rng.integers(-1, 9)) for _ in range(3))
    if seed == 0:
        sh_o = sh_g = sh_p = 0                     # the widest steps
    wmax = int(rng.choice([63, 255]))
    lane_off = rng.integers(-20, 20, n_lanes)
    lane_gain = rng.uniform(0.85, 1.15, n_lanes)
    k = np.arange(3000)
    words = np.round(lane_gain[k % n_lanes] * rng.normal(scale=40, size=k.size)
                     + lane_off[k % n_lanes]).astype(np.int64)
    pm0 = int(1600 * 2 ** f)
    cp = np.array([1, f, b, sh_o, sh_g, sh_p, wmax], dtype=np.int64)
    co = np.zeros(n_lanes, dtype=np.int64)
    cg = np.full(n_lanes, 1 << b, dtype=np.int64)
    cpm = np.array([pm0], dtype=np.int64)
    got = [int(fxl._cal_word_py(i % n_lanes, int(w), cp, co, cg, cpm)) for i, w in enumerate(words)]
    want, off, gain, pm = _reference(words, n_lanes, f, b, sh_o, sh_g, sh_p, wmax, pm0)
    assert got == want
    assert list(co) == off and list(cg) == gain and int(cpm[0]) == pm


def test_off_passes_the_word_through():
    cp = np.array([0, 16, 14, 3, 3, 3, 255], dtype=np.int64)
    co, cg, cpm = np.zeros(4, dtype=np.int64), np.ones(4, dtype=np.int64), np.zeros(1, dtype=np.int64)
    assert all(fxl._cal_word_py(i % 4, w, cp, co, cg, cpm) == w for i, w in enumerate(range(-300, 300)))
    assert not co.any() and np.all(cg == 1) and cpm[0] == 0


def test_replay_reproduces_the_closed_loop_registers_included():
    """The RTL's input is the raw ADC words: replaying them through the
    digital back end, calibration first, gives the closed loop's every
    decision, slicer value, PI code and final register."""
    r = run_time_link(_fixed(_cfg(8_000, cal=_bg(2.0 ** -6, 2.0 ** -6), skew_sigma_ui=0.04,
                                  **MISMATCH)))
    fx = r.extras["fixed"]
    rec = fx["record"]
    rp = replay_digital(fx["loop"], rec["xin"], rec["ref"])
    assert np.array_equal(rp["pi"], rec["pi"])
    assert np.array_equal(rp["dec"], rec["dec"]) and np.array_equal(rp["v_out"], rec["v_out"])
    for a, b in zip(rp["cal"], rec["cal"]):
        assert np.array_equal(np.asarray(a), np.asarray(b))
    assert not np.array_equal(rec["xin"], rec["x_cal"])        # it did correct


@pytest.mark.skipif(fxl._fixed_loop_digital is fxl._fixed_loop_digital_py,
                    reason="compares the JIT with the pure-Python loop")
def test_jit_and_python_agree(monkeypatch):
    r = run_time_link(_fixed(_cfg(6_000, cal=_bg(2.0 ** -6, 2.0 ** -6), skew_sigma_ui=0.04,
                                  **MISMATCH)))
    fx = r.extras["fixed"]
    rec = fx["record"]
    jit = replay_digital(fx["loop"], rec["xin"], rec["ref"])
    monkeypatch.setattr(fxl, "_fixed_loop_digital", fxl._fixed_loop_digital_py)
    monkeypatch.setattr(fxl, "_digital_step", fxl._digital_step_py)
    monkeypatch.setattr(fxl, "_cal_word", fxl._cal_word_py)
    monkeypatch.setattr(fxl, "_rnd", fxl._rnd_py)
    monkeypatch.setattr(fxl, "_trim", fxl._trim_py)
    monkeypatch.setattr(fxl, "_skew_step", fxl._skew_step_py)
    py = replay_digital(fx["loop"], rec["xin"], rec["ref"])
    assert np.array_equal(jit["dec"], py["dec"]) and np.array_equal(jit["v_out"], py["v_out"])
    for a, b in zip(jit["cal"], py["cal"]):
        assert np.array_equal(np.asarray(a), np.asarray(b))


def test_without_calibration_the_loop_is_unchanged():
    """The calibration registers sit idle and the words go through as they
    are: the record's corrected words are its raw ones."""
    r = run_time_link(_fixed(_cfg(4_000, **MISMATCH)))
    rec = r.extras["fixed"]["record"]
    assert np.array_equal(rec["xin"], rec["x_cal"])
    assert r.extras["fixed"]["loop"].cal[0] == 0


@needs_jit
def test_the_integer_loop_gets_what_the_float_one_gets():
    """Steady state, the first 3/4 unscored: at each step the bit-true loop
    lands within 0.3 dB of the float kernel (measured 0.13-0.19 dB), and its
    registers converge to the drawn mismatch."""
    n = 200_000
    cm = ChannelModel.from_config(_cfg())
    for mu in (2.0 ** -8, 2.0 ** -10):
        cfg = _cfg(n, 3 * n // 4, cal=_bg(mu), **MISMATCH)
        fl = run_time_link(cfg, channel=cm)
        fx = run_time_link(_fixed(cfg), channel=cm)
        assert abs(fx.slicer_snr_db - fl.slicer_snr_db) < 0.3, (mu, fx.slicer_snr_db,
                                                              fl.slicer_snr_db)
    loop, (co, cg, *_) = fx.extras["fixed"]["loop"], fx.extras["fixed"]["record"]["cal"]
    a = fx.extras["adc"]
    off = np.asarray(co) * 2.0 ** -int(loop.cal[1]) * loop.in_lsb
    g = a.gains * np.asarray(cg) * 2.0 ** -int(loop.cal[2])
    assert np.std(a.offsets - off) < 0.3 * np.std(a.offsets)
    assert np.std(g / g.mean()) < 0.5 * np.std(a.gains / a.gains.mean())


def test_skew_trims_are_zero_mean_and_shift_the_pi_codes():
    """The applied trims are the registers less their mean -- a shift, the
    lanes a power of two -- so they sum to zero to the LSB; and they are
    what moves one lane's PI code against the next."""
    r = run_time_link(_fixed(_cfg(8_000, cal=_bg(2.0 ** -6, 2.0 ** -6), skew_sigma_ui=0.04,
                                  **MISMATCH)))
    fx = r.extras["fixed"]
    loop, rec = fx["loop"], fx["record"]
    cts = np.concatenate([rec["cal"][3], [np.sum(rec["cal"][3])]])
    applied = np.array([fxl._trim_py(lane, cts, loop.lane_shift) for lane in range(loop.n_lanes)])
    assert abs(int(applied.sum())) < loop.n_lanes          # floor of the mean only
    assert np.ptp(applied) >> loop.pi_sh >= 1               # at least a PI code apart


@needs_jit
def test_skew_trims_converge_as_the_float_ones_do():
    """Offset, gain and skew together: the bit-true loop lands within 0.4 dB
    of the float kernel (measured 0.15-0.40 dB over 2^-7..2^-9) and its trims
    track the drawn skews (differences only: the common part is the CDR's)."""
    n = 200_000
    mm = dict(skew_sigma_ui=0.04, **MISMATCH)
    cm = ChannelModel.from_config(_cfg())
    cfg = _cfg(n, 3 * n // 4, cal=_bg(2.0 ** -10, 2.0 ** -8), **mm)
    fl = run_time_link(cfg, channel=cm)
    fx = run_time_link(_fixed(cfg), channel=cm)
    og = run_time_link(_fixed(_cfg(n, 3 * n // 4, cal=_bg(2.0 ** -10), **mm)), channel=cm)
    assert abs(fx.slicer_snr_db - fl.slicer_snr_db) < 0.4, (fx.slicer_snr_db, fl.slicer_snr_db)
    assert fx.slicer_snr_db > og.slicer_snr_db + 0.5, (fx.slicer_snr_db, og.slicer_snr_db)
    loop, ts = fx.extras["fixed"]["loop"], np.asarray(fx.extras["fixed"]["record"]["cal"][3])
    a = fx.extras["adc"]
    true = (a.skews - a.skews.mean()) / 16                  # UI (osr 16)
    trim = (ts - ts.mean()) * 2.0 ** -loop.ph_frac
    assert np.corrcoef(true, trim)[0, 1] > 0.9
    assert np.std(true - trim) < 0.5 * np.std(true)
