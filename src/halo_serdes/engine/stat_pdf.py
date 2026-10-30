"""The statistical engine's distributions: ISI PDFs on a voltage grid.

``isi_pdf`` is the distribution of the residual ISI over the symbol levels;
the per-level pair PDFs condition it on the sampled symbol's neighbours (the
optical noise is binned on them, ``_BINNED_NEIGHBOURS``). Part of
``engine.statistical`` (split out 2026-10-30; the names are importable from
there as before).
"""

from __future__ import annotations

import numpy as np


#: Cursor offsets whose symbols the optical noise is binned on: the next
#: symbol sent (-1) and the previous one (+1). They move the most light
#: along the receive filter's memory.
_BINNED_NEIGHBOURS = (-1, 1)


def _shift_add(dst: np.ndarray, src: np.ndarray, shift_bins: float, weight: float) -> None:
    """dst += weight * src shifted by fractional bins (linear interpolation,
    edge clipping — no wraparound)."""
    i0 = int(np.floor(shift_bins))
    frac = shift_bins - i0
    for off, w in ((i0, (1.0 - frac) * weight), (i0 + 1, frac * weight)):
        if w == 0.0:
            continue
        if off >= 0:
            n = src.size - off
            if n > 0:
                dst[off:] += w * src[:n]
        else:
            n = src.size + off
            if n > 0:
                dst[:n] += w * src[-n:]


def isi_pdf(cursor_amps: np.ndarray, levels_norm: np.ndarray,
            v_centers: np.ndarray, level_probs: np.ndarray | None = None) -> np.ndarray:
    """Distribution of sum_k c_k * a_k over the symbol levels: equiprobable,
    or sent as often as ``level_probs`` says (PAM6's corner32 map sends its
    outer levels less often, ``core.mapping.level_probabilities``)."""
    dv = v_centers[1] - v_centers[0]
    pdf = np.zeros(v_centers.size)
    pdf[v_centers.size // 2] = 1.0  # delta at 0 (grid is symmetric)
    m = levels_norm.size
    w = np.full(m, 1.0 / m) if level_probs is None else np.asarray(level_probs, dtype=np.float64)
    for c in cursor_amps:
        if abs(c) < dv * 1e-6:
            continue
        new = np.zeros_like(pdf)
        for a, pa in zip(levels_norm, w):
            _shift_add(new, pdf, c * a / dv, float(pa))
        pdf = new
    return pdf


def _convolve_centred(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """``a * b`` on ``a``'s grid, both centred on bin ``n // 2`` as ``isi_pdf``
    lays its PDFs out. ``np.convolve(mode="same")`` puts the centre of an
    even-length grid one bin late -- a systematic shift of one ``dv`` per
    convolution, 4-7 % of SER four sigma out on a flat channel, and 8 % (NRZ)
    to 22 % (PAM4) of SER with eight aggressors whose coupling is nil. The
    odd-length noise kernels (``gaussian_kernel``) are not affected: ``same``
    centres those."""
    ic = a.size // 2
    return np.convolve(a, b)[ic: ic + a.size]


def _pair_isi_pdfs(c_pair: np.ndarray, levels_norm: np.ndarray, v_centers: np.ndarray,
                   level_probs: np.ndarray, cond: np.ndarray
                   ) -> tuple[list[np.ndarray], np.ndarray, tuple[np.ndarray, np.ndarray]]:
    """The ISI of the two neighbouring cursors ``c_pair`` = (c(-1), c(+1))
    when the symbols come in pairs (PAM6's corner32 map): per transmitted
    level ``k`` the partner is drawn from ``cond[k]`` and the other
    neighbour from the occupancy. A symbol in the pair's first position has
    its partner after it, on the pre-cursor c(-1), and the free neighbour
    before it on c(+1); in the second position the other way round, each
    half the time. Returns the per-level PDFs, the unconditional one (the
    partner drawn from the occupancy too, what independent symbols give)
    and the free neighbour's PDF per position -- ``(c(+1) alone, c(-1)
    alone)`` -- for the bit count that knows the partner (ROADMAP P2 #33)."""
    c_m, c_p = float(c_pair[0]), float(c_pair[1])
    free_m = isi_pdf(np.array([c_m]), levels_norm, v_centers, level_probs)
    free_p = isi_pdf(np.array([c_p]), levels_norm, v_centers, level_probs)
    per_level = []
    for k in range(levels_norm.size):
        part_m = isi_pdf(np.array([c_m]), levels_norm, v_centers, cond[k])   # first position
        part_p = isi_pdf(np.array([c_p]), levels_norm, v_centers, cond[k])   # second position
        per_level.append(0.5 * (_convolve_centred(part_m, free_p)
                                + _convolve_centred(part_p, free_m)))
    return per_level, _convolve_centred(free_p, free_m), (free_p, free_m)
