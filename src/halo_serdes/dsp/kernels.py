"""Serial DSP kernels (per-symbol loops), numba-compiled when available.

All hot loops in the framework live here (and later cdr/kernels.py), taking
only ndarrays/scalars, so the JIT boundary is explicit. Pure-Python fallbacks
define the semantics; numba is a performance layer only (HALO_NO_JIT=1 forces
the fallback, and CI runs both).
"""

from __future__ import annotations

import os

import numpy as np


def _dfe_static_py(y: np.ndarray, w_dfe: np.ndarray, levels: np.ndarray):
    """Static (fixed-weight) decision-feedback equalizer at baud rate.

    y: FFE output, one sample per symbol.
    w_dfe: postcursor tap weights (index 0 = 1-UI-back tap).
    levels: decision levels (sorted ascending); nearest-level slicer.

    Returns (decisions_idx int64, y_eq float64) where y_eq[k] is the
    summing-node value y[k] - sum_i w[i]*levels[dec[k-1-i]].
    """
    n = y.size
    nt = w_dfe.size
    dec = np.zeros(n, dtype=np.int64)
    y_eq = np.empty(n, dtype=np.float64)
    nl = levels.size
    for k in range(n):
        fb = 0.0
        for i in range(nt):
            j = k - 1 - i
            if j >= 0:
                fb += w_dfe[i] * levels[dec[j]]
        v = y[k] - fb
        y_eq[k] = v
        # nearest-level slicer (levels sorted ascending)
        best = 0
        bd = abs(v - levels[0])
        for m in range(1, nl):
            d = abs(v - levels[m])
            if d < bd:
                bd = d
                best = m
        dec[k] = best
    return dec, y_eq


dfe_static = _dfe_static_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        dfe_static = numba.njit(cache=True)(_dfe_static_py)
    except ImportError:
        pass


def slice_nearest(y: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Vectorized nearest-level slicer (no feedback): returns level indices."""
    y = np.asarray(y, dtype=np.float64)
    levels = np.asarray(levels, dtype=np.float64)
    thresholds = (levels[:-1] + levels[1:]) / 2.0
    return np.searchsorted(thresholds, y).astype(np.int64)
