"""Bit-true sliding-detector MLSD (``dsp/fixed_mlsd.py``, ROADMAP P1 #1).

The integer detector has to: gain what the float one gains, make exactly the
float detector's decisions when its words are wide (here: values where the
float arithmetic is exact), agree with an independent reference on every
rounding / shift / saturation choice, and be the same under JIT and pure
Python. The SV reimplementation is checked in test_rtl_lockstep.
"""

import dataclasses

import numpy as np
import pytest

import halo_serdes.dsp.fixed_mlsd as fm
from halo_serdes.config.schema import MlsdConfig
from halo_serdes.dsp.fixed_mlsd import FixedSliding, run_fixed_sliding
from halo_serdes.dsp.mlsd import sliding_detector
from halo_serdes.engine import run_time_link

LEVELS = np.array([-600, -200, 200, 600], dtype=np.int64)


def _stream(n, resid, sigma, seed, levels=LEVELS):
    """Slicer words of a channel with residual postcursor ``resid`` and the
    DFE-style first decisions (feedback from the decided symbol)."""
    rng = np.random.default_rng(seed)
    sym = rng.integers(0, levels.size, n)
    lv = levels[sym]
    v = lv.copy()
    v[1:] += np.round(resid * lv[:-1]).astype(np.int64)
    v += np.round(rng.normal(scale=sigma, size=n)).astype(np.int64)
    fbt = np.round(resid * levels).astype(np.int64)
    dec0 = np.zeros(n, dtype=np.int64)
    for k in range(n):
        u = v[k] - (fbt[dec0[k - 1]] if k else 0)
        dec0[k] = int(np.argmin(np.abs(u - levels)))
    return sym, v, dec0


def _detector(fbt, seq_len=4, margin=0, sq_shift=0, metric_max=(1 << 62) - 1):
    return FixedSliding(rp=0, rp_fl=0, fbt=np.asarray(fbt, dtype=np.int64), seq_len=seq_len,
                        margin=margin, sq_shift=sq_shift, metric_max=metric_max)


def test_it_corrects_errors_the_slicer_made():
    sym, v, dec0 = _stream(60_000, 0.5, 95.0, 1)
    fs = _detector(np.round(0.5 * LEVELS))
    dec = run_fixed_sliding(fs, v, dec0, LEVELS)
    e0, e1 = np.sum(dec0 != sym), np.sum(dec != sym)
    assert e0 > 300 and e1 < 0.85 * e0, (e0, e1)


@pytest.mark.parametrize("seed", range(3))
def test_wide_words_make_the_float_detectors_decisions(seed):
    """resid 0.25 on levels that are multiples of 4: every float operation
    of the float detector is exact, so the two must agree symbol for symbol."""
    sym, v, dec0 = _stream(20_000, 0.25, 90.0, seed)
    fs = _detector(LEVELS // 4)                 # exact 0.25 * L
    got = run_fixed_sliding(fs, v, dec0, LEVELS)
    want = dec0.copy()
    for _ in range(fm.PASSES):
        want = sliding_detector(v.astype(float), want, LEVELS.astype(float), 0.25, 4, 0.0)
    assert np.sum(got != dec0) > 50                 # it did flip decisions
    assert np.array_equal(got, want)


def _reference(v, dec0, levels, fbt, seq_len, margin, sq_shift, metric_max, passes):
    """Written for clarity, not speed: each hypothesis builds the whole window
    of residuals for the candidate decision sequence from scratch."""
    v = [int(x) for x in v]
    out = [int(x) for x in dec0]
    n = len(v)
    if n < seq_len + 2:
        return np.array(out)

    def res(d, k):
        return v[k] - int(levels[d[k]]) - (int(fbt[d[k - 1]]) if k > 0 else 0)

    def metric(d, k):
        s = sum((res(d, j) ** 2) >> sq_shift for j in range(k, k + seq_len))
        return min(s, metric_max)

    for _ in range(passes):
        for k in range(1, n - seq_len - 1):
            best, best_d = metric(out, k) - margin, 0
            for delta in (-1, 1):
                m = out[k] + delta
                if 0 <= m < len(levels):
                    cand = out[:]
                    cand[k] = m
                    s = metric(cand, k)
                    if s < best:
                        best, best_d = s, delta
            out[k] += best_d
    return np.array(out)


@pytest.mark.parametrize("seed", range(4))
def test_matches_an_independent_reference(seed):
    rng = np.random.default_rng(10 + seed)
    levels = np.sort(rng.integers(-700, 700, 4))      # not uniform
    resid = rng.uniform(0.2, 0.6)
    _, v, dec0 = _stream(1_500, resid, 120.0, seed, levels)
    fbt = (int(np.round(resid * 256)) * levels + 128) >> 8
    seq_len = int(rng.integers(2, 6))
    sq_shift = int(rng.integers(0, 6))
    margin = int(rng.integers(0, 2000))
    metric_max = int([1 << 40, 60_000, 200_000, 1 << 20][seed])
    fs = _detector(fbt, seq_len, margin, sq_shift, metric_max)
    got = run_fixed_sliding(fs, v, dec0, levels)
    want = _reference(v, dec0, levels, fbt, seq_len, margin, sq_shift, metric_max, fm.PASSES)
    assert np.array_equal(got, want)


@pytest.mark.skipif(fm.sliding_fixed_kernel is fm._sliding_fixed_py,
                    reason="compares the JIT with the pure-Python kernel")
def test_jit_and_python_agree(monkeypatch):
    _, v, dec0 = _stream(30_000, 0.4, 110.0, 7)
    fs = _detector(np.round(0.4 * LEVELS), 4, 500, 3, 1 << 22)
    jit = run_fixed_sliding(fs, v, dec0, LEVELS)
    monkeypatch.setattr(fm, "sliding_fixed_kernel", fm._sliding_fixed_py)
    assert np.array_equal(jit, run_fixed_sliding(fs, v, dec0, LEVELS))


def test_the_engine_runs_it_on_the_loops_words():
    from test_fixed_loop import _cfg

    cfg = _cfg(12_000, mode="fixed")
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, noise_rms=0.012, mlsd=MlsdConfig(kind="sliding", seq_len=4)))
    r = run_time_link(cfg)
    fx = r.extras["fixed"]
    rec, fsd = fx["record"], fx["mlsd"]["detector"]
    n = fx["mlsd"]["dec"].size
    lv = fx["loop"].levels_out
    # it corrects the loop's own decisions (data-aided in training), as the
    # float detector corrects the float receiver's -- not a plain slice
    dec0 = rec["dec"][:n]
    again = run_fixed_sliding(fsd, rec["v_out"][:n], dec0, lv)
    assert np.array_equal(again, fx["mlsd"]["dec"])
    assert np.array_equal(r.extras["decisions"], fx["mlsd"]["dec"][r.extras["warmup"]:])
