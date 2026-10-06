"""Bit-true ADC back end with its CDR loop (``dsp/fixed_loop.py``, ROADMAP P1 #1).

What has to hold for the loop to be a golden model:

* the digital part replayed from the recorded ADC words reproduces the
  closed loop exactly -- PI codes, slicer values, decisions -- so the RTL can
  be checked on that stream (``rtl/adc_dsp_loop.sv``, test_rtl_lockstep);
* an independent integer implementation agrees on arbitrary words and
  parameters, right shifts, clamp and latency included;
* JIT and pure Python agree bit for bit;
* with every word wide it is the float receiver, CDR included.
"""

import dataclasses

import numpy as np
import pytest

import halo_serdes.dsp.fixed_loop as fxl
import halo_serdes.engine.timedomain as td
from halo_serdes.cdr.adc_kernel import adc_rx
from halo_serdes.config import LinkConfig, PrConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig,
    NumericConfig, QFormat, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.dsp.fixed_loop import (
    FixedLoop, build_fixed_loop, float_equivalent_gains, replay_digital, run_fixed_loop,
)
from halo_serdes.engine import run_time_link

jit_only = pytest.mark.skipif(fxl._fixed_loop_digital is fxl._fixed_loop_digital_py,
                              reason="compares the JIT with the pure-Python loop")


def _cfg(n=12_000, pd_input="ffe", **numeric):
    return LinkConfig(
        modulation="pam4", symbol_rate=106.25e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.12, rdc=5.0, r_skin=2.0e-3,
                              loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, fullscale=0.6, offset_sigma=2e-3,
                                  gain_sigma=0.01, skew_sigma_ui=0.01),
                    ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                    dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=3, ki_shift=7, clamp=0.02,
                                  loop_latency_symbols=32, pd_input=pd_input),
                    clock=ClockConfig(sj_ui=0.12, sj_freq=106.25e9 / 1500),
                    noise_rms=0.004),
        numeric=NumericConfig(**numeric),
        sim=SimConfig(n_symbols=n, seed=3, pattern="prbs13q"))


def _captured(cfg, monkeypatch):
    """Run the float link and keep what the receiver kernel was handed."""
    cap = {}
    orig = td._run_rx

    def spy(run, rx, c, progress):
        cap.update(run=run, y=rx, pos0=float(run.fs[0]))
        return orig(run, rx, c, progress)

    monkeypatch.setattr(td, "_run_rx", spy)
    res = run_time_link(cfg)
    return res, cap


@pytest.mark.parametrize("pd_input", ["ffe", "adc"])
def test_replay_from_the_words_is_the_closed_loop(pd_input):
    res = run_time_link(_cfg(pd_input=pd_input, mode="fixed"))
    fx = res.extras["fixed"]
    rec = fx["record"]
    assert np.ptp(rec["pi"]) >= 3                    # the loop does move
    rp = replay_digital(fx["loop"], rec["xin"])
    assert np.array_equal(rp["pi"], rec["pi"])
    assert np.array_equal(rp["dec"], rec["dec"])
    assert np.array_equal(rp["v_out"], rec["v_out"])


def _reference(xin, p):
    """Plain-integer reference written the other way round: the datapath
    over the whole stream first (it never reads the phase), then the phase
    detector and loop filter as a second pass over its outputs."""
    xin = [int(v) for v in xin]
    n = len(xin)
    hi, lo = (1 << (p["out_bits"] - 1)) - 1, -(1 << (p["out_bits"] - 1))

    def rnd(v, s):
        return (v + (1 << (s - 1)) if s > 0 else v) >> s

    def sh(v, s):
        return v << s if s >= 0 else v >> -s

    dec, vout = [], []
    for s in range(n - p["n_pre"]):
        k = s + p["n_pre"]
        acc = sum(int(w) * xin[k - i] for i, w in enumerate(p["wf"]) if k - i >= 0)
        acc = min(hi, max(lo, rnd(acc, p["ffe_shift"])))
        fb = sum(int(w) * int(p["levels"][dec[s - 1 - d]])
                 for d, w in enumerate(p["wd"]) if s - 1 - d >= 0)
        v = min(hi, max(lo, acc - rnd(fb, p["dfe_shift"])))
        vout.append(v)
        dec.append(min(range(len(p["levels"])), key=lambda m: (abs(v - int(p["levels"][m])), m)))

    src = vout if p["pd_ffe"] else xin
    queue = [0] * (p["lat"] + 1)
    ph = integ = corr = acc = cnt = qi = 0
    pis = []
    for k in range(n):
        pis.append(ph >> p["pi_sh"])
        s = k - p["n_pre"]
        if s >= 2:
            x0, x1, x2 = src[s - 2], src[s - 1], src[s]
            acc += x1 * ((1 if x2 > 0 else -1) - (1 if x0 > 0 else -1))
            cnt += 1
            if cnt == p["lanes"]:
                pd = -acc - p["pd_off"]
                integ += sh(pd, p["ki_sh"])
                c = sh(pd, p["kp_sh"]) + integ
                if p["clamp"]:
                    c = max(-p["clamp"], min(p["clamp"], c))
                queue[qi] = c
                qi = (qi + 1) % (p["lat"] + 1)
                corr = queue[qi]
                acc = cnt = 0
        ph += corr >> int(np.log2(p["lanes"]))
    return np.array(pis), np.array(dec), np.array(vout)


@pytest.mark.parametrize("seed", range(4))
def test_digital_back_end_matches_an_independent_reference(seed):
    rng = np.random.default_rng(seed)
    p = {"wf": rng.integers(-60, 200, 9), "wd": rng.integers(-50, 50, 2),
         "ffe_shift": int(rng.integers(0, 5)), "dfe_shift": 8,
         "levels": np.array([-600, -200, 200, 600]), "out_bits": 12, "n_pre": 3,
         "pd_ffe": int(seed % 2), "kp_sh": int(rng.integers(-6, 6)),
         "ki_sh": int(rng.integers(-12, 0)), "clamp": int([0, 5000][seed // 2]),
         "pd_off": int(rng.integers(-50, 50)), "lanes": 8, "lat": int(seed % 3),
         "pi_sh": 10}
    fl = FixedLoop(wf=p["wf"].astype(np.int64), wd=p["wd"].astype(np.int64),
                   ffe_shift=p["ffe_shift"], dfe_shift=8, levels_out=p["levels"],
                   out_bits=12, do_round=1, n_pre=3, pd_ffe=p["pd_ffe"], kp_sh=p["kp_sh"],
                   ki_sh=p["ki_sh"], clamp_int=p["clamp"], pd_off=p["pd_off"], n_lanes=8,
                   lane_shift=3, lat=p["lat"], pi_bits=6, ph_frac=16, in_lsb=1.0,
                   out_lsb=1.0, pd_lsb=1.0)
    xin = 2 * rng.integers(-128, 128, 3_000) + 1
    got = replay_digital(fl, xin)
    pi, dec, vout = _reference(xin, p)
    assert np.array_equal(got["dec"], dec)
    assert np.array_equal(got["v_out"], vout)
    assert np.array_equal(got["pi"], pi)


@jit_only
def test_jit_and_python_loops_agree(monkeypatch):
    cfg = _cfg(6_000, mode="fixed")
    res, cap = _captured(cfg, monkeypatch)
    fl = res.extras["fixed"]["loop"]
    run, adc = cap["run"], res.extras["adc"]
    args = (fl, cap["y"], cfg.osr, cap["pos0"], run.n_symbols, adc, run.params[7], run.params[22])
    jit = run_fixed_loop(*args)
    monkeypatch.setattr(fxl, "_fixed_loop_closed", fxl._fixed_loop_closed_py)
    monkeypatch.setattr(fxl, "_digital_step", fxl._digital_step_py)
    monkeypatch.setattr(fxl, "_farrow_fx", fxl._farrow_py)
    monkeypatch.setattr(fxl, "_ashift", fxl._ashift_py)
    py = run_fixed_loop(*args)
    for k in ("xin", "pi", "phase", "dec", "v_out"):
        assert np.array_equal(jit[k], py[k]), k


def test_wide_words_are_the_float_receiver(monkeypatch):
    """Weights, slicer word, PI and phase register all wide: the loop is the
    float ADC kernel run with the gains it implements (frozen weights)."""
    cfg = _cfg(8_000, ffe_weight=QFormat(32, 26), dfe_weight=QFormat(32, 26),
               pi_bits=16, phase_frac_bits=24)
    res, cap = _captured(cfg, monkeypatch)
    run, adc = cap["run"], res.extras["adc"]
    fl = build_fixed_loop(cfg, res.ffe_taps, res.dfe_taps, res.extras["levels"],
                          adc.q_step, out_bits=44)
    noise, rx_clk = run.params[7], run.params[22]
    fx = run_fixed_loop(fl, cap["y"], cfg.osr, cap["pos0"], run.n_symbols, adc, noise, rx_clk)

    kp, ki, clamp, pd_off = float_equivalent_gains(fl, cfg.osr)
    n = run.n_symbols
    fl_out = adc_rx(
        cap["y"], cfg.osr, cap["pos0"], n, fl.levels_out * fl.out_lsb, adc.n_lanes,
        adc.offsets, adc.gains, adc.skews, adc.q_step, adc.code_max, noise,
        fl.wf * 2.0 ** -cfg.numeric.ffe_weight.fl, fl.n_pre, 0.0,
        fl.wd * 2.0 ** -cfg.numeric.dfe_weight.fl, 0.0, kp, ki, clamp, pd_off, fl.pd_ffe,
        fl.lat, np.full(n, -1, dtype=np.int64), 0, n, rx_clk, 0.0, 0, np.zeros(1), 0.0,
        np.zeros(2), 0.0, 1)
    dec_f, y_f, phase_f = fl_out[0], fl_out[1], fl_out[2]
    m = min(fx["phase"].size, phase_f.size)
    assert np.max(np.abs(fx["phase"][:m] - phase_f[:m])) < 1e-3 * cfg.osr
    nd = fx["n_dec"]
    assert np.mean(fx["dec"] != dec_f[:nd]) < 1e-3
    # the same ADC words, except where the 2^-16 UI PI step tips a sample
    # over a quantiser threshold -- and so the same slicer values
    words_f = np.round(2.0 * fl_out[6] / adc.q_step).astype(np.int64)
    assert np.mean(fx["xin"][:m] != words_f[:m]) < 1e-3
    dv = np.abs(fx["v_out"] * fl.out_lsb - y_f[:nd])
    assert np.mean(dv > 1e-6) < 5e-3, np.mean(dv > 1e-6)


def test_default_words_track_the_float_link():
    """At the configured (DragonPHY-like) widths the bit-true loop is the
    same link: its error rate sits with the float run's."""
    f = run_time_link(_cfg(20_000))
    x = run_time_link(_cfg(20_000, mode="fixed"))
    assert x.ber.n_checked == f.ber.n_checked
    e1, e2 = f.ber.n_errors, x.ber.n_errors
    assert abs(e1 - e2) <= 3 * np.sqrt(e1 + e2) + 5, (e1, e2)
    assert abs(x.slicer_snr_db - f.slicer_snr_db) < 0.5


@pytest.mark.parametrize("what", ["mixed_signal", "pr", "stream", "lanes"])
def test_what_the_loop_does_not_model_is_refused(what):
    cfg = _cfg(4_000, mode="fixed")
    if what == "mixed_signal":
        cfg = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, arch="mixed_signal"))
        exc = ValueError
    elif what == "pr":
        cfg = dataclasses.replace(cfg, pr=PrConfig(target=(1.0, 0.5)))
        exc = NotImplementedError
    elif what == "stream":
        cfg = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, stream=True))
        exc = ValueError
    else:
        cfg = dataclasses.replace(cfg, rx=dataclasses.replace(
            cfg.rx, adc=dataclasses.replace(cfg.rx.adc, n_lanes=12)))
        exc = ValueError
    with pytest.raises(exc):
        run_time_link(cfg)
