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
    MlsdConfig, NumericConfig, QFormat, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.dsp.fixed_loop import (
    FixedLoop, build_fixed_loop, float_equivalent_gains, float_equivalent_mu, replay_digital,
    run_fixed_loop,
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
        cap.update(run=run, y=rx, pos0=float(run.fs[0]), fs0=run.fs.copy())
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
    rp = replay_digital(fx["loop"], rec["xin"], rec["ref"])
    assert np.array_equal(rp["wf"], rec["wf"]) and np.array_equal(rp["wd"], rec["wd"])
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

    g = p["g"]
    wacc_f = [int(w) << g for w in p["wf"]]
    wacc_d = [int(w) << g for w in p["wd"]]

    def clip_acc(a, lo_w, hi_w):
        return max(lo_w << g, min((hi_w << g) + (1 << g) - 1, a))

    L = [int(x) for x in p["levels"]]
    mode, nt = p["pr_mode"], (p["pr_nt"] if p["pr_mode"] else 1)
    a_acc, b_acc, c_acc = p["a"] << g, p["b"] << g, p["c"] << g

    def nearest(x, lv):
        return min(range(len(lv)), key=lambda m: (abs(x - int(lv[m])), m))

    dec, xl, vout, rout = [], [], [], []
    for s in range(n - p["n_pre"]):
        k = s + p["n_pre"]
        wf = [a >> g for a in wacc_f]
        wd = [a >> g for a in wacc_d]
        acc = sum(w * xin[k - i] for i, w in enumerate(wf) if k - i >= 0)
        acc = min(hi, max(lo, rnd(acc, p["ffe_shift"])))
        fb = sum(w * L[xl[s - nt - d]] for d, w in enumerate(wd) if s - nt - d >= 0)
        v = min(hi, max(lo, acc - rnd(fb, p["dfe_shift"])))
        vout.append(v)
        prev = L[xl[s - 1]] if mode and s >= 1 else 0
        prev2 = L[xl[s - 2]] if mode and nt >= 3 and s >= 2 else 0
        prev3 = L[xl[s - 3]] if mode and nt == 4 and s >= 3 else 0
        ctl = (rnd((a_acc >> g) * prev + (b_acc >> g) * prev2 + (c_acc >> g) * prev3, p["pfl"])
               if mode else 0)
        rout.append(v - ctl)
        q = nearest(v, p["pr_lv"]) if mode == 2 else 0
        best = (q - (dec[s - 1] if s else 0)) % len(L) if mode == 2 else nearest(v - ctl, L)
        training = s < p["train"] and p["ref"][s] >= 0
        if training:
            dec.append(int(p["ref"][s]))
            xl.append(int(p["ref"][s]))
        else:
            dec.append(best)
            xl.append(min(len(L) - 1, max(0, q - (xl[s - 1] if s else 0))) if mode == 2 else best)
        if s >= p["adapt"] and (p["lms_f"] or p["lms_d"]):
            if mode == 0:
                e = v - L[dec[s]]
            elif mode == 2 and not training:
                e = v - int(p["pr_lv"][q])
            else:
                e = v - (L[xl[s]] + ctl)
                if p["lms_a"] and mode == 1:
                    a_acc = max(0, min((p["a_max"] << g) + (1 << g) - 1,
                                       a_acc + rnd(e * prev, -p["sh_a"])))
                    if nt >= 3:
                        b_acc = max(-(1 << p["pfl"]) << g,
                                    min((p["b_max"] << g) + (1 << g) - 1,
                                        b_acc + rnd(e * prev2, -p["sh_a"])))
                    if nt == 4:
                        c_acc = max(-(1 << p["pfl"]) << g,
                                    min(((1 << p["pfl"]) << g) + (1 << g) - 1,
                                        c_acc + rnd(e * prev3, -p["sh_a"])))
            if p["lms_f"]:
                wacc_f = [clip_acc(a - rnd(e * xin[k - i], -p["sh_f"]), *p["lim"])
                          if k - i >= 0 else a for i, a in enumerate(wacc_f)]
            if p["lms_d"]:
                wacc_d = [clip_acc(a + rnd(e * L[xl[s - nt - d]], -p["sh_d"]), *p["lim"])
                          if s - nt - d >= 0 else a for d, a in enumerate(wacc_d)]

    src = (rout if mode else vout) if p["pd_ffe"] else xin
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
    return (np.array(pis), np.array(dec), np.array(vout),
            np.array([a >> g for a in wacc_f]), np.array([a >> g for a in wacc_d]),
            np.array([a_acc >> g, b_acc >> g, c_acc >> g]))


@pytest.mark.parametrize("pr", ["delta", "pr2", "pr3", "pr4", "precoded"])
@pytest.mark.parametrize("seed", range(4))
def test_digital_back_end_matches_an_independent_reference(seed, pr):
    rng = np.random.default_rng(seed)
    p = {"wf": rng.integers(-60, 200, 9), "wd": rng.integers(-50, 50, 2),
         "ffe_shift": int(rng.integers(0, 5)), "dfe_shift": 8,
         "levels": np.array([-600, -200, 200, 600]), "out_bits": 12, "n_pre": 3,
         "pd_ffe": int(seed % 2), "kp_sh": int(rng.integers(-6, 6)),
         "ki_sh": int(rng.integers(-12, 0)), "clamp": int([0, 5000][seed // 2]),
         "pd_off": int(rng.integers(-50, 50)), "lanes": 8, "lat": int(seed % 3),
         "pi_sh": 10, "train": 400, "adapt": 150, "lms_f": seed != 1, "lms_d": seed != 2,
         "sh_f": -int(rng.integers(3, 9)), "sh_d": -int(rng.integers(3, 9)), "g": 6,
         "lim": (-512, 511)}
    xin = 2 * rng.integers(-128, 128, 3_000) + 1
    p["ref"] = rng.integers(-1, 4, xin.size)
    mode = {"delta": 0, "pr2": 1, "pr3": 1, "pr4": 1, "precoded": 2}[pr]
    p.update(pr_mode=mode, pr_nt={"pr3": 3, "pr4": 4}.get(pr, 2), pfl=8,
             a=int(rng.integers(100, 256)) if mode == 1 else (256 if mode == 2 else 0),
             b=int(rng.integers(-60, 60)) if pr in ("pr3", "pr4") else 0,
             c=int(rng.integers(-40, 40)) if pr == "pr4" else 0,
             lms_a=int(mode == 1 and seed % 2 == 0), sh_a=-int(rng.integers(4, 9)),
             a_max={"pr3": 512, "pr4": 768}.get(pr, 256), b_max=768 if pr == "pr4" else 256,
             pr_lv=np.array([-1200, -800, -400, 0, 400, 800, 1200]))
    fl = FixedLoop(wf=p["wf"].astype(np.int64), wd=p["wd"].astype(np.int64),
                   ffe_shift=p["ffe_shift"], dfe_shift=8, levels_out=p["levels"],
                   out_bits=12, do_round=1, n_pre=3, pd_ffe=p["pd_ffe"], kp_sh=p["kp_sh"],
                   ki_sh=p["ki_sh"], clamp_int=p["clamp"], pd_off=p["pd_off"], n_lanes=8,
                   lane_shift=3, lat=p["lat"], pi_bits=6, ph_frac=16, in_lsb=1.0,
                   out_lsb=1.0, pd_lsb=1.0,
                   lp=np.array([p["train"], p["adapt"], int(p["lms_f"]), p["sh_f"],
                                int(p["lms_d"]), p["sh_d"], p["g"], -512, 511, -512, 511,
                                mode, p["pr_nt"] if mode else 1, p["pfl"], p["lms_a"],
                                p["sh_a"], p["a_max"], -256, p["b_max"], -256, 256],
                               dtype=np.int64),
                   pr_lv=p["pr_lv"].astype(np.int64),
                   pr_ab=np.array([p["a"], p["b"], p["c"]], dtype=np.int64))
    got = replay_digital(fl, xin, p["ref"])
    pi, dec, vout, wf, wd, ab = _reference(xin, p)
    assert np.array_equal(got["ab"], ab)
    assert np.array_equal(got["dec"], dec)
    assert np.array_equal(got["v_out"], vout)
    assert np.array_equal(got["pi"], pi)
    assert np.array_equal(got["wf"], wf) and np.array_equal(got["wd"], wd)
    assert not np.array_equal(wf, p["wf"]) or not p["lms_f"]      # it did adapt


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
    monkeypatch.setattr(fxl, "_rnd_shift", fxl._rnd_shift_py)
    py = run_fixed_loop(*args)
    for k in ("xin", "pi", "phase", "dec", "v_out", "wf", "wd"):
        assert np.array_equal(jit[k], py[k]), k


def test_wide_words_are_the_float_receiver(monkeypatch):
    """Weights, accumulators, slicer word, PI and phase register all wide: the
    loop is the float ADC kernel -- training, LMS on FFE and DFE, CDR -- run
    with the steps and gains it implements."""
    cfg = _cfg(8_000, ffe_weight=QFormat(32, 26), dfe_weight=QFormat(32, 26),
               pi_bits=16, phase_frac_bits=24)
    # steps large enough that the weights travel well beyond the tolerance
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, ffe=dataclasses.replace(cfg.rx.ffe, mu=1e-2),
        dfe=dataclasses.replace(cfg.rx.dfe, mu=1e-2)))
    res, cap = _captured(cfg, monkeypatch)
    run, adc = cap["run"], res.extras["adc"]
    train, settle = res.extras["train_end"], res.extras["settle"]
    fl = build_fixed_loop(cfg, res.extras["w_ffe0"], res.extras["w_dfe0"], res.extras["levels"],
                          adc.q_step, out_bits=44, train_len=train, adapt_start=settle)
    noise, ref, rx_clk = run.params[7], run.params[19], run.params[22]
    fx = run_fixed_loop(fl, cap["y"], cfg.osr, cap["pos0"], run.n_symbols, adc, noise, rx_clk,
                        ref=ref)

    kp, ki, clamp, pd_off = float_equivalent_gains(fl, cfg.osr)
    ffl, dfl = cfg.numeric.ffe_weight.fl, cfg.numeric.dfe_weight.fl
    mu_f, mu_d, _ = float_equivalent_mu(fl, ffl, dfl)
    assert mu_f > 0 and mu_d > 0
    n = run.n_symbols
    fl_out = adc_rx(
        cap["y"], cfg.osr, cap["pos0"], n, fl.levels_out * fl.out_lsb, adc.n_lanes,
        adc.offsets, adc.gains, adc.skews, adc.q_step, adc.code_max, noise,
        fl.wf * 2.0 ** -ffl, fl.n_pre, mu_f, fl.wd * 2.0 ** -dfl, mu_d, kp, ki, clamp, pd_off,
        fl.pd_ffe, fl.lat, ref, train, settle, rx_clk, 0.0, 0, np.zeros(1), 0.0,
        np.zeros(2), 0.0, 1)
    # the weights travelled, and to the same place
    moved = np.max(np.abs(fl_out[3] - fl.wf * 2.0 ** -ffl))
    assert moved > 1e-3
    assert np.max(np.abs(fx["wf"] * 2.0 ** -ffl - fl_out[3])) < 0.02 * moved
    assert np.max(np.abs(fx["wd"] * 2.0 ** -dfl - fl_out[4])) < 0.02 * moved
    dec_f, y_f, phase_f = fl_out[0], fl_out[1], fl_out[2]
    m = min(fx["phase"].size, phase_f.size)
    assert np.max(np.abs(fx["phase"][:m] - phase_f[:m])) < 1e-3 * cfg.osr
    nd = fx["n_dec"]
    assert np.mean(fx["dec"] != dec_f[:nd]) < 1e-3
    # the same ADC words, except where the 2^-16 UI PI step tips a sample
    # over a quantiser threshold -- and so the same slicer values
    words_f = np.round(2.0 * fl_out[6] / adc.q_step).astype(np.int64)
    assert np.mean(fx["xin"][:m] != words_f[:m]) < 1e-3
    # (to 10 uV: the adapting weights differ in their last bits, ~1e-7 V)
    dv = np.abs(fx["v_out"] * fl.out_lsb - y_f[:nd])
    assert np.mean(dv > 1e-5) < 5e-3, np.mean(dv > 1e-5)


_PR_CASES = {
    "pr_lms": dict(pr=PrConfig(target=(1.0, 0.75), adapt="lms", mu=2e-2)),
    "pr3": dict(pr=PrConfig(target=(1.0, 0.6, 0.2))),
    "pr4": dict(pr=PrConfig(target=(1.0, 0.6, 0.2, 0.1))),
    "precoded": dict(pr=PrConfig(target=(1.0, 1.0)), precode=True),
}


@pytest.mark.parametrize("case", sorted(_PR_CASES))
def test_wide_words_are_the_float_pr_receiver(monkeypatch, case):
    """The same with a partial-response target: subtracting the controlled
    cursors (two, three and four cursors, a adapted) and the precoded
    composite slicer."""
    cfg = _cfg(8_000, ffe_weight=QFormat(32, 26), dfe_weight=QFormat(32, 26),
               pi_bits=16, phase_frac_bits=24)
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, mlsd=MlsdConfig(kind="viterbi", memory=1)), **_PR_CASES[case])
    res, cap = _captured(cfg, monkeypatch)
    run, adc = cap["run"], res.extras["adc"]
    P, fs0 = run.params, cap["fs0"]
    pr_mode, pr_levels, mu_a, nt = P[23], P[24], P[25], P[26]
    assert pr_mode == (2 if case == "precoded" else 1)
    train, settle = res.extras["train_end"], res.extras["settle"]
    fl = build_fixed_loop(cfg, res.extras["w_ffe0"], res.extras["w_dfe0"], res.extras["levels"],
                          adc.q_step, out_bits=44, train_len=train, adapt_start=settle,
                          pr_mode=pr_mode, pr_nt=nt, alpha=fs0[4], beta=fs0[5], gamma=fs0[6],
                          pr_levels=pr_levels, mu_alpha=mu_a)
    noise, ref, rx_clk = P[7], P[19], P[22]
    fx = run_fixed_loop(fl, cap["y"], cfg.osr, cap["pos0"], run.n_symbols, adc, noise, rx_clk,
                        ref=ref)
    kp, ki, clamp, pd_off = float_equivalent_gains(fl, cfg.osr)
    ffl, dfl = cfg.numeric.ffe_weight.fl, cfg.numeric.dfe_weight.fl
    levels_q = fl.levels_out * fl.out_lsb
    mu_f, mu_d, mu_a_eq = float_equivalent_mu(fl, ffl, dfl, float(np.mean(res.extras["levels"] ** 2)))
    assert (mu_a_eq > 0) == (case == "pr_lms")
    n = run.n_symbols
    ab_q = fl.pr_ab * 2.0 ** -dfl
    a_out = np.zeros(3)
    fl_out = adc_rx(
        cap["y"], cfg.osr, cap["pos0"], n, levels_q, adc.n_lanes,
        adc.offsets, adc.gains, adc.skews, adc.q_step, adc.code_max, noise,
        fl.wf * 2.0 ** -ffl, fl.n_pre, mu_f, fl.wd * 2.0 ** -dfl, mu_d, kp, ki, clamp, pd_off,
        fl.pd_ffe, fl.lat, ref, train, settle, rx_clk, ab_q[0], pr_mode,
        fl.pr_lv * fl.out_lsb if pr_mode == 2 else np.zeros(1), mu_a_eq, a_out, ab_q[1], nt,
        ab_q[2])
    m = min(fx["phase"].size, fl_out[2].size)
    assert np.max(np.abs(fx["phase"][:m] - fl_out[2][:m])) < 1e-3 * cfg.osr
    nd = fx["n_dec"]
    assert np.mean(fx["dec"] != fl_out[0][:nd]) < 1e-3
    a_end = fx["ab"] * 2.0 ** -dfl
    assert abs(a_end[0] - a_out[0]) < 1e-4, (a_end, a_out)
    if case == "pr_lms":
        assert abs(a_out[0] - ab_q[0]) > 1e-3          # a travelled


@pytest.mark.parametrize("case", sorted(_PR_CASES))
def test_default_words_track_the_float_pr_link(case):
    """(At the default a step. With the wide test's 2e-2, a wanders 0.43 ->
    ~0.32 over 20k symbols in both loops -- the equivalent integer step is
    0.90x -- and the error count follows where each walk happens to be:
    117 float vs 42 fixed on this seed. That is the step, not the words.)"""
    cfg = _cfg(20_000)
    kw = dict(_PR_CASES[case])
    kw["pr"] = dataclasses.replace(kw["pr"], mu=PrConfig().mu)
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, mlsd=MlsdConfig(kind="viterbi", memory=1)), **kw)
    f = run_time_link(cfg)
    x = run_time_link(dataclasses.replace(cfg, numeric=NumericConfig(mode="fixed")))
    assert x.ber.n_checked == f.ber.n_checked
    e1, e2 = f.ber.n_errors, x.ber.n_errors
    assert abs(e1 - e2) <= 3 * np.sqrt(e1 + e2) + 5, (e1, e2)
    assert abs(x.extras["pr_alpha"][1] - f.extras["pr_alpha"][1]) < 0.05


def test_default_words_track_the_float_link():
    """At the configured (DragonPHY-like) widths the bit-true loop is the
    same link: its error rate sits with the float run's."""
    f = run_time_link(_cfg(20_000))
    x = run_time_link(_cfg(20_000, mode="fixed"))
    assert x.ber.n_checked == f.ber.n_checked
    e1, e2 = f.ber.n_errors, x.ber.n_errors
    assert abs(e1 - e2) <= 3 * np.sqrt(e1 + e2) + 5, (e1, e2)
    assert abs(x.slicer_snr_db - f.slicer_snr_db) < 0.5


@pytest.mark.parametrize("what", ["mixed_signal", "stream", "lanes"])
def test_what_the_loop_does_not_model_is_refused(what):
    cfg = _cfg(4_000, mode="fixed")
    if what == "mixed_signal":
        cfg = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, arch="mixed_signal"))
        exc = ValueError
    elif what == "stream":
        cfg = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, stream=True))
        exc = ValueError
    else:
        cfg = dataclasses.replace(cfg, rx=dataclasses.replace(
            cfg.rx, adc=dataclasses.replace(cfg.rx.adc, n_lanes=12)))
        exc = ValueError
    with pytest.raises(exc):
        run_time_link(cfg)
