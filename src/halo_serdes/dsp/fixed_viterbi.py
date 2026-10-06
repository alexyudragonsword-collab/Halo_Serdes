"""Bit-true Viterbi MLSD on the fixed-point slicer words.

The integer counterpart of ``mlsd._viterbi_py``, run on the slicer values of
the bit-true loop (``fixed_loop``) and reimplemented in ``rtl/viterbi_mlsd.sv``.

Integer semantics:

* the trellis cursors ``[c0, c1, .., cL]`` are words with ``c_fl`` fraction
  bits; the expected slicer word for a state and a new symbol is
  ``rnd(sum_i c[i] L[x_i], c_fl)`` -- one value per (state, symbol), a table
  in hardware (``expected``);
* branch metric ``(y - expected)^2 >>> sq_shift``; path metric the sum,
  saturated at ``metric_max``;
* after every step the smallest path metric is subtracted from all of them
  (renormalisation: the comparisons see only differences, and the metrics
  stay within ``metric_bits`` however long the run);
* add-compare-select in the float kernel's order -- previous states
  ascending, new symbols ascending, a strictly smaller candidate wins -- and
  the traceback starts from the first state with the smallest metric.

The traceback is over the whole run, as the float detector's; a hardware one
would cut it at a fixed depth (not modelled). When the float arithmetic is
exact the two make the same decisions (``tests/test_fixed_viterbi.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np


def _viterbi_fixed_py(y, expected, nl, L, sq_shift, metric_max, out):
    """``expected[s, m]``: expected word for previous state ``s`` (most
    recent symbol in the lowest base-``nl`` digit) and new symbol ``m``."""
    n = y.size
    n_states = expected.shape[0]
    metric = np.zeros(n_states, dtype=np.int64)
    new_metric = np.zeros(n_states, dtype=np.int64)
    valid = np.ones(n_states, dtype=np.int64)
    new_valid = np.zeros(n_states, dtype=np.int64)
    bp = np.zeros((n, n_states), dtype=np.int64)
    for k in range(n):
        for s in range(n_states):
            new_valid[s] = 0
        for s in range(n_states):
            if valid[s] == 0:
                continue
            for m in range(nl):
                e = y[k] - expected[s, m]
                cand = metric[s] + ((e * e) >> sq_shift)
                if cand > metric_max:
                    cand = metric_max
                ns = (s * nl + m) % n_states
                if new_valid[ns] == 0 or cand < new_metric[ns]:
                    new_metric[ns] = cand
                    new_valid[ns] = 1
                    bp[k, ns] = s
        lo = metric_max
        for s in range(n_states):
            if new_valid[s] == 1 and new_metric[s] < lo:
                lo = new_metric[s]
        for s in range(n_states):
            valid[s] = new_valid[s]
            metric[s] = new_metric[s] - lo if new_valid[s] == 1 else 0
    best = 0
    for s in range(n_states):
        if valid[s] == 1 and (valid[best] == 0 or metric[s] < metric[best]):
            best = s
    s = best
    for k in range(n - 1, -1, -1):
        out[k] = s % nl
        s = bp[k, s]


viterbi_fixed_kernel = _viterbi_fixed_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        viterbi_fixed_kernel = numba.njit(cache=True)(_viterbi_fixed_py)
    except ImportError:
        pass


@dataclass(frozen=True)
class FixedViterbi:
    cursors: np.ndarray       # trellis cursors [2^-c_fl]
    c_fl: int
    expected: np.ndarray      # [n_states, n_levels] expected slicer words
    sq_shift: int
    metric_max: int


def expected_table(cursors: np.ndarray, levels: np.ndarray, c_fl: int) -> np.ndarray:
    """``rnd(sum_i c[i] L[x_i], c_fl)`` for every state and new symbol."""
    c = [int(v) for v in np.asarray(cursors, dtype=np.int64)]
    lv = [int(v) for v in np.asarray(levels, dtype=np.int64)]
    nl, mem = len(lv), len(c) - 1
    n_states = nl ** mem if mem > 0 else 1
    half = (1 << (c_fl - 1)) if c_fl > 0 else 0
    tab = np.zeros((n_states, nl), dtype=np.int64)
    for s in range(n_states):
        for m in range(nl):
            acc = c[0] * lv[m]
            ss = s
            for i in range(1, mem + 1):
                acc += c[i] * lv[ss % nl]
                ss //= nl
            tab[s, m] = (acc + half) >> c_fl
    return tab


def build_fixed_viterbi(cfg, cursors: np.ndarray, levels_out: np.ndarray,
                        out_bits: int) -> FixedViterbi:
    """Quantise the float detector's trellis for slicer words of ``out_bits``."""
    num = cfg.numeric
    c_fl = int(num.dfe_weight.fl)
    c = np.round(np.asarray(cursors, dtype=float) * 2.0 ** c_fl).astype(np.int64)
    # |y - expected| < 2^(out_bits + 1): the squares fit, before the shift,
    # in 2 (out_bits + 1) bits, and a path metric is a few of them
    sq_shift = max(0, 2 * (out_bits + 1) + 2 - num.mlsd_metric_bits)
    return FixedViterbi(cursors=c, c_fl=c_fl, expected=expected_table(c, levels_out, c_fl),
                        sq_shift=int(sq_shift),
                        metric_max=int((1 << num.mlsd_metric_bits) - 1))


def run_fixed_viterbi(fv: FixedViterbi, y: np.ndarray, n_levels: int) -> np.ndarray:
    y = np.asarray(y, dtype=np.int64)
    out = np.zeros(y.size, dtype=np.int64)
    viterbi_fixed_kernel(y, fv.expected, int(n_levels), int(fv.cursors.size - 1),
                         fv.sq_shift, fv.metric_max, out)
    return out


def dump_viterbi_vectors(path, fv: FixedViterbi, y, n_levels: int, dec, cw: int = 64):
    """Vectors for ``rtl/tb_viterbi_mlsd.sv`` and ``vit_dims.svh``."""
    from pathlib import Path

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    np.savetxt(path / "vit_y.txt", np.atleast_1d(y), fmt="%d")
    np.savetxt(path / "vit_expected.txt", fv.expected.ravel(), fmt="%d")
    np.savetxt(path / "vit_dec.txt", np.atleast_1d(dec), fmt="%d")
    params = {"N": int(np.size(y)), "NL": int(n_levels), "NS": int(fv.expected.shape[0]),
              "SQ_SHIFT": fv.sq_shift, "METRIC_MAX": fv.metric_max, "CW": cw}
    with open(path / "vit_dims.svh", "w", encoding="utf-8") as fh:
        fh.write("// generated by halo_serdes.dsp.fixed_viterbi.dump_viterbi_vectors\n")
        for k, v in params.items():
            fh.write(f"localparam longint {k} = {int(v)};\n")
    return path
