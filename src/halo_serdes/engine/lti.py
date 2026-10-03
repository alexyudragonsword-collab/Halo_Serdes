"""Chunked (overlap-save) FFT filtering for long waveforms.

Keeps peak memory bounded for multi-million-sample runs (224G budget) while
remaining exactly equal to full-array convolution truncated to len(x).
"""

from __future__ import annotations

import numpy as np


def fft_filter(x: np.ndarray, h: np.ndarray, chunk: int = 1 << 20) -> np.ndarray:
    """y = (x * h)[: len(x)] via overlap-save block convolution."""
    x = np.asarray(x, dtype=np.float64)
    h = np.asarray(h, dtype=np.float64)
    nh = h.size
    if x.size + nh <= chunk * 2:  # small enough: single FFT
        n = int(2 ** np.ceil(np.log2(x.size + nh - 1)))
        y = np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(h, n), n)
        return y[: x.size]
    block = chunk
    nfft = int(2 ** np.ceil(np.log2(block + nh - 1)))
    H = np.fft.rfft(h, nfft)
    out = np.empty(x.size, dtype=np.float64)
    overlap = np.zeros(nh - 1)
    pos = 0
    while pos < x.size:
        seg = x[pos: pos + block]
        n_seg = seg.size
        y = np.fft.irfft(np.fft.rfft(seg, nfft) * H, nfft)[: n_seg + nh - 1]
        y[: nh - 1] += overlap
        out[pos: pos + n_seg] = y[: n_seg]
        overlap = y[n_seg: n_seg + nh - 1].copy()
        if overlap.size < nh - 1:
            overlap = np.pad(overlap, (0, nh - 1 - overlap.size))
        pos += n_seg
    return out


def receiver_awgn(rng: np.random.Generator, sigma: float, n: int, osr: int) -> np.ndarray:
    """Receiver noise on the oversampled grid: ``n`` draws from ``rng``,
    brick-wall band-limited to [0, baud] and scaled to RMS ``sigma``.

    Why not one independent draw per sample: the time engines sample the
    waveform at fractional positions through a cubic interpolator, which
    averages neighbouring samples -- on per-sample white noise it passes only
    sum c(mu)^2 of the variance, 0.64 at mid-sample, so the noise at the
    slicer depended on where the CDR happened to sit (up to 1.9 dB, 4.6x in
    BER between two phases 0.3 samples apart). Band-limited to the baud rate
    the waveform is smooth over the interpolator's four samples and every
    phase sees ``sigma``; and since the autocorrelation sinc(2 baud tau) is
    zero at multiples of UI/2, data samples a UI apart and edge samples half
    a UI away stay uncorrelated, as they were.
    """
    w = rng.normal(size=n)
    if n < 2:
        return sigma * w
    spec = np.fft.rfft(w)
    k_c = min(n // osr, spec.size - 1)       # f_k = k fs / n <= baud = fs / osr
    spec[k_c + 1:] = 0.0
    # Parseval: unit-variance white noise keeps (1 + 2 k_c) / n of its power
    # (DC once, every other kept bin twice; k_c < n / 2 here)
    return sigma * np.sqrt(n / (1.0 + 2.0 * k_c)) * np.fft.irfft(spec, n)
