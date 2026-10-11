"""Reduced-state sequence detection (``rx.mlsd.state_memory``, DFSE).

The trellis keeps K of its L cursors in the state and cancels the rest with
each survivor's own decisions. Pinned here: with K = L it is the full Viterbi
decision for decision; numba and Python agree (invariant 4); the statistical
engine's truncated event distances track the time engine on a 224G link
(invariant 3); and the configuration refuses what it cannot run.
"""

import dataclasses as dc

import numpy as np
import pytest

from halo_serdes.analysis.power import power_report, trellis_states
from halo_serdes.config import LinkConfig, PrConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig,
    MlsdConfig, NumericConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.dsp.mlsd import (
    _dfse_py,
    _viterbi_py,
    dfse_mlsd,
    mlse_min_distance_sq,
    post_detect,
    viterbi_mlsd,
)
from halo_serdes.engine import run_time_link
from halo_serdes.engine.statistical import pr_error_events, run_statistical

LV = np.array([-3.0, -1.0, 1.0, 3.0])
CHANNELS = [np.array([1.0, 0.8, 0.4, 0.1]), np.array([1.0, 1.0, 0.5, 0.2, -0.05]),
            np.array([1.0, 0.6])]


def _noisy(c, n, sigma, seed=1):
    rng = np.random.default_rng(seed)
    sym = rng.integers(0, LV.size, n)
    return sym, np.convolve(LV[sym], c)[:n] + rng.normal(0.0, sigma, n)


# ----------------------------------------------------------------- kernel

# Engine-level runs go through the pure-Python receiver kernel in minutes;
# they check the detector on a link, not the fallback, so they run where
# numba does (test_numba_and_python_agree is the fallback's check).
JIT = dfse_mlsd is not _dfse_py
needs_jit = pytest.mark.skipif(not JIT, reason="engine-level run; minutes without numba")


@pytest.mark.parametrize("c", CHANNELS)
def test_full_state_memory_is_the_viterbi_decision_for_decision(c):
    sym, y = _noisy(c, 2000, 0.45)
    L = c.size - 1
    assert np.array_equal(_dfse_py(y, LV, c, L), _viterbi_py(y, LV, c))
    if JIT:
        sym, y = _noisy(c, 30000, 0.45, seed=2)
        assert np.array_equal(dfse_mlsd(y, LV, c, L), viterbi_mlsd(y, LV, c))


@pytest.mark.parametrize("c", CHANNELS[:2])
def test_numba_and_python_agree(c):
    """Invariant 4: the JIT kernel decides what the Python one does."""
    sym, y = _noisy(c, 2000, 0.45, seed=4)
    for K in range(1, c.size - 1):
        assert np.array_equal(dfse_mlsd(y, LV, c, K), _dfse_py(y, LV, c, K))


def test_noiseless_decisions_are_exact_after_start_up():
    """The tails start at level 0, not at the zeros before the first
    symbol: the first L - K decisions may miss, none after."""
    c = CHANNELS[0]
    sym, _ = _noisy(c, 5000, 0.0)
    y = np.convolve(LV[sym], c)[:sym.size]
    for K in (1, 2):
        d = dfse_mlsd(y, LV, c, K)
        assert np.array_equal(d[c.size:], sym[c.size:])


def test_fewer_states_cost_errors_where_the_dropped_cursors_matter():
    """[1, 0.8, 0.4, 0.1]: feeding back 0.4 and 0.1 (K = 1) costs about 30 %
    more errors, feeding back only 0.1 (K = 2) next to none (0-12 % over
    three seeds: a wrong survivor's feedback can still propagate)."""
    c = CHANNELS[0]
    sym, y = _noisy(c, 20000, 0.45)
    ser = [np.mean(dfse_mlsd(y, LV, c, K) != sym) for K in (1, 2)]
    full = np.mean(viterbi_mlsd(y, LV, c) != sym)
    assert ser[0] > 1.2 * full
    assert full <= ser[1] < 1.15 * full


def test_post_detect_dispatch():
    c = CHANNELS[0]
    _, y = _noisy(c, 4000, 0.45, seed=6)
    full = post_detect(y, c, LV, "viterbi")
    assert np.array_equal(post_detect(y, c, LV, "viterbi", state_memory=3), full)
    assert np.array_equal(post_detect(y, c, LV, "viterbi", state_memory=7), full)
    assert np.array_equal(post_detect(y, c, LV, "viterbi", state_memory=1),
                          dfse_mlsd(y, LV, c, 1))
    with pytest.raises(ValueError, match="state_memory"):
        post_detect(y, c, LV, "viterbi", state_memory=0)


# ------------------------------------------------------- truncated distance

def test_truncated_distance_reaches_the_full_one():
    for c in CHANNELS:
        L = c.size - 1
        d = [mlse_min_distance_sq(c, state_memory=K) for K in range(1, L + 1)]
        assert d[-1] == pytest.approx(mlse_min_distance_sq(c))
        assert all(a <= b + 1e-12 for a, b in zip(d, d[1:]))
    # [1, 0.8, 0.4, 0.1], K = 1: the alternating pair (1, -1) differs by
    # (1, -0.2, -0.4, -0.3, -0.1), of which the first 2 + K samples count
    assert mlse_min_distance_sq(CHANNELS[0], state_memory=1) == pytest.approx(1.2, abs=1e-9)


def test_pr_error_events_at_full_state_memory_are_unchanged():
    c = np.array([1.0, 0.9, 0.35, 0.05])
    acf = np.array([1.0, -0.3, -0.1])
    a = pr_error_events(c, acf, 4, False)
    b = pr_error_events(c, acf, 4, False, state_memory=3)
    assert a == b
    short = pr_error_events(c, acf, 4, False, state_memory=1)
    assert min(g for g, _ in short) < min(g for g, _ in a)


# ------------------------------------------------------------ configuration

def test_config_refuses_what_it_cannot_run():
    with pytest.raises(ValueError, match="state_memory"):
        MlsdConfig(kind="viterbi", memory=2, state_memory=0)
    with pytest.raises(ValueError, match="Viterbi"):
        MlsdConfig(kind="sliding", state_memory=1)
    rx = RxConfig(arch="adc_dsp", mlsd=MlsdConfig(kind="viterbi", memory=2, state_memory=4))
    with pytest.raises(ValueError, match="trellis"):
        LinkConfig(modulation="pam4", rx=rx, pr=PrConfig(target=(1.0, 0.5)))
    LinkConfig(modulation="pam4", rx=rx, pr=PrConfig(target=(1.0, 0.5, 0.0)))   # L = 4
    rx2 = dc.replace(rx, mlsd=MlsdConfig(kind="viterbi", memory=2, state_memory=2))
    LinkConfig(modulation="pam4", rx=rx2, pr=PrConfig(target=(1.0, 0.5)),
               numeric=NumericConfig(mode="fixed"))                        # bit-true DFSE (fixed_viterbi)


def test_power_model_prices_the_reduced_trellis():
    def cfg(k):
        rx = RxConfig(arch="adc_dsp", adc=AdcConfig(n_bits=8, enob=6.5),
                      mlsd=MlsdConfig(kind="viterbi", memory=2, state_memory=k))
        return LinkConfig(modulation="pam4", symbol_rate=112e9, rx=rx,
                          pr=PrConfig(target=(1.0, 0.5, 0.0)))
    assert [trellis_states(cfg(k)) for k in (None, 4, 3, 2, 1)] == [256, 256, 64, 16, 4]
    w = [power_report(cfg(k)).watts("host.rx.mlsd") for k in (None, 3, 2, 1)]
    assert w[0] > w[1] > w[2] > w[3]
    note = {b.name: b.note for b in power_report(cfg(2)).blocks}["host.rx.mlsd"]
    assert "16 states" in note and "2 cursors fed back" in note


# ------------------------------------------------------- engines (224G LR)

def _lr_cfg(k, n=120_000):
    """Example 38's receiver at 39.4 dB with the MMSE (1, a, b) target."""
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.26, rdc=5.0, r_skin=2.0e-3,
                              loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1,
                    clock=ClockConfig(rj_ui=0.004)),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=14, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    mlsd=MlsdConfig(kind="viterbi", memory=2, state_memory=k),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n, seed=3, pattern="prbs13q"),
        pr=PrConfig(target=(1.0, 0.5, 0.0), adapt="mmse"))


@pytest.fixture(scope="module")
def lr_runs():
    out = {}
    for k in (None, 2, 1):
        cfg = _lr_cfg(k)
        t = run_time_link(cfg)
        s = run_statistical(cfg, ffe_taps=t.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
        out[k] = (t.ber.ber, s.ber)
    return out


@needs_jit
def test_sixteen_states_hold_the_two_cursor_target(lr_runs):
    """The trellis on the target alone (K = 2, 16 states) loses nothing to
    the 256-state one: the 21-tap FFE leaves little beyond the target. K = 1
    (4 states) feeds the second controlled cursor back and pays for it."""
    full, k2, k1 = (lr_runs[k][0] for k in (None, 2, 1))
    assert k2 == pytest.approx(full, rel=0.1)
    assert k1 > 1.5 * full


@needs_jit
@pytest.mark.parametrize("k", [None, 2, 1])
def test_engines_agree_on_the_reduced_trellis(lr_runs, k):
    """Invariant 3 with a reduced-state detector on a real link."""
    t, s = lr_runs[k]
    assert 0.5 < s / t < 2.0, (k, t, s)
