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
