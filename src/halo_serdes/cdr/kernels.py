"""Mixed-signal RX serial kernel: joint DFE + bang-bang CDR, per-symbol loop.

The CDR outputs a *fractional* sampling position advanced symbol-by-symbol;
data/edge samples are taken by Farrow cubic interpolation on the oversampled
grid (never quantized to the grid — grid quantization would inject ~UI/OSR
phase noise, worse than the jitter being measured).

Pure-Python function defines the semantics; numba compiles the same source
when available (HALO_NO_JIT=1 forces fallback; CI runs both paths).
"""

from __future__ import annotations

import os

import numpy as np


def _farrow_py(y: np.ndarray, pos: float) -> float:
    """Catmull-Rom cubic interpolation at fractional index ``pos``."""
    i = int(np.floor(pos))
    mu = pos - i
    y0 = y[i - 1]
    y1 = y[i]
    y2 = y[i + 1]
    y3 = y[i + 2]
    return 0.5 * (2.0 * y1 + (-y0 + y2) * mu
                  + (2.0 * y0 - 5.0 * y1 + 4.0 * y2 - y3) * mu * mu
                  + (-y0 + 3.0 * y1 - 3.0 * y2 + y3) * mu * mu * mu)


def _ms_rx_py(y: np.ndarray, osr: int, phase0: float, n_symbols: int,
              levels: np.ndarray, w_dfe: np.ndarray, mu_dfe: float,
              n_ave: int, kp: float, ki: float, clamp: float,
              sum_alpha: float, ref_idx: np.ndarray, train_len: int,
              adapt_start: int, tap1_unrolled: int,
              branch_offsets: np.ndarray):
    """Joint DFE + Alexander bang-bang CDR loop.

    Args:
        y: oversampled RX waveform (CTLE/VGA output).
        osr: nominal samples per UI.
        phase0: initial data-sampling position [samples].
        levels: slicer levels (ascending), scaled to the received main cursor.
        w_dfe: initial DFE taps (postcursor, index 0 = 1 UI back). Copied.
        mu_dfe: sign-sign LMS step (0 disables adaptation).
        n_ave: LMS batch length (accumulate then update, PyBERT style).
        kp/ki: CDR proportional/integral gains [samples per PD unit].
        clamp: max |phase correction| per symbol [samples] (<=0: none).
        sum_alpha: summing-node 1-pole IIR coefficient for the DFE feedback
            (1.0 = ideal infinite-bandwidth summing node).
        ref_idx: training symbol indices aligned to decisions (-1 = unknown).
        train_len: decisions [0, train_len) use data-aided error/feedback.
        adapt_start: first symbol index at which LMS updates run (lets the
            CDR settle first — the mandated staged startup).
        tap1_unrolled: 1 = speculative/loop-unrolled first DFE tap — the tap-1
            correction becomes a per-branch slicer threshold selected (muxed)
            by the previous decision, and bypasses the summing-node bandwidth
            model entirely; taps >= 2 stay on the analog feedback path.
            0 = direct analog feedback for all taps (all through sum_alpha).
        branch_offsets: per-branch comparator offset [V], one entry per level
            hypothesis of the previous symbol (unrolled mode only; pass zeros
            of length len(levels) otherwise).

    Returns:
        dec (int64[n]), y_sum (float64[n] summing-node values),
        phase (float64[n] data sample positions), w_out, pd_hist (int8[n]),
        w_hist (float64[n_batches, nt] tap trajectory, one row per n_ave).
    """
    nl = levels.size
    nt = w_dfe.size
    w = w_dfe.copy()
    dec = np.zeros(n_symbols, dtype=np.int64)
    y_sum = np.zeros(n_symbols, dtype=np.float64)
    phase = np.zeros(n_symbols, dtype=np.float64)
    pd_hist = np.zeros(n_symbols, dtype=np.int8)
    corr = np.zeros(nt, dtype=np.float64)
    # tap trajectory: one row per n_ave symbols (adaptation batch cadence)
    n_hist = n_symbols // n_ave + 2
    w_hist = np.zeros((n_hist, nt), dtype=np.float64)
    h_i = 0

    pos = phase0
    integ = 0.0
    fb_f = 0.0  # summing-node filtered feedback
    n_acc = 0
    s_prev = 1.0  # sign of previous data sample

    for k in range(n_symbols):
        if k % n_ave == 0 and h_i < n_hist:
            for i in range(nt):
                w_hist[h_i, i] = w[i]
            h_i += 1
        # --- data & edge samples (fractional positions) ---
        y_d = _farrow(y, pos)
        y_e = _farrow(y, pos - osr / 2.0)

        # --- DFE feedback ---
        # direct mode: all taps through the summing node (sum_alpha settling).
        # unrolled mode: tap 1 is a speculative threshold offset muxed by the
        # previous decision (instantaneous, escapes sum_alpha); taps >= 2
        # remain on the analog feedback path.
        first_tap = 1 if (tap1_unrolled == 1 and nt > 0) else 0
        fb = 0.0
        for i in range(first_tap, nt):
            j = k - 1 - i
            if j >= 0:
                fb += w[i] * levels[dec[j]]
        fb_f += sum_alpha * (fb - fb_f)
        v = y_d - fb_f
        if tap1_unrolled == 1 and nt > 0 and k >= 1:
            prev = dec[k - 1]
            v = v - w[0] * levels[prev] + branch_offsets[prev]
        y_sum[k] = v
        phase[k] = pos

        # --- slicer (nearest level) ---
        best = 0
        bd = abs(v - levels[0])
        for m in range(1, nl):
            d = abs(v - levels[m])
            if d < bd:
                bd = d
                best = m
        # data-aided override during training
        if k < train_len and ref_idx[k] >= 0:
            dec[k] = ref_idx[k]
        else:
            dec[k] = best

        # --- sign-sign LMS on the DFE taps ---
        if mu_dfe > 0.0 and k >= adapt_start:
            e = v - levels[dec[k]]
            se = 1.0 if e > 0 else -1.0
            for i in range(nt):
                j = k - 1 - i
                if j >= 0:
                    sd = 1.0 if levels[dec[j]] > 0 else -1.0
                    corr[i] += se * sd
            n_acc += 1
            if n_acc >= n_ave:
                for i in range(nt):
                    w[i] += mu_dfe * corr[i] / n_ave
                    corr[i] = 0.0
                n_acc = 0

        # --- Alexander bang-bang phase detector (sign domain) ---
        s_cur = 1.0 if y_d > 0 else -1.0
        s_edge = 1.0 if y_e > 0 else -1.0
        pd = 0
        if s_cur != s_prev:  # data transition
            # edge sample equals OLD data -> sampled before the transition ->
            # clock early -> move later (+). equals NEW data -> clock late (-).
            pd = 1 if s_edge == s_prev else -1
        pd_hist[k] = pd
        s_prev = s_cur

        # --- loop filter & phase advance ---
        integ += ki * pd
        corr_step = kp * pd + integ
        if clamp > 0.0:
            if corr_step > clamp:
                corr_step = clamp
            elif corr_step < -clamp:
                corr_step = -clamp
        pos += osr + corr_step
        if pos + osr + 2 >= y.size:
            # truncate: out of waveform
            return (dec[: k + 1], y_sum[: k + 1], phase[: k + 1], w,
                    pd_hist[: k + 1], w_hist[:h_i])

    return dec, y_sum, phase, w, pd_hist, w_hist[:h_i]


# numba resolves the `_farrow` global at (lazy) compile time, so rebinding it
# to the jitted version before first call is sufficient.
_farrow = _farrow_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _farrow = numba.njit(cache=True, inline="always")(_farrow_py)
        ms_rx = numba.njit(cache=True)(_ms_rx_py)
    except ImportError:
        ms_rx = _ms_rx_py
else:
    ms_rx = _ms_rx_py
