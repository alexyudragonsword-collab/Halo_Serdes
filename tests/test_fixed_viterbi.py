"""Bit-true Viterbi MLSD (``dsp/fixed_viterbi.py``, ROADMAP P1 #1).

As for the sliding detector: it gains what the float Viterbi gains, makes
exactly the float detector's decisions where the float arithmetic is exact,
agrees with an independent reference on every shift / saturation choice,
and is the same under JIT and pure Python. Renormalisation keeps the path
metrics bounded however long the run. The SV model is in test_rtl_lockstep.
"""

import dataclasses
from itertools import product

import numpy as np
import pytest

import halo_serdes.dsp.fixed_viterbi as fv
from halo_serdes.config.schema import MlsdConfig, NumericConfig, PrConfig
from halo_serdes.dsp.fixed_viterbi import FixedViterbi, expected_table, run_fixed_viterbi
from halo_serdes.dsp.mlsd import viterbi_mlsd
from halo_serdes.engine import run_time_link

LEVELS = np.array([-600, -200, 200, 600], dtype=np.int64)


def _stream(n, cursors_f, sigma, seed, levels=LEVELS):
    rng = np.random.default_rng(seed)
    sym = rng.integers(0, levels.size, n)
    lv = levels[sym].astype(float)
    y = cursors_f[0] * lv
    for i, c in enumerate(cursors_f[1:], 1):
        y[i:] += c * lv[:-i]
    y = np.round(y + rng.normal(scale=sigma, size=n)).astype(np.int64)
    return sym, y


def _detector(c_int, c_fl=8, sq_shift=0, metric_max=(1 << 62) - 1, levels=LEVELS):
    c = np.asarray(c_int, dtype=np.int64)
    return FixedViterbi(cursors=c, c_fl=c_fl, expected=expected_table(c, levels, c_fl),
                        sq_shift=sq_shift, metric_max=metric_max)


def test_it_beats_the_slicer_on_a_strong_postcursor():
    sym, y = _stream(30_000, [1.0, 0.75], 110.0, 1)
    slicer = np.zeros_like(sym)
    for k in range(sym.size):                    # a DFE-style slicer for comparison
        u = y[k] - (0.75 * LEVELS[slicer[k - 1]] if k else 0)
        slicer[k] = int(np.argmin(np.abs(u - LEVELS)))
    dec = run_fixed_viterbi(_detector([256, 192]), y, 4)
    e0, e1 = np.sum(slicer != sym), np.sum(dec != sym)
    assert e0 > 200 and e1 < 0.7 * e0, (e0, e1)


@pytest.mark.parametrize("cursors", [[1.0, 0.5], [1.0, 0.75], [1.0, 0.5, 0.25]])
def test_exact_inputs_make_the_float_viterbis_decisions(cursors):
    """Cursors that are multiples of 1/4 on levels that are multiples of 4:
    every float operation is exact, so the decisions must be identical."""
    sym, y = _stream(8_000, cursors, 130.0, 2)
    c_int = [int(c * 256) for c in cursors]
    got = run_fixed_viterbi(_detector(c_int), y, 4)
    want = viterbi_mlsd(y.astype(float), LEVELS.astype(float), np.array(cursors))
    assert np.sum(got != sym) > 20                   # errors left to decide on
    assert np.array_equal(got, want)


def _reference(y, c, levels, c_fl, sq_shift, metric_max):
    """Dictionary-based trellis over explicit symbol histories, normalised
    by the minimum each step."""
    nl, mem = len(levels), len(c) - 1
    half = (1 << (c_fl - 1)) if c_fl else 0
    states = list(product(range(nl), repeat=mem))     # (newest, ..., oldest)
    pm = {st: 0 for st in states}
    hist = {st: [] for st in states}
    for yk in (int(v) for v in y):
        new_pm, new_hist = {}, {}
        for st in sorted(states, key=lambda t: sum(d * nl ** i for i, d in enumerate(t))):
            for m in range(nl):
                exp = (c[0] * levels[m] + sum(c[i] * levels[st[i - 1]] for i in range(1, mem + 1))
                       + half) >> c_fl
                cand = min(metric_max, pm[st] + (((yk - exp) ** 2) >> sq_shift))
                nst = (m,) + st[:-1]
                if nst not in new_pm or cand < new_pm[nst]:
                    new_pm[nst], new_hist[nst] = cand, hist[st] + [m]
        lo = min(new_pm.values())
        pm = {st: v - lo for st, v in new_pm.items()}
        hist = new_hist
    order = sorted(states, key=lambda t: sum(d * nl ** i for i, d in enumerate(t)))
    best = min(order, key=lambda st: pm[st])           # first of the minima
    return np.array(hist[best])


@pytest.mark.parametrize("seed", range(4))
def test_matches_an_independent_reference(seed):
    rng = np.random.default_rng(20 + seed)
    levels = np.sort(rng.integers(-700, 700, 4))
    mem = 1 + seed % 2
    c = [256] + [int(v) for v in rng.integers(-150, 200, mem)]
    _, y = _stream(600, np.array(c) / 256, 140.0, seed, levels)
    sq_shift = int(rng.integers(0, 6))
    metric_max = int([1 << 40, 40_000, 300_000, 1 << 20][seed])
    got = run_fixed_viterbi(_detector(c, 8, sq_shift, metric_max, levels), y, 4)
    want = _reference(y, c, [int(v) for v in levels], 8, sq_shift, metric_max)
    assert np.array_equal(got, want)


def test_renormalised_metrics_stay_small_on_a_long_run():
    """Without renormalisation the metrics would grow with the run length
    and need ever more bits; with it a narrow metric gives the wide one's
    decisions."""
    _, y = _stream(50_000, [1.0, 0.5], 120.0, 5)
    wide = run_fixed_viterbi(_detector([256, 128]), y, 4)
    narrow = run_fixed_viterbi(_detector([256, 128], metric_max=(1 << 22) - 1), y, 4)
    assert np.array_equal(wide, narrow)


@pytest.mark.skipif(fv.viterbi_fixed_kernel is fv._viterbi_fixed_py,
                    reason="compares the JIT with the pure-Python kernel")
def test_jit_and_python_agree(monkeypatch):
    _, y = _stream(20_000, [1.0, 0.6, 0.2], 120.0, 7)
    d = _detector([256, 154, 51], 8, 3, (1 << 20) - 1)
    jit = run_fixed_viterbi(d, y, 4)
    monkeypatch.setattr(fv, "viterbi_fixed_kernel", fv._viterbi_fixed_py)
    assert np.array_equal(jit, run_fixed_viterbi(d, y, 4))


@pytest.mark.parametrize("pr", [False, True], ids=["delta", "pr"])
def test_the_engine_runs_it_on_the_loops_words(pr):
    from test_fixed_loop import _cfg

    cfg = _cfg(12_000, mode="fixed")
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, noise_rms=0.008, dfe=dataclasses.replace(cfg.rx.dfe, n_taps=0),
        mlsd=MlsdConfig(kind="viterbi", memory=1)))
    if pr:
        cfg = dataclasses.replace(cfg, pr=PrConfig(target=(1.0, 0.75)))
    r = run_time_link(cfg)
    fx = r.extras["fixed"]
    det = fx["mlsd"]["detector"]
    n = fx["mlsd"]["dec"].size
    again = run_fixed_viterbi(det, fx["record"]["v_out"][:n], fx["loop"].levels_out.size)
    assert np.array_equal(again, fx["mlsd"]["dec"])
    assert np.array_equal(r.extras["decisions"], fx["mlsd"]["dec"][r.extras["warmup"]:])
    f = run_time_link(dataclasses.replace(cfg, numeric=NumericConfig()))
    e1, e2 = f.ber.n_errors, r.ber.n_errors
    assert abs(e1 - e2) <= 3 * np.sqrt(e1 + e2) + 5, (e1, e2)


# ------------------------------------------------- reduced-state trellis (DFSE)

def _dfse(c_int, K, c_fl=8, sq_shift=0, metric_max=(1 << 62) - 1, levels=LEVELS):
    c = np.asarray(c_int, dtype=np.int64)
    acc, fbt = fv.dfse_tables(c, levels, K)
    return fv.FixedDfse(cursors=c, c_fl=c_fl, state_memory=K, acc=acc, fbt=fbt,
                        sq_shift=sq_shift, metric_max=metric_max)


@pytest.mark.parametrize("c_int, sq_shift, metric_max", [
    ([256, 154, 51], 3, (1 << 20) - 1), ([256, 192], 0, (1 << 62) - 1),
    ([256, 128, 64, 26], 2, 300_000)])
def test_dfse_at_full_state_memory_is_the_fixed_viterbi(c_int, sq_shift, metric_max):
    _, y = _stream(4_000, np.array(c_int) / 256, 130.0, 8)
    L = len(c_int) - 1
    full = run_fixed_viterbi(_detector(c_int, 8, sq_shift, metric_max), y, 4)
    assert np.array_equal(fv.run_fixed_dfse(_dfse(c_int, L, 8, sq_shift, metric_max), y, 4), full)


@pytest.mark.parametrize("cursors, K", [([1.0, 0.75, 0.5, 0.25], 1), ([1.0, 0.75, 0.5, 0.25], 2),
                                        ([1.0, 0.5, 0.25], 1)])
def test_dfse_exact_inputs_make_the_float_dfses_decisions(cursors, K):
    """As for the full trellis: exact float arithmetic, identical decisions."""
    from halo_serdes.dsp.mlsd import dfse_mlsd

    sym, y = _stream(8_000, cursors, 130.0, 9)
    c_int = [int(c * 256) for c in cursors]
    got = fv.run_fixed_dfse(_dfse(c_int, K), y, 4)
    want = dfse_mlsd(y.astype(float), LEVELS.astype(float), np.array(cursors), K)
    assert np.sum(got != sym) > 20
    assert np.array_equal(got, want)


def _dfse_reference(y, c, levels, K, c_fl, sq_shift, metric_max):
    """Each survivor's whole symbol sequence (the L symbols before the first
    sample -- the state's K, level 0 below them -- then its decisions); the
    expected word read straight off it, rounded once."""
    nl, L = len(levels), len(c) - 1
    half = (1 << (c_fl - 1)) if c_fl else 0
    states = list(range(nl ** K))
    seq = {}
    for s in states:
        digits = [(s // nl ** i) % nl for i in range(K)]          # newest first
        seq[s] = [0] * (L - K) + digits[::-1]                     # oldest first
    pm = {s: 0 for s in states}
    for yk in (int(v) for v in y):
        new_pm, new_seq = {}, {}
        for s in states:
            for m in range(nl):
                hist = seq[s]
                acc = c[0] * levels[m] + sum(c[i] * levels[hist[-i]] for i in range(1, L + 1))
                cand = min(metric_max, pm[s] + (((yk - ((acc + half) >> c_fl)) ** 2) >> sq_shift))
                ns = (s * nl + m) % nl ** K
                if ns not in new_pm or cand < new_pm[ns]:
                    new_pm[ns], new_seq[ns] = cand, hist + [m]
        lo = min(new_pm.values())
        pm = {s: v - lo for s, v in new_pm.items()}
        seq = new_seq
    best = min(states, key=lambda s: pm[s])                      # first of the minima
    return np.array(seq[best][L:])


@pytest.mark.parametrize("seed", range(4))
def test_dfse_matches_an_independent_reference(seed):
    rng = np.random.default_rng(40 + seed)
    levels = np.sort(rng.integers(-700, 700, 4))
    L = 2 + seed % 2
    K = 1 if seed < 2 else L - 1              # tails of 1 and 2 symbols
    c = [256] + [int(v) for v in rng.integers(-150, 200, L)]
    _, y = _stream(500, np.array(c) / 256, 140.0, seed, levels)
    sq_shift = int(rng.integers(0, 6))
    metric_max = int([1 << 40, 40_000, 300_000, 1 << 20][seed])
    got = fv.run_fixed_dfse(_dfse(c, K, 8, sq_shift, metric_max, levels), y, 4)
    want = _dfse_reference(y, c, [int(v) for v in levels], K, 8, sq_shift, metric_max)
    assert np.array_equal(got, want)


@pytest.mark.skipif(fv.dfse_fixed_kernel is fv._dfse_fixed_py,
                    reason="compares the JIT with the pure-Python kernel")
def test_dfse_jit_and_python_agree(monkeypatch):
    _, y = _stream(20_000, [1.0, 0.6, 0.3, 0.1], 120.0, 10)
    d = _dfse([256, 154, 77, 26], 1, 8, 3, (1 << 20) - 1)
    jit = fv.run_fixed_dfse(d, y, 4)
    monkeypatch.setattr(fv, "dfse_fixed_kernel", fv._dfse_fixed_py)
    assert np.array_equal(jit, fv.run_fixed_dfse(d, y, 4))


def test_the_engine_runs_the_reduced_trellis_bit_true():
    """numeric.mode='fixed' with mlsd.state_memory < the trellis length: the
    bit-true DFSE on the loop's words, as many errors as the float one."""
    from test_fixed_loop import _cfg

    cfg = _cfg(12_000, mode="fixed")
    cfg = dataclasses.replace(cfg, pr=PrConfig(target=(1.0, 0.75)), rx=dataclasses.replace(
        cfg.rx, noise_rms=0.008, dfe=dataclasses.replace(cfg.rx.dfe, n_taps=0),
        mlsd=MlsdConfig(kind="viterbi", memory=2, state_memory=1)))
    r = run_time_link(cfg)
    fx = r.extras["fixed"]
    det = fx["mlsd"]["detector"]
    assert isinstance(det, fv.FixedDfse) and det.state_memory == 1 and det.fbt.shape[0] == 2
    n = fx["mlsd"]["dec"].size
    again = fv.run_fixed_dfse(det, fx["record"]["v_out"][:n], fx["loop"].levels_out.size)
    assert np.array_equal(again, fx["mlsd"]["dec"])
    assert np.array_equal(r.extras["decisions"], fx["mlsd"]["dec"][r.extras["warmup"]:])
    f = run_time_link(dataclasses.replace(cfg, numeric=NumericConfig()))
    e1, e2 = f.ber.n_errors, r.ber.n_errors
    assert abs(e1 - e2) <= 3 * np.sqrt(e1 + e2) + 5, (e1, e2)
