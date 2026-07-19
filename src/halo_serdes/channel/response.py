"""Frequency response <-> impulse response conversion utilities.

Conventions:
- Frequency grids are uniform, start at DC, single-sided: f = [0, df, ..., f_max].
- ``freq2impulse`` returns the impulse response in *per-sample-weight* form
  (dimensionless): sum(h) == H(0), so ``np.convolve(x, h)`` maps volts to volts.
  This matches serdespy's conjugate-symmetric IFFT reconstruction.
- ``zero_pad_to_dt`` extends the grid with zeros so the IFFT lands exactly on a
  requested time step (serdespy ``zero_pad`` technique, with an exact-grid fix).
"""

from __future__ import annotations

import numpy as np

from ..core.waveform import Waveform


def _check_uniform_grid(f: np.ndarray) -> float:
    f = np.asarray(f, dtype=np.float64)
    if f[0] != 0.0:
        raise ValueError("frequency grid must start at DC (f[0] == 0)")
    df = f[1] - f[0]
    if not np.allclose(np.diff(f), df, rtol=1e-6):
        raise ValueError("frequency grid must be uniform")
    return df


def freq2impulse(H: np.ndarray, f: np.ndarray) -> Waveform:
    """Single-sided H(f) on a uniform DC-inclusive grid -> real impulse response.

    Equivalent to serdespy's conjugate-symmetric reconstruction
    ``ifft([H, conj(flip(H[1:-1]))])`` but via ``irfft``. Resulting
    dt = 1 / (2 * f_max).
    """
    df = _check_uniform_grid(f)
    n_time = 2 * (len(H) - 1)
    h = np.fft.irfft(H, n=n_time)
    dt = 1.0 / (df * n_time)
    return Waveform(h, dt)


def impulse2freq(h: Waveform) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of ``freq2impulse`` (exact round-trip for even-length h)."""
    H = np.fft.rfft(h.y)
    f = np.fft.rfftfreq(h.y.size, d=h.dt)
    return f, H


def zero_pad_to_dt(H: np.ndarray, f: np.ndarray, dt: float) -> tuple[np.ndarray, np.ndarray]:
    """Zero-pad H(f) so that ``freq2impulse`` yields time step <= ``dt``.

    Returns the padded ``(H_zp, f_zp)`` on the same df grid extended to
    f_max' = 1/(2*dt') where dt' = 1/(2*n*df) for the smallest integer n
    giving dt' <= dt. Use the returned grid to recover the exact dt'.
    """
    df = _check_uniform_grid(f)
    n_needed = int(np.ceil(1.0 / (2.0 * dt * df)))  # points above DC
    if n_needed < len(H) - 1:
        raise ValueError(
            f"requested dt={dt:.3e} needs f_max={n_needed * df:.3e} < existing grid; "
            "decimation not supported — choose a finer dt or coarser grid"
        )
    f_zp = np.arange(n_needed + 1) * df
    H_zp = np.zeros(n_needed + 1, dtype=complex)
    H_zp[: len(H)] = H
    return H_zp, f_zp


def trim_impulse(h: Waveform, keep_energy: float = 0.999,
                 min_len: int | None = None, max_len: int | None = None) -> tuple[Waveform, int]:
    """Extract the significant span of an impulse response.

    PyBERT-style criterion: find the window containing ``keep_energy`` of the
    first-difference energy (robust against DC tails). Returns the trimmed
    waveform and the start-sample index in the original array.
    """
    y = h.y
    d = np.diff(y, prepend=y[0])
    e = np.cumsum(d * d)
    if e[-1] == 0.0:
        return h, 0
    e = e / e[-1]
    lo = float((1.0 - keep_energy) / 2.0)
    start = int(np.searchsorted(e, lo))
    stop = int(np.searchsorted(e, 1.0 - lo)) + 1
    if min_len is not None and stop - start < min_len:
        pad = (min_len - (stop - start) + 1) // 2
        start = max(0, start - pad)
        stop = min(y.size, start + min_len)
    if max_len is not None and stop - start > max_len:
        stop = start + max_len
    return Waveform(y[start:stop], h.dt, h.t0 + start * h.dt), start


def pulse_from_impulse(h: Waveform, osr: int) -> Waveform:
    """Single-UI pulse response: h convolved with a 1-UI rectangle of ones
    (per-sample-weight convention keeps volts as volts)."""
    p = np.convolve(h.y, np.ones(osr))[: h.y.size]
    return Waveform(p, h.dt, h.t0)
