"""Bit-true sliding-detector MLSD on the fixed-point slicer words.

The integer counterpart of ``mlsd._sliding_detector_py`` (DragonPHY's error-
event post-detector), run on the slicer values and decisions of the bit-true
loop (``fixed_loop``) and reimplemented in ``rtl/sliding_mlsd.sv``.

Integer semantics:

* the residual postcursor ``r`` is a ``Q(·, rp_fl)`` word ``rp``; the feedback
  it predicts for level ``m`` is ``fbt[m] = (rp * L[m] + 2^(rp_fl-1)) >> rp_fl``
  (one value per level -- a table in hardware);
* residual ``e[k] = v[k] - L[d[k]] - fbt[d[k-1]]``;
* a hypothesis "symbol k is level m = d[k] +- 1" recomputes the two residuals
  it touches from that definition (``e[k]`` with ``L[m]``, ``e[k+1]`` with
  ``fbt[m]``) instead of subtracting a precomputed signature, so the residual
  stream after an accepted flip is exactly what it would be from scratch --
  with rounded integers the two differ, and the float detector's uniform
  ``step`` would not even be the level spacing of the rounded levels;
* metric: squared errors shifted right by ``sq_shift`` and summed over
  ``seq_len``, saturated at ``metric_max``; accept the best hypothesis whose
  metric is strictly below ``base - margin`` (``margin`` in metric LSBs);
* two passes, as ``mlsd.post_detect``.

With wide words (``rp_fl`` large, ``sq_shift`` 0, no saturation) it makes
the float detector's decisions (``tests/test_fixed_mlsd.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

PASSES = 2


def _sliding_fixed_py(v, dec0, levels, fbt, seq_len, margin, sq_shift, metric_max,
                      passes, out):
    n = v.size
    nl = levels.size
    for i in range(n):
        out[i] = dec0[i]
    if n < seq_len + 2:
        return
    resid = np.zeros(n, dtype=np.int64)
    for _p in range(passes):
        for k in range(n):
            e = v[k] - levels[out[k]]
            if k > 0:
                e -= fbt[out[k - 1]]
            resid[k] = e
        for k in range(1, n - seq_len - 1):
            base = 0
            for j in range(seq_len):
                base += (resid[k + j] * resid[k + j]) >> sq_shift
            if base > metric_max:
                base = metric_max
            best = base - margin
            best_d = 0
            for d in (-1, 1):
                m = out[k] + d
                if m < 0 or m >= nl:
                    continue
                e0 = v[k] - levels[m] - fbt[out[k - 1]]
                e1 = v[k + 1] - levels[out[k + 1]] - fbt[m]
                sse = ((e0 * e0) >> sq_shift) + ((e1 * e1) >> sq_shift)
                for j in range(2, seq_len):
                    sse += (resid[k + j] * resid[k + j]) >> sq_shift
                if sse > metric_max:
                    sse = metric_max
                if sse < best:
                    best = sse
                    best_d = d
            if best_d != 0:
                m = out[k] + best_d
                resid[k] = v[k] - levels[m] - fbt[out[k - 1]]
                resid[k + 1] = v[k + 1] - levels[out[k + 1]] - fbt[m]
                out[k] = m


sliding_fixed_kernel = _sliding_fixed_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        sliding_fixed_kernel = numba.njit(cache=True)(_sliding_fixed_py)
    except ImportError:
        pass


@dataclass(frozen=True)
class FixedSliding:
    rp: int                   # residual postcursor word
    rp_fl: int
    fbt: np.ndarray           # predicted feedback per level [slicer LSBs]
    seq_len: int
    margin: int               # [metric LSBs]
    sq_shift: int
    metric_max: int


def build_fixed_sliding(cfg, resid_post: float, levels_out: np.ndarray, out_lsb: float,
                        out_bits: int) -> FixedSliding:
    """Quantise the float detector's settings for slicer words of ``out_bits``."""
    num = cfg.numeric
    mcfg = cfg.rx.mlsd
    rp_fl = int(num.dfe_weight.fl)
    rp = int(np.round(resid_post * 2.0 ** rp_fl))
    lv = np.asarray(levels_out, dtype=np.int64)
    half = (1 << (rp_fl - 1)) if rp_fl > 0 else 0
    fbt = (rp * lv + half) >> rp_fl
    # |e| < 2^(out_bits + 1); the summed metric must fit metric_bits
    need = 2 * (out_bits + 1) + int(np.ceil(np.log2(mcfg.seq_len)))
    sq_shift = max(0, need - num.mlsd_metric_bits)
    metric_max = (1 << num.mlsd_metric_bits) - 1
    margin = int(np.round(mcfg.margin / (out_lsb * out_lsb * 2.0 ** sq_shift)))
    return FixedSliding(rp=rp, rp_fl=rp_fl, fbt=fbt.astype(np.int64), seq_len=int(mcfg.seq_len),
                        margin=margin, sq_shift=int(sq_shift), metric_max=int(metric_max))


def run_fixed_sliding(fs: FixedSliding, v_out: np.ndarray, dec0: np.ndarray,
                      levels_out: np.ndarray) -> np.ndarray:
    v = np.asarray(v_out, dtype=np.int64)
    out = np.zeros(v.size, dtype=np.int64)
    sliding_fixed_kernel(v, np.asarray(dec0, dtype=np.int64),
                         np.asarray(levels_out, dtype=np.int64), fs.fbt, fs.seq_len,
                         fs.margin, fs.sq_shift, fs.metric_max, PASSES, out)
    return out


def dump_mlsd_vectors(path, fs: FixedSliding, v_out, dec0, levels_out, dec, cw: int = 64):
    """Vectors for ``rtl/tb_sliding_mlsd.sv`` and ``mlsd_dims.svh``."""
    from pathlib import Path

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    for name, arr in (("v", v_out), ("dec0", dec0), ("levels", levels_out),
                      ("fbt", fs.fbt), ("dec", dec)):
        np.savetxt(path / f"mlsd_{name}.txt", np.atleast_1d(arr), fmt="%d")
    params = {"N": int(np.size(v_out)), "NL": int(np.size(levels_out)),
              "SEQ_LEN": fs.seq_len, "MARGIN": fs.margin, "SQ_SHIFT": fs.sq_shift,
              "METRIC_MAX": fs.metric_max, "PASSES": PASSES, "CW": cw}
    with open(path / "mlsd_dims.svh", "w", encoding="utf-8") as fh:
        fh.write("// generated by halo_serdes.dsp.fixed_mlsd.dump_mlsd_vectors\n")
        for k, v in params.items():
            fh.write(f"localparam longint {k} = {int(v)};\n")
    return path
