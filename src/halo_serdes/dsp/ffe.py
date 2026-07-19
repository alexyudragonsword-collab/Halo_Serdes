"""FFE tap solvers (zero-forcing / MMSE) and the baud-rate FIR filter.

Ports serdespy's ``forcing_ffe`` Toeplitz zero-forcing solve (with its
redundant double normalization removed) and extends it to regularized MMSE:
``w = (M^T M + lambda I)^-1 M^T d`` where M is the channel convolution matrix
and d the desired (delta) response. At lambda -> 0 with a square system, MMSE
reduces exactly to ZF.
"""

from __future__ import annotations

import numpy as np

from ..core.waveform import Waveform


def channel_cursors(pulse: Waveform, osr: int, n_pre: int, n_post: int,
                    peak_idx: int | None = None) -> np.ndarray:
    """UI-spaced cursor samples of a pulse response around its main cursor.

    Returns array of length ``n_pre + 1 + n_post``: [pre..., main, post...].
    (serdespy ``channel_coefficients``, without the plotting.)
    """
    y = pulse.y
    if peak_idx is None:
        peak_idx = int(np.argmax(np.abs(y)))
    cursors = np.zeros(n_pre + 1 + n_post)
    for i, k in enumerate(range(-n_pre, n_post + 1)):
        idx = peak_idx + k * osr
        if 0 <= idx < y.size:
            cursors[i] = y[idx]
    return cursors


def _conv_matrix(c: np.ndarray, n_taps: int) -> np.ndarray:
    """Convolution matrix M (rows: output cursors, cols: taps): (M w)[m] =
    sum_j c[m - j] w[j], m over the full support len(c) + n_taps - 1."""
    n_out = c.size + n_taps - 1
    M = np.zeros((n_out, n_taps))
    for j in range(n_taps):
        M[j: j + c.size, j] = c
    return M


def zf_ffe(cursors: np.ndarray, c_pre: int, n_taps: int, tap_pre: int,
           normalize: bool = True) -> np.ndarray:
    """Zero-forcing FFE solve.

    Args:
        cursors: channel cursor vector [pre..., main, post...] with ``c_pre``
            precursors.
        n_taps: total FFE taps; ``tap_pre`` of them are precursor taps.
        normalize: scale so sum(|w|) == 1 (transmit-power style constraint).

    Solves the square system forcing the equalized response to a unit pulse at
    the main position over ``n_taps`` constrained cursor positions centered on
    the main cursor (serdespy ``forcing_ffe`` formulation).
    """
    A = np.zeros((n_taps, n_taps))
    for j in range(n_taps):
        for i in range(n_taps):
            k = (i - tap_pre) - (j - tap_pre)  # channel index offset
            ci = c_pre + k
            if 0 <= ci < cursors.size:
                A[i, j] = cursors[ci]
    d = np.zeros(n_taps)
    d[tap_pre] = 1.0
    w = np.linalg.solve(A, d)
    if normalize:
        w = w / np.abs(w).sum()
    return w


def mmse_ffe(cursors: np.ndarray, c_pre: int, n_taps: int, tap_pre: int,
             noise_var: float = 0.0, normalize: bool = True) -> np.ndarray:
    """Regularized MMSE FFE over the full ISI support.

    Minimizes ||M w - d||^2 + noise_var * ||w||^2 with d a delta at the main
    output cursor. noise_var = 0 gives the least-squares ZF solution.
    """
    M = _conv_matrix(cursors, n_taps)
    d = np.zeros(M.shape[0])
    d[c_pre + tap_pre] = 1.0
    A = M.T @ M + noise_var * np.eye(n_taps)
    w = np.linalg.solve(A, M.T @ d)
    if normalize:
        w = w / np.abs(w).sum()
    return w


def apply_ffe(y_baud: np.ndarray, w: np.ndarray, tap_pre: int) -> np.ndarray:
    """Baud-rate FFE: output[k] = sum_j w[j] * y[k + tap_pre - j], aligned so
    sample k still corresponds to symbol k."""
    full = np.convolve(y_baud, np.asarray(w, dtype=np.float64))
    return full[tap_pre: tap_pre + y_baud.size]


def equalized_cursors(cursors: np.ndarray, w: np.ndarray, c_pre: int,
                      tap_pre: int) -> tuple[np.ndarray, int]:
    """Channel cursors after FFE; returns (new_cursors, new_pre_count)."""
    out = np.convolve(cursors, w)
    return out, c_pre + tap_pre
