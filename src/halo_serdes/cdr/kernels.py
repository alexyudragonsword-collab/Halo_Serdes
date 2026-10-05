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


def _ms_rx_core_py(y: np.ndarray, osr: int, k0: int, k1: int,
                   levels: np.ndarray, w: np.ndarray, corr: np.ndarray,
                   mu_dfe: float, n_ave: int, kp: float, ki: float,
                   clamp: float, sum_alpha: float, ref_idx: np.ndarray,
                   train_len: int, adapt_start: int, tap1_unrolled: int,
                   branch_offsets: np.ndarray,
                   rx_clock_offset_samples: np.ndarray,
                   fs: np.ndarray, ist: np.ndarray,
                   dec: np.ndarray, y_sum: np.ndarray, phase: np.ndarray,
                   pd_hist: np.ndarray, w_hist: np.ndarray):
    """Symbols ``k0 <= k < k1`` of the loop :func:`_ms_rx_py` describes.

    Everything the loop carries from one symbol to the next lives in the
    arguments: the DFE taps ``w`` and the LMS accumulator ``corr`` (updated
    in place), ``fs`` = [position, integrator, summing-node feedback, sign of
    the previous data sample], ``ist`` = [LMS batch count, tap-history row],
    and the output arrays, which the decisions and feedback read back from.
    So any split of [0, n) into consecutive calls runs the same arithmetic in
    the same order as one call -- bit for bit.

    Returns (symbols done, 1 if the waveform ran out else 0).
    """
    nl = levels.size
    nt = w.size
    n_hist = w_hist.shape[0]
    pos = fs[0]
    integ = fs[1]
    fb_f = fs[2]
    s_prev = fs[3]
    n_acc = ist[0]
    h_i = ist[1]

    for k in range(k0, k1):
        if k % n_ave == 0 and h_i < n_hist:
            for i in range(nt):
                w_hist[h_i, i] = w[i]
            h_i += 1
        # --- data & edge samples (fractional positions) ---
        # The sampler fires where the loop says plus where its own clock
        # wandered to. The guard only exists for a non-zero offset: with an
        # ideal clock the end-of-iteration check below is the one that ran
        # before this parameter existed, and its truncation semantics stay.
        off = rx_clock_offset_samples[k]
        p_d = pos + off
        if off != 0.0 and (p_d + osr + 2 >= y.size or p_d - osr < 1):
            fs[0] = pos
            fs[1] = integ
            fs[2] = fb_f
            fs[3] = s_prev
            ist[0] = n_acc
            ist[1] = h_i
            return k, 1
        y_d = _farrow(y, p_d)
        y_e = _farrow(y, p_d - osr / 2.0)

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
        phase[k] = p_d

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
            fs[0] = pos
            fs[1] = integ
            fs[2] = fb_f
            fs[3] = s_prev
            ist[0] = n_acc
            ist[1] = h_i
            return k + 1, 1

    fs[0] = pos
    fs[1] = integ
    fs[2] = fb_f
    fs[3] = s_prev
    ist[0] = n_acc
    ist[1] = h_i
    return k1, 0


class MsRxRun:
    """One mixed-signal receiver run, advanced in as many calls as wanted.

    ``advance(k1)`` runs the loop up to symbol ``k1`` (or until the waveform
    runs out); ``result()`` gives what :func:`ms_rx` returns. One ``advance``
    to ``n_symbols`` is :func:`ms_rx`; any sequence of smaller ones gives the
    same arrays bit for bit, which is what lets the time engine report
    progress and stop a run between chunks.
    """

    def __init__(self, y, osr, phase0, n_symbols, levels, w_dfe, mu_dfe,
                 n_ave, kp, ki, clamp, sum_alpha, ref_idx, train_len,
                 adapt_start, tap1_unrolled, branch_offsets,
                 rx_clock_offset_samples, core=None):
        nt = w_dfe.size
        self.core = core if core is not None else _ms_rx_core
        self.args = (y, osr)
        self.params = (levels, w_dfe.copy(), np.zeros(nt, dtype=np.float64),
                       mu_dfe, n_ave, kp, ki, clamp, sum_alpha, ref_idx,
                       train_len, adapt_start, tap1_unrolled, branch_offsets,
                       rx_clock_offset_samples)
        self.fs = np.array([phase0, 0.0, 0.0, 1.0], dtype=np.float64)
        self.ist = np.zeros(2, dtype=np.int64)
        self.n_symbols = n_symbols
        self.dec = np.zeros(n_symbols, dtype=np.int64)
        self.y_sum = np.zeros(n_symbols, dtype=np.float64)
        self.phase = np.zeros(n_symbols, dtype=np.float64)
        self.pd_hist = np.zeros(n_symbols, dtype=np.int8)
        # tap trajectory: one row per n_ave symbols (adaptation batch cadence)
        self.w_hist = np.zeros((n_symbols // n_ave + 2, nt), dtype=np.float64)
        self.done = 0
        self.stopped = False

    def advance(self, k1: int) -> int:
        k1 = min(int(k1), self.n_symbols)
        if self.stopped or k1 <= self.done:
            return self.done
        y, osr = self.args
        done, out = self.core(y, osr, self.done, k1, *self.params, self.fs, self.ist,
                              self.dec, self.y_sum, self.phase, self.pd_hist, self.w_hist)
        self.done = int(done)
        self.stopped = bool(out)
        return self.done

    @property
    def finished(self) -> bool:
        return self.stopped or self.done >= self.n_symbols

    def result(self):
        n = self.done
        return (self.dec[:n], self.y_sum[:n], self.phase[:n], self.params[1],
                self.pd_hist[:n], self.w_hist[:int(self.ist[1])])


def _ms_rx_with(core):
    def ms_rx(y: np.ndarray, osr: int, phase0: float, n_symbols: int,
              levels: np.ndarray, w_dfe: np.ndarray, mu_dfe: float,
              n_ave: int, kp: float, ki: float, clamp: float,
              sum_alpha: float, ref_idx: np.ndarray, train_len: int,
              adapt_start: int, tap1_unrolled: int,
              branch_offsets: np.ndarray,
              rx_clock_offset_samples: np.ndarray):
        run = MsRxRun(y, osr, phase0, n_symbols, levels, w_dfe, mu_dfe, n_ave,
                      kp, ki, clamp, sum_alpha, ref_idx, train_len, adapt_start,
                      tap1_unrolled, branch_offsets, rx_clock_offset_samples,
                      core=core)
        run.advance(n_symbols)
        return run.result()
    return ms_rx


_MS_RX_DOC = """Joint DFE + Alexander bang-bang CDR loop.

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
        rx_clock_offset_samples: the receiver's own sampling-clock error per
            symbol [samples] (length >= n_symbols). Added to the loop's
            phase for both the data and the edge sample, so the loop sees
            and tracks the *difference* between the two clocks, exactly as a
            real CDR does; zeros give the pre-existing ideal-clock behaviour
            bit for bit.

    The loop itself is :func:`_ms_rx_core_py`; :class:`MsRxRun` runs it in
    chunks with the same result.

    Returns:
        dec (int64[n]), y_sum (float64[n] summing-node values),
        phase (float64[n] actual data sample positions, offset included),
        w_out, pd_hist (int8[n]),
        w_hist (float64[n_batches, nt] tap trajectory, one row per n_ave).
    """


# numba resolves the `_farrow` global at (lazy) compile time, so rebinding it
# to the jitted version before first call is sufficient.
_farrow = _farrow_py
_ms_rx_core = _ms_rx_core_py
#: the pure-Python loop, whatever the JIT does (tests compare the two)
_ms_rx_py = _ms_rx_with(_ms_rx_core_py)
_ms_rx_py.__doc__ = _MS_RX_DOC
ms_rx = _ms_rx_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _farrow = numba.njit(cache=True, inline="always")(_farrow_py)
        _ms_rx_core = numba.njit(cache=True)(_ms_rx_core_py)
        ms_rx = _ms_rx_with(_ms_rx_core)
        ms_rx.__doc__ = _MS_RX_DOC
    except ImportError:
        pass
