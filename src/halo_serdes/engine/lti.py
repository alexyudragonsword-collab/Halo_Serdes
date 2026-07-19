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
