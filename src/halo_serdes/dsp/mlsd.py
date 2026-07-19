"""Maximum-likelihood sequence detection over residual ISI.

Two implementations, mirroring the DragonPHY2 design space:

- ``viterbi_mlsd``: full MLSE over a short residual channel memory L
  (states = n_levels^L, branch metric = squared error against the expected
  observation). The high-end option (PyBERT ViterbiDecoder_ISI idea, numba).
- ``sliding_detector``: DragonPHY's low-cost post-detector — hypothesize
  single/adjacent-pair decision-error events, re-inject the corresponding
  error signature into the residual stream, keep the hypothesis with minimum
  squared error over a short window. Near-MLSD gain on 1-dominant-tap
  residuals at a fraction of the hardware cost.
"""

from __future__ import annotations

import os

import numpy as np


def _viterbi_py(y: np.ndarray, levels: np.ndarray, cursors: np.ndarray):
    """MLSE: y[k] = sum_i cursors[i] * v[k-i] + noise, cursors[0] = main.

    Memory L = len(cursors) - 1; state = (idx[k-L+1..k]) packed base-n_levels
    (most recent symbol in the lowest digit). Returns decided level indices.
    """
    n = y.size
    nl = levels.size
    L = cursors.size - 1
    if L == 0:
        out = np.zeros(n, dtype=np.int64)
        for k in range(n):
            best, bd = 0, abs(y[k] - cursors[0] * levels[0])
            for m in range(1, nl):
                d = abs(y[k] - cursors[0] * levels[m])
                if d < bd:
                    bd, best = d, m
            out[k] = best
        return out
    n_states = nl ** L
    INF = 1e30
    metric = np.full(n_states, 0.0)
    # backpointers: store previous state's newest symbol per (k, state)
    bp = np.zeros((n, n_states), dtype=np.int64)
    new_metric = np.empty(n_states)
    for k in range(n):
        for s in range(n_states):
            new_metric[s] = INF
        for s in range(n_states):  # previous state
            if metric[s] >= INF:
                continue
            for m in range(nl):    # new symbol
                # expected observation: cursors[0]*new + sum_i cursors[i]*hist
                exp_v = cursors[0] * levels[m]
                ss = s
                for i in range(1, L + 1):
                    exp_v += cursors[i] * levels[ss % nl]
                    ss //= nl
                e = y[k] - exp_v
                cand = metric[s] + e * e
                ns = (s * nl + m) % n_states  # shift in newest symbol
                if cand < new_metric[ns]:
                    new_metric[ns] = cand
                    bp[k, ns] = s
        for s in range(n_states):
            metric[s] = new_metric[s]
    # traceback
    out = np.zeros(n, dtype=np.int64)
    s = int(np.argmin(metric))
    for k in range(n - 1, -1, -1):
        out[k] = s % nl
        s = bp[k, s]
    return out


def _sliding_detector_py(y_eq: np.ndarray, dec: np.ndarray, levels: np.ndarray,
                         resid_post: float, seq_len: int, margin: float):
    """DragonPHY-style error-event post-detector (adjacent-decision events).

    y_eq: slicer-input samples; dec: initial decisions (level indices);
    resid_post: dominant residual postcursor (relative to the slicer levels'
    main-cursor scale); seq_len: squared-error window; margin: MAP acceptance
    threshold on the SSE improvement — a flip hypothesis must beat "no error"
    by at least this much (2*sigma^2*ln((1-p)/p) for noise sigma and prior
    error rate p; without it, noise alone triggers false corrections).

    Under hypothesis "true symbol at k = dec[k] + delta", the model residual
    e[j] = y[j] - L[dec[j]] - resid_post*L[dec[j-1]] changes by -delta*step
    at j = k and -delta*step*resid_post at j = k+1.
    """
    n = dec.size
    out = dec.copy()
    if n < seq_len + 2:
        return out
    step = levels[1] - levels[0]  # adjacent-level spacing (uniform grids)
    resid = np.empty(n)
    for k in range(n):
        fb = resid_post * levels[out[k - 1]] if k > 0 else 0.0
        resid[k] = y_eq[k] - levels[out[k]] - fb
    nl = levels.size
    for k in range(1, n - seq_len - 1):
        base = 0.0
        for j in range(seq_len):
            base += resid[k + j] * resid[k + j]
        best_sse = base - margin
        best_delta = 0
        for delta in (-1, 1):
            m = out[k] + delta
            if m < 0 or m >= nl:
                continue
            sse = 0.0
            for j in range(seq_len):
                e = resid[k + j]
                if j == 0:
                    e = e - delta * step        # hypothesis: level was delta higher
                elif j == 1:
                    e = e - delta * step * resid_post
                sse += e * e
            if sse < best_sse:
                best_sse = sse
                best_delta = delta
        if best_delta != 0:
            out[k] = out[k] + best_delta
            # update residual stream for downstream positions
            resid[k] -= best_delta * step
            if k + 1 < n:
                resid[k + 1] -= best_delta * step * resid_post
    return out


viterbi_mlsd = _viterbi_py
sliding_detector = _sliding_detector_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        viterbi_mlsd = numba.njit(cache=True)(_viterbi_py)
        sliding_detector = numba.njit(cache=True)(_sliding_detector_py)
    except ImportError:
        pass
