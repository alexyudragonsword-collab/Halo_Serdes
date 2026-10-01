"""The two receiver kernels exactly as they were before they took a receiver-clock input.

Frozen from commit 35aa9b3 (``src/halo_serdes/cdr/kernels.py::_ms_rx_py`` and
``src/halo_serdes/cdr/adc_kernel.py::_adc_rx_py``), pure Python, so that
``tests/test_rx_clock.py`` can run old and new side by side on the same
machine and demand bit-identical output for an all-zero offset array. A
stored hash of float arrays was the first attempt and failed across CI's
Python/numpy versions on the very first run (3.11 agreed, 3.10 did not);
same-process comparison does not depend on anyone's libm.

Do not edit: the point of this file is that it never changes.
"""

from __future__ import annotations

import numpy as np

from halo_serdes.cdr.kernels import _farrow_py


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
        y_d = _farrow_py(y, pos)
        y_e = _farrow_py(y, pos - osr / 2.0)

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


def _adc_rx_py(y: np.ndarray, osr: int, pos0: float, n_symbols: int,
               levels: np.ndarray,
               n_lanes: int, offsets: np.ndarray, gains: np.ndarray,
               skews: np.ndarray, q_step: float, code_max: int,
               noise: np.ndarray,
               w_ffe: np.ndarray, n_pre_ffe: int, mu_ffe: float,
               w_dfe: np.ndarray, mu_dfe: float,
               kp: float, ki: float, clamp: float, pd_offset: float,
               pd_use_ffe: int, loop_latency_blocks: int,
               ref_idx: np.ndarray, train_len: int, adapt_start: int):
    """Returns (dec, y_slicer, phase, w_ffe_out, w_dfe_out, lane_of, q_codes)."""
    nl = levels.size
    nf = w_ffe.size
    nd = w_dfe.size
    wf = w_ffe.copy()
    wd = w_dfe.copy()

    dec = np.zeros(n_symbols, dtype=np.int64)
    y_sl = np.zeros(n_symbols, dtype=np.float64)
    phase = np.zeros(n_symbols, dtype=np.float64)
    lane_of = np.zeros(n_symbols, dtype=np.int64)
    q_hist = np.zeros(n_symbols, dtype=np.float64)  # dequantized samples

    # PD state
    acc = 0.0
    n_acc = 0
    integ = 0.0
    corr_now = 0.0
    lat = loop_latency_blocks
    corr_queue = np.zeros(lat + 1, dtype=np.float64)
    qi = 0

    pos = pos0
    for k in range(n_symbols):
        lane = k % n_lanes
        p = pos + skews[lane]
        if p + osr + 2 >= y.size or p < 1:
            n_symbols = k
            break
        x = _farrow_py(y, p) * gains[lane] + offsets[lane] + noise[k]
        # mid-rise quantizer
        code = np.floor(x / q_step)
        if code > code_max:
            code = code_max
        elif code < -code_max - 1:
            code = -code_max - 1
        q = (code + 0.5) * q_step
        q_hist[k] = q
        phase[k] = pos
        lane_of[k] = lane

        # FFE produces symbol s = k - n_pre_ffe
        s = k - n_pre_ffe
        if s >= 0:
            v_ffe = 0.0
            for i in range(nf):
                j = k - i
                if j >= 0:
                    v_ffe += wf[i] * q_hist[j]
            fb = 0.0
            for d in range(nd):
                jj = s - 1 - d
                if jj >= 0:
                    fb += wd[d] * levels[dec[jj]]
            v = v_ffe - fb
            y_sl[s] = v

            best = 0
            bd = abs(v - levels[0])
            for m in range(1, nl):
                dd = abs(v - levels[m])
                if dd < bd:
                    bd = dd
                    best = m
            if s < train_len and ref_idx[s] >= 0:
                dec[s] = ref_idx[s]
            else:
                dec[s] = best

            # joint LMS
            if s >= adapt_start and (mu_ffe > 0.0 or mu_dfe > 0.0):
                e = v - levels[dec[s]]
                if mu_ffe > 0.0:
                    for i in range(nf):
                        j = k - i
                        if j >= 0:
                            wf[i] -= mu_ffe * e * q_hist[j]
                if mu_dfe > 0.0:
                    for d in range(nd):
                        jj = s - 1 - d
                        if jj >= 0:
                            wd[d] += mu_dfe * e * levels[dec[jj]]

            # --- Mueller-Muller PD on symbol s-1 (needs x[s] lookahead) ---
            if s >= 2:
                if pd_use_ffe == 1:
                    x0 = y_sl[s - 2]
                    x1 = y_sl[s - 1]
                    x2 = v
                else:
                    x0 = q_hist[s - 2]
                    x1 = q_hist[s - 1]
                    x2 = q_hist[s]
                a0 = 1.0 if x0 > 0 else -1.0
                a2 = 1.0 if x2 > 0 else -1.0
                acc += x1 * (a2 - a0)
                n_acc += 1
                if n_acc >= n_lanes:
                    # E[acc/N] = h(-1) - h(+1): positive when sampling late,
                    # so the phase correction takes the negated PD.
                    pd = -(acc / n_lanes) - pd_offset
                    integ += ki * pd
                    c = kp * pd + integ
                    if clamp > 0.0:
                        if c > clamp:
                            c = clamp
                        elif c < -clamp:
                            c = -clamp
                    corr_queue[qi] = c
                    qi = (qi + 1) % (lat + 1)
                    corr_now = corr_queue[qi]  # oldest entry: latency delay
                    acc = 0.0
                    n_acc = 0

        pos += osr + corr_now / n_lanes

    return (dec[:n_symbols], y_sl[:n_symbols], phase[:n_symbols], wf, wd,
            lane_of[:n_symbols], q_hist[:n_symbols])
