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


def mlse_min_distance_sq(cursors: np.ndarray, max_event_len: int = 8) -> float:
    """Minimum-distance² of the MLSE trellis for residual channel ``cursors``.

    ``d_min² = min over nonzero error events e of ‖cursors * e‖²`` with the
    error symbols drawn from unit single-level steps ``{-1, 0, +1}`` (adjacent
    decision errors dominate the union bound). ``cursors[0]`` is the main
    cursor. For a memoryless channel ``[1]`` this returns ``1``; for the
    duobinary ``[1, 1]`` it returns ``2`` — the matched-filter bound, i.e. the
    classic 3 dB MLSE gain over an ideal DFE.

    Enumerates error events up to ``max_event_len`` taps (first tap fixed to +1
    by symmetry; remaining taps ternary), which captures the true minimum for
    the short residuals MLSE is used on.
    """
    h = np.asarray(cursors, dtype=float)
    best = float(np.dot(h, h))                 # single-symbol error e = [1]
    from itertools import product
    for L in range(1, max_event_len):
        for tail in product((-1, 0, 1), repeat=L):
            if tail[-1] == 0:                  # last tap must be nonzero
                continue
            e = np.array((1,) + tail, dtype=float)
            d = float(np.dot(np.convolve(h, e), np.convolve(h, e)))
            if d < best:
                best = d
    return best


def mlse_gain_over_dfe_db(cursors: np.ndarray, max_event_len: int = 8) -> float:
    """Asymptotic MLSE coding gain over an ideal DFE [dB].

    An ideal DFE cancels the postcursors, leaving distance ``|main|`` per
    symbol; MLSE achieves ``sqrt(d_min²)``. Gain = ``10·log10(d_min² / main²)``.
    Duobinary ``[1, 1]`` → ``≈3.01 dB``; memoryless → ``0 dB``.
    """
    h = np.asarray(cursors, dtype=float)
    main2 = float(h[0]) ** 2
    return 10.0 * np.log10(mlse_min_distance_sq(h, max_event_len) / max(main2, 1e-30))


def post_detect(y_eq: np.ndarray, cursors: np.ndarray, levels: np.ndarray,
                method: str = "viterbi", *, seq_len: int = 4,
                margin: float = 0.0):
    """Run an MLSD post-detector over slicer-input samples of a *real* link.

    Turns the standalone kernels into a drop-in post-detector: ``y_eq`` are the
    equalized slicer-input samples, ``cursors`` the residual channel (main +
    uncancelled postcursors, ``cursors[0]`` = main), ``levels`` the constellation.
    ``method`` is ``'viterbi'`` (full MLSE) or ``'sliding'`` (DragonPHY error-event
    detector, refined over the dominant postcursor). Returns decided level indices.
    """
    cur = np.asarray(cursors, dtype=float)
    lv = np.asarray(levels, dtype=float)
    if method == "viterbi":
        return viterbi_mlsd(np.asarray(y_eq, dtype=float), lv, cur)
    if method == "sliding":
        dec0 = slice_nearest_local(y_eq, lv * cur[0])
        resid_post = float(cur[1] / cur[0]) if cur.size > 1 else 0.0
        out = dec0
        for _ in range(2):                     # two refinement passes
            out = sliding_detector(np.asarray(y_eq, dtype=float), out,
                                   lv * cur[0], resid_post, seq_len, margin)
        return out
    raise ValueError(f"method must be 'viterbi' or 'sliding', got {method!r}")


def slice_nearest_local(y: np.ndarray, scaled_levels: np.ndarray) -> np.ndarray:
    """Nearest-level decisions of ``y`` against ``scaled_levels`` (index out)."""
    y = np.asarray(y, dtype=float)
    return np.argmin(np.abs(y[:, None] - scaled_levels[None, :]), axis=1)


viterbi_mlsd = _viterbi_py
sliding_detector = _sliding_detector_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        viterbi_mlsd = numba.njit(cache=True)(_viterbi_py)
        sliding_detector = numba.njit(cache=True)(_sliding_detector_py)
    except ImportError:
        pass
