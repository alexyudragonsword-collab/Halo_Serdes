"""Receive-side partial response (1 + aD): the FFE target, the kernel's
decision and LMS reference, the detector, the CDR, the statistical engine
and the configuration's limits -- each against a closed form or a direct
comparison, not a smoke run."""

import dataclasses

import numpy as np
import pytest

from halo_serdes.cdr.adc_kernel import _adc_rx_py, adc_rx
from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig, PrConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig, MlsdConfig,
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.mapping import precode_1plusd, unprecode_1plusd
from halo_serdes.dsp.ffe import equalized_cursors, mmse_ffe, zf_ffe
from halo_serdes.dsp.mlsd import mlse_gain_over_dfe_db, viterbi_mlsd
from halo_serdes.engine import run_time_link
from halo_serdes.engine.statistical import run_statistical


# Engine-level runs of 60k-600k symbols take 2-6 minutes each through the
# pure-Python kernel. They check the PR model, not the fallback, so they run
# where numba does; with HALO_NO_JIT=1 the kernel's own equivalence is
# test_numba_kernel_matches_python_with_pr's job.
needs_jit = pytest.mark.skipif(adc_rx is _adc_rx_py,
                               reason="engine-level PR run; minutes through the pure-Python kernel")


# ------------------------------------------------------------ configuration

def test_config_limits():
    adc = RxConfig(arch="adc_dsp")
    assert not LinkConfig().pr.active and LinkConfig().pr.alpha == 0.0
    assert LinkConfig(rx=adc, pr=PrConfig(target=(1.0, 0.5))).pr.alpha == 0.5
    assert PrConfig(target=(1.0, 0.5), at="tx").at_tx
    assert not PrConfig(target=(1.0,), at="tx").at_tx            # nothing to shape
    for bad in ((0.9, 0.5), (1.0, 0.5, 0.2), (1.0, 1.5), ()):
        with pytest.raises(ValueError):
            PrConfig(target=bad)
    with pytest.raises(ValueError, match="mixed_signal"):
        LinkConfig(pr=PrConfig(target=(1.0, 0.5)))           # default arch is mixed-signal


def test_engines_without_a_detector_refuse_rather_than_mislead():
    from halo_serdes.dsp.fixed_datapath import run_fixed_datapath
    from halo_serdes.config.schema import NumericConfig
    from halo_serdes.engine import run_static_link

    cfg = _link(0.15, 0.5, n_sym=20_000)
    with pytest.raises(NotImplementedError, match="static engine"):
        run_static_link(cfg)
    with pytest.raises(NotImplementedError, match="delta target"):
        run_fixed_datapath(np.zeros(16, dtype=np.int64), np.array([1.0]), np.zeros(0),
                           np.array([-1.0, 1.0]), 0, NumericConfig(), 1.0, 8, pr=cfg.pr)
    with pytest.warns(UserWarning, match="sequence detector"):
        run_time_link(dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, mlsd=MlsdConfig())))


# ------------------------------------------------------------ FFE solvers

@pytest.mark.parametrize("alpha", [0.25, 0.5, 1.0])
def test_zf_ffe_equalises_to_the_target(alpha):
    """ZF onto [1, a]: equalised cursors [1, a, ~0 ...], residual under 1 %."""
    c = np.array([0.05, 1.0, 0.45, 0.2, 0.08, 0.03])
    w = zf_ffe(c, 1, 15, 3, target=(1.0, alpha))
    eq, pre = equalized_cursors(c, w, 1, 3)
    shaped = eq / eq[pre]
    assert shaped[pre + 1] == pytest.approx(alpha, abs=0.01)
    window = np.delete(shaped[pre - 3: pre + 12], [3, 4])          # all but main and alpha
    assert np.max(np.abs(window)) < 0.01, shaped
    # equalized_cursors(target=...) is the ISI the target does not account for
    resid, _ = equalized_cursors(c, w, 1, 3, target=(1.0, alpha))
    assert np.max(np.abs(resid[pre - 3: pre + 12])) < 0.01 * abs(eq[pre])


def test_delta_target_solvers_are_unchanged():
    c = np.array([0.05, 1.0, 0.45, 0.2, 0.08, 0.03])
    assert np.array_equal(zf_ffe(c, 1, 15, 3), zf_ffe(c, 1, 15, 3, target=(1.0,)))
    assert np.array_equal(mmse_ffe(c, 1, 15, 3, 1e-3), mmse_ffe(c, 1, 15, 3, 1e-3, target=(1.0,)))
    eq0, _ = equalized_cursors(c, zf_ffe(c, 1, 15, 3), 1, 3)
    eq1, _ = equalized_cursors(c, zf_ffe(c, 1, 15, 3), 1, 3, target=None)
    assert np.array_equal(eq0, eq1)


# ------------------------------------------------------------ detector closed forms

def test_viterbi_on_duobinary_gains_the_mlse_bound():
    """NRZ through a pure [1, 1] channel + AWGN, detected by Viterbi: its
    minimum distance is 3.01 dB more than a memoryless slicer's
    (mlse_gain_over_dfe_db). Every alternating error event of length L has
    that distance and needs the data to match it (probability 2^-L, two
    signs), so the union bound is BER = sum_L L 2^(1-L) Q(.) = 4 Q(.); the
    gain is read after taking that multiplicity out."""
    from scipy.special import erfcinv

    rng = np.random.default_rng(7)
    n, sigma = 2_000_000, 0.42
    levels = np.array([-1.0, 1.0])
    x = rng.integers(0, 2, n)
    a = levels[x]
    y = a + np.concatenate([[0.0], a[:-1]]) + rng.normal(scale=sigma, size=n)
    dec = viterbi_mlsd(y, levels, np.array([1.0, 1.0]))
    ber = float(np.mean(dec[100:-100] != x[100:-100]))
    assert ber > 1e-4, ber                                    # enough errors to read
    # the memoryless slicer (half-distance 1) reaching Q(.) = ber / 4 needs this noise
    sigma_eq = 1.0 / (np.sqrt(2.0) * erfcinv(2.0 * ber / 4.0))
    gain_db = 20.0 * np.log10(sigma / sigma_eq)
    assert abs(gain_db - mlse_gain_over_dfe_db([1.0, 1.0])) < 0.5, (gain_db, ber)


def _kernel_on_composites(user, alpha, precode, sigma, seed=3, osr=16):
    """Drive the ADC kernel with a waveform that already is the 1 + aD
    composite (no channel, unit FFE): what is left is its per-symbol
    decision. Returns (user-domain decisions, user symbols)."""
    n_lv = 4
    levels = np.array([-3.0, -1.0, 1.0, 3.0]) / 3.0
    line = precode_1plusd(user, n_lv) if precode else user
    v = levels[line] + alpha * np.concatenate([[0.0], levels[line[:-1]]])
    v = v + np.random.default_rng(seed).normal(scale=sigma, size=v.size)
    y = np.concatenate([np.zeros(4 * osr), np.repeat(v, osr), np.zeros(4 * osr)])
    n_sym = user.size - 8
    ref = np.full(n_sym, -1, dtype=np.int64)
    ref[:50] = line[:50]
    comp = np.array([np.mean([levels[i] + levels[q - i]
                              for i in range(max(0, q - n_lv + 1), min(q, n_lv - 1) + 1)])
                     for q in range(2 * n_lv - 1)])
    mode = 2 if (precode and alpha == 1.0) else 1
    dec, *_ = adc_rx(y, osr, 4.0 * osr + osr / 2, n_sym, levels, 4, np.zeros(4), np.ones(4),
                     np.zeros(4), 6.0 / 4096, 2047, np.zeros(n_sym + 8), np.array([1.0]), 0, 0.0,
                     np.zeros(0), 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, ref, 50, 50, np.zeros(n_sym),
                     float(alpha), mode, comp if mode == 2 else np.zeros(1))
    out = unprecode_1plusd(dec, n_lv) if precode else dec
    return out, user[: out.size]


def _error_runs(err: np.ndarray) -> np.ndarray:
    """Lengths of the runs of consecutive symbol errors."""
    e = np.concatenate([[0], err.astype(int), [0]])
    starts, ends = np.flatnonzero(np.diff(e) == 1), np.flatnonzero(np.diff(e) == -1)
    return ends - starts


def test_precoded_duobinary_decisions_do_not_propagate():
    """1 + D, per-symbol decision. Without precoding the decision subtracts
    the previous one, so one error starts a burst; precoded, the composite
    is sliced and taken mod N, so one composite error is one user error and
    errors land like independent ones: adjacent pairs only as often as two
    independent errors fall side by side (~2p of the runs)."""
    user = np.random.default_rng(11).integers(0, 4, 200_000)
    plain, ref_p = _kernel_on_composites(user, 1.0, False, sigma=0.12)
    pre, ref_q = _kernel_on_composites(user, 1.0, True, sigma=0.12)
    runs_plain = _error_runs(plain[100:] != ref_p[100:])
    runs_pre = _error_runs(pre[100:] != ref_q[100:])
    assert runs_pre.size > 50 and runs_plain.size > 5
    p = (pre[100:] != ref_q[100:]).mean()
    assert np.mean(runs_pre > 1) < 3.0 * p, (np.bincount(runs_pre), p)
    assert np.mean(runs_plain) > 3.0 * np.mean(runs_pre), (np.mean(runs_plain), np.mean(runs_pre))


@pytest.mark.parametrize("mode_args", [(0.5, 1, None), (1.0, 2, "composite")])
def test_numba_kernel_matches_python_with_pr(mode_args):
    if adc_rx is _adc_rx_py:
        pytest.skip("numba not active")
    alpha, mode, comp = mode_args
    user = np.random.default_rng(2).integers(0, 4, 6_000)
    osr, levels = 16, np.array([-1.0, -1 / 3, 1 / 3, 1.0])
    v = levels[user] + alpha * np.concatenate([[0.0], levels[user[:-1]]])
    v = v + np.random.default_rng(3).normal(scale=0.05, size=v.size)
    y = np.concatenate([np.zeros(4 * osr), np.repeat(v, osr), np.zeros(4 * osr)])
    n_sym = 5_900
    ref = np.full(n_sym, -1, dtype=np.int64)
    ref[:500] = user[:500]
    pr_levels = (np.array([2 * levels[0] + q * (levels[1] - levels[0]) for q in range(7)])
                 if comp else np.zeros(1))
    args = (y, osr, 4.0 * osr + osr / 2, n_sym, levels, 4, np.zeros(4), np.ones(4),
            np.array([0.0, 0.2, -0.1, 0.05]), 2.0 / 4096, 2047, np.zeros(n_sym + 8),
            np.array([0.02, 1.0, -0.05]), 1, 1e-4, np.array([0.05]), 1e-4,
            osr / 128, osr / 8192, 0.0, 0.0, 1, 0, ref, 500, 200, np.zeros(n_sym),
            float(alpha), mode, pr_levels)
    a, b = adc_rx(*args), _adc_rx_py(*args)
    for x, z in zip(a, b):
        np.testing.assert_allclose(np.asarray(x, dtype=float), np.asarray(z, dtype=float),
                                   rtol=0, atol=1e-12)


# ------------------------------------------------------------ the link

def _link(length_m, alpha, *, n_sym=200_000, precode=False, noise=0.0015, enob=6.5,
          mlsd="viterbi", seed=3):
    """Example 18's 224 Gb/s PAM4 LR receiver (21-tap FFE, MM-CDR)."""
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16, precode=precode,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0, r_skin=2.0e-3,
                              loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=10 if enob is None else 8, n_lanes=16, enob=enob,
                                  fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=14, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0), mlsd=MlsdConfig(kind=mlsd, memory=2),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=n_sym, seed=seed, pattern="prbs13q"),
        pr=PrConfig(target=(1.0,) if alpha == 0 else (1.0, alpha)))


def _mean_phase_ui(res, cfg) -> float:
    ph = np.asarray(res.extras["phase_track"])
    st = res.extras["settle"]
    return float(np.mean(ph[st:] - ph[0] - np.arange(st, ph.size) * cfg.osr)) / cfg.osr


@pytest.mark.parametrize("alpha,precode", [(0.5, False), (1.0, False), (1.0, True)])
def test_mm_cdr_holds_its_phase_under_a_pr_target(alpha, precode):
    """The detector reads the sample less alpha times the previous decision,
    so a shaped pulse does not move the lock point: within 0.05 UI of the
    delta-target run on the same link (on the shaped sample itself it
    walked 0.4-3 UI at alpha 0.75-1)."""
    base = _link(0.22, 0.0, n_sym=60_000)
    shaped = _link(0.22, alpha, n_sym=60_000, precode=precode)
    ph0 = _mean_phase_ui(run_time_link(base), base)
    ph1 = _mean_phase_ui(run_time_link(shaped), shaped)
    assert abs(ph1 - ph0) < 0.05, (ph0, ph1)


@needs_jit
@pytest.mark.parametrize("alpha", [0.5, 1.0])
def test_invariant3_with_a_pr_target(alpha):
    """LTI + AWGN + receive PR + Viterbi: statistical (union bound over the
    alternating error events of [1, alpha, r...] in the FFE-coloured noise,
    pr_error_events) and time-domain BER within 2x. The operating point is
    BER ~1e-4: a union bound is loose above ~1e-3 (3x at 3e-2, alpha 0.5).
    No ADC excess noise -- the statistical engine does not model ENOB
    (ROADMAP 4b)."""
    cfg = _link(0.24, alpha, n_sym=600_000, noise=0.0022, enob=None, precode=alpha == 1.0)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 50, mc.ber.n_errors
    st = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = st.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (alpha, st.ber, mc.ber.ber, ratio)


@needs_jit
def test_pr_helps_on_a_lossy_channel_and_costs_nothing_on_a_short_one():
    """Direction only (example 36 has the numbers): at -33 dB the best alpha
    is above 0 and beats the delta target + MLSD; at -23 dB no alpha is
    worse than the delta target by more than the count's noise."""
    def ber(length_m, alpha):
        return run_time_link(_link(length_m, alpha, n_sym=150_000)).ber

    deep = {a: ber(0.22, a).ber for a in (0.0, 0.5)}
    assert deep[0.5] < 0.2 * deep[0.0], deep
    short = {a: ber(0.15, a) for a in (0.0, 0.5)}
    assert short[0.5].n_errors <= short[0.0].n_errors + 10, (short[0.0].n_errors, short[0.5].n_errors)


# ------------------------------------------------------------ transmit side

def _tx_pr(cfg, alpha, swing=None, ideal_adc=False):
    cfg = dataclasses.replace(cfg, pr=PrConfig(target=(1.0, alpha), at="tx"))
    if swing is not None:
        cfg = dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, swing=swing))
    if ideal_adc:
        cfg = dataclasses.replace(cfg, rx=dataclasses.replace(
            cfg.rx, adc=dataclasses.replace(cfg.rx.adc, n_bits=12, enob=None, fullscale=1.2)))
    return cfg


@pytest.mark.parametrize("alpha", [0.5, 1.0])
def test_tx_pr_filter_is_peak_normalised_and_in_the_symbol_response(alpha):
    """(x_k + a x_{k-1}) / (1 + a): the composite peak is the unshaped one,
    the DC gain is unchanged, and the receivers' pulse analysis sees it."""
    from halo_serdes.tx.pipeline import TxPipeline

    cfg = _tx_pr(_link(0.15, alpha, n_sym=1000), alpha)
    pipe, plain = TxPipeline.from_config(cfg), TxPipeline.from_config(_link(0.15, 0.0, n_sym=1000))
    x = np.random.default_rng(1).integers(0, 4, 400)
    lv = pipe.levels(x)
    shaped = pipe.pr_filter(lv)
    assert np.allclose(shaped[1:], (lv[1:] + alpha * lv[:-1]) / (1 + alpha))
    assert np.abs(shaped).max() <= np.abs(lv).max() + 1e-12
    resp, ref = pipe.equivalent_symbol_response(), plain.equivalent_symbol_response()
    assert resp.sum() == pytest.approx(ref.sum())                 # sum(pr_taps) = 1
    assert np.array_equal(pipe.equivalent_symbol_response(shaping=False), ref)
    assert pipe.symbol_response_lead() == plain.symbol_response_lead()
    # receive-side PR leaves the Tx untouched
    rx_pipe = TxPipeline.from_config(_link(0.15, alpha, n_sym=1000))
    assert rx_pipe.pr_taps is None and np.array_equal(rx_pipe.pr_filter(lv), lv)


@needs_jit
@pytest.mark.parametrize("alpha", [0.5, 1.0])
def test_tx_pr_is_rx_pr_moved_plus_its_peak_cost(alpha):
    """Linear chain, noise at the receiver: shaping in the Tx does not change
    what the receive FFE must invert (the channel, down to a delta), so
    without the peak limit (swing x (1 + a)) it lands within ~2.5 dB of the
    receive-side target; peak-normalised it loses up to 20 log10(1 + a) more
    (all of it where the slicer SNR is noise-limited)."""
    rx = _link(0.24, alpha, n_sym=60_000, enob=None)
    rx = dataclasses.replace(rx, rx=dataclasses.replace(
        rx.rx, adc=dataclasses.replace(rx.rx.adc, n_bits=12, fullscale=1.2)))
    snr_rx = run_time_link(rx).slicer_snr_db
    snr_free = run_time_link(_tx_pr(rx, alpha, swing=1.0 + alpha)).slicer_snr_db
    snr_peak = run_time_link(_tx_pr(rx, alpha)).slicer_snr_db
    assert 0.0 <= snr_rx - snr_free < 3.0, (snr_rx, snr_free)
    # at most the full 20 log10(1 + a): residual ISI does not shrink with the swing
    cost = 20 * np.log10(1 + alpha)
    assert cost - 1.5 < snr_free - snr_peak < cost + 0.5, (snr_free, snr_peak)


def test_tx_pr_main_cursor_is_the_symbols_own():
    """At a = 1 the shaped pulse has two equal cursors and its peak can be the
    a x_{k-1} one; the receiver locates x_k on the unshaped pulse. Located by
    argmax this link reads 1.5e-2, located on the unshaped pulse 3.7e-3."""
    res = run_time_link(_tx_pr(_link(0.20, 1.0, n_sym=40_000, precode=True), 1.0))
    assert res.ber.ber < 7e-3, res.summary()


@needs_jit
def test_invariant3_with_a_tx_pr_target():
    """Precoded duobinary from the Tx, Viterbi at the receiver: statistical
    within 2x of the time engine. (a = 0.5 from the Tx is 2.5-2.9x
    pessimistic: with lag-1 noise correlation -0.67 the alternating error
    events of every length sit at nearly the same distance and the union
    bound counts the nested ones several times -- ROADMAP P3 #8.)"""
    cfg = _tx_pr(_link(0.20, 1.0, n_sym=400_000, noise=0.0022, enob=None, precode=True), 1.0)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    st = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    assert 0.5 < st.ber / mc.ber.ber < 2.0, (st.ber, mc.ber.ber)

