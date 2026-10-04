"""FFE tap solvers (zero-forcing / MMSE) and the baud-rate FIR filter.

Ports serdespy's ``forcing_ffe`` Toeplitz zero-forcing solve (with its
redundant double normalization removed) and extends it to regularized MMSE:
``w = (M^T M + lambda I)^-1 M^T d`` where M is the channel convolution matrix
and d the desired response: a delta at the main cursor, or a partial-response
``target`` starting there (``(1.0, a)`` for 1 + aD). At lambda -> 0 with a
square system, MMSE reduces exactly to ZF.
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


def _target(target) -> np.ndarray:
    return np.array([1.0] if target is None else target, dtype=np.float64)


def zf_ffe(cursors: np.ndarray, c_pre: int, n_taps: int, tap_pre: int,
           normalize: bool = True, target=None) -> np.ndarray:
    """Zero-forcing FFE solve.

    Args:
        cursors: channel cursor vector [pre..., main, post...] with ``c_pre``
            precursors.
        n_taps: total FFE taps; ``tap_pre`` of them are precursor taps.
        normalize: scale so sum(|w|) == 1 (transmit-power style constraint).

    Solves the square system forcing the equalized response to a unit pulse at
    the main position over ``n_taps`` constrained cursor positions centered on
    the main cursor (serdespy ``forcing_ffe`` formulation) -- or to ``target``
    from the main position on.
    """
    A = np.zeros((n_taps, n_taps))
    for j in range(n_taps):
        for i in range(n_taps):
            k = (i - tap_pre) - (j - tap_pre)  # channel index offset
            ci = c_pre + k
            if 0 <= ci < cursors.size:
                A[i, j] = cursors[ci]
    d = np.zeros(n_taps)
    t = _target(target)
    n = min(t.size, n_taps - tap_pre)
    d[tap_pre: tap_pre + n] = t[:n]
    w = np.linalg.solve(A, d)
    if normalize:
        w = w / np.abs(w).sum()
    return w


def mmse_ffe(cursors: np.ndarray, c_pre: int, n_taps: int, tap_pre: int,
             noise_var: float = 0.0, normalize: bool = True, target=None) -> np.ndarray:
    """Regularized MMSE FFE over the full ISI support.

    Minimizes ||M w - d||^2 + noise_var * ||w||^2 with d a delta at the main
    output cursor, or ``target`` from there on. noise_var = 0 gives the
    least-squares ZF solution.
    """
    M = _conv_matrix(cursors, n_taps)
    d = np.zeros(M.shape[0])
    t = _target(target)
    n = min(t.size, d.size - (c_pre + tap_pre))
    d[c_pre + tap_pre: c_pre + tap_pre + n] = t[:n]
    A = M.T @ M + noise_var * np.eye(n_taps)
    w = np.linalg.solve(A, M.T @ d)
    if normalize:
        w = w / np.abs(w).sum()
    return w


def mmse_pr_alpha(cursors: np.ndarray, c_pre: int, n_taps: int, tap_pre: int,
                  noise_var: float = 0.0, symbol_power: float = 1.0) -> float:
    """The a in [0, 1] of a 1 + aD target that minimises the FFE's mean-square
    error, main cursor held at 1 (the monic MMSE target).

    With the FFE optimal for each target d = e0 + a e1, the residual is
    J(a) = d' Q d, Q = P I - P^2 M (P M'M + s2 I)^-1 M' (P the symbol power,
    s2 the noise variance): a quadratic in a, minimised at
    a = -(e1' Q e0) / (e1' Q e1). It costs one solve, the same one as
    ``mmse_ffe``, and is what an LMS adapting a with the FFE converges to.
    """
    M = _conv_matrix(cursors, n_taps)
    A = symbol_power * (M.T @ M) + noise_var * np.eye(n_taps)
    m0 = c_pre + tap_pre
    if m0 + 1 >= M.shape[0]:
        return 0.0
    # Q e for the two unit vectors only: columns m0 and m0 + 1
    rows = M[[m0, m0 + 1], :]
    sol = np.linalg.solve(A, rows.T)                     # A^-1 M' e
    q = symbol_power * np.eye(2) - symbol_power ** 2 * (rows @ sol)
    if q[1, 1] <= 0.0:
        return 0.0
    return float(np.clip(-q[0, 1] / q[1, 1], 0.0, 1.0))


def apply_ffe(y_baud: np.ndarray, w: np.ndarray, tap_pre: int) -> np.ndarray:
    """Baud-rate FFE: output[k] = sum_j w[j] * y[k + tap_pre - j], aligned so
    sample k still corresponds to symbol k."""
    full = np.convolve(y_baud, np.asarray(w, dtype=np.float64))
    return full[tap_pre: tap_pre + y_baud.size]


def equalized_cursors(cursors: np.ndarray, w: np.ndarray, c_pre: int,
                      tap_pre: int, target=None) -> tuple[np.ndarray, int]:
    """Channel cursors after FFE; returns (new_cursors, new_pre_count).

    With ``target`` the controlled cursors are taken out -- main * target
    subtracted from the main position on -- so what is returned is the ISI
    the partial-response target does not account for (the main cursor
    itself then reads 0)."""
    out = np.convolve(cursors, w)
    pre = c_pre + tap_pre
    if target is not None:
        t = _target(target)
        main = out[pre]
        n = min(t.size, out.size - pre)
        out = out.copy()
        out[pre: pre + n] -= main * t[:n]
    return out, pre
