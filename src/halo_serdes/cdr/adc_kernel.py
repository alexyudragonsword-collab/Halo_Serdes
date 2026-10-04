"""ADC-DSP RX serial kernel: TI-ADC sampling/quantization -> baud-rate FFE ->
DFE -> slicer, with Mueller-Muller baud-rate CDR closing the loop to the
sampling phase (behavioral phase interpolator).

Follows the DragonPHY2 structure: linear MM PD e[n] = x[n]*(sign(x[n+1]) -
sign(x[n-1])) block-averaged over the interleave factor, shift-style Kp/Ki
gains, clamp, PD input selectable between raw ADC codes and FFE output, and
explicit loop latency in blocks. Adaptation is full LMS (FFE + DFE jointly),
data-aided during training then decision-directed.
"""

from __future__ import annotations

import os

import numpy as np

from .kernels import _farrow_py

_farrow_local = _farrow_py


def _adc_rx_py(y: np.ndarray, osr: int, pos0: float, n_symbols: int,
               levels: np.ndarray,
               n_lanes: int, offsets: np.ndarray, gains: np.ndarray,
               skews: np.ndarray, q_step: float, code_max: int,
               noise: np.ndarray,
               w_ffe: np.ndarray, n_pre_ffe: int, mu_ffe: float,
               w_dfe: np.ndarray, mu_dfe: float,
               kp: float, ki: float, clamp: float, pd_offset: float,
               pd_use_ffe: int, loop_latency_blocks: int,
               ref_idx: np.ndarray, train_len: int, adapt_start: int,
               rx_clock_offset_samples: np.ndarray,
               pr_alpha: float, pr_mode: int, pr_levels: np.ndarray,
               mu_alpha: float, alpha_out: np.ndarray,
               pr_beta: float, pr_nt: int):
    """Returns (dec, y_slicer, phase, w_ffe_out, w_dfe_out, lane_of, q_codes).

    ``rx_clock_offset_samples[k]`` is the receiver sampling clock's own error
    at symbol ``k`` [samples], added to the loop phase before the lane skew;
    ``phase`` reports the position actually sampled (offset included). Zeros
    reproduce the ideal-clock kernel bit for bit.

    Partial response (``pr_mode`` > 0): the FFE is driven towards
    ``levels[x_s] + pr_alpha * levels[x_{s-1}]``, the DFE starts after the
    controlled cursor, and the per-symbol decision -- for the LMS and the CDR;
    the final one is a sequence detector's -- either subtracts ``pr_alpha``
    times the previous decision before slicing (``pr_mode`` 1, propagates
    errors) or, for precoded 1 + D, slices to the 2N - 1 composite
    ``pr_levels`` and takes the line symbol as (q - x_{s-1}) mod N
    (``pr_mode`` 2: the user symbol is q mod N, so an error does not
    propagate). The Mueller-Muller detector on equalised samples reads the
    same sample less ``pr_alpha`` times the previous decision: on the shaped
    pulse h(+1) = alpha and the detector's gradient fades as alpha grows (the
    loop walked off 0.4-3 UI at alpha 0.75-1), on the residual it is a delta
    pulse again. ``pr_mode`` 0 is the delta target, bit for bit.

    ``mu_alpha`` > 0 adapts the controlled cursor with the FFE (``pr_mode``
    1 only; the composite slicer assumes a = 1): the same error, the gradient
    taken in a, normalised by the mean symbol power and clipped to [0, 1].
    ``alpha_out[0]`` returns the a the run ended on.

    ``pr_nt`` 3 is a 1 + aD + bD^2 target: ``pr_beta`` times the decision two
    symbols back is subtracted as well, everywhere ``pr_alpha`` times the
    previous one is, and the DFE starts after both controlled cursors.
    """
    nl = levels.size
    nf = w_ffe.size
    nd = w_dfe.size
    nt = pr_nt if pr_mode > 0 else 1    # cursors the target accounts for
    a_pr = pr_alpha
    p_sym = 0.0
    for m in range(nl):
        p_sym += levels[m] * levels[m]
    p_sym = p_sym / nl
    wf = w_ffe.copy()
    wd = w_dfe.copy()

    dec = np.zeros(n_symbols, dtype=np.int64)
    # line-symbol estimates the DFE and the PR residual subtract; the same as
    # dec except for precoded 1 + D, where dec's mod-N chain is right for the
    # user symbol but, after one wrong composite, wrong for every line symbol
    # that follows. This chain clips instead, and the composite's extremes
    # (both symbols at the bottom or top level) resynchronise it.
    xl = np.zeros(n_symbols, dtype=np.int64)
    y_sl = np.zeros(n_symbols, dtype=np.float64)
    r_sl = np.zeros(n_symbols, dtype=np.float64)   # PR: sample less the controlled cursor
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
        p_clk = pos + rx_clock_offset_samples[k]
        p = p_clk + skews[lane]
        if p + osr + 2 >= y.size or p < 1:
            n_symbols = k
            break
        x = _farrow_local(y, p) * gains[lane] + offsets[lane] + noise[k]
        # mid-rise quantizer
        code = np.floor(x / q_step)
        if code > code_max:
            code = code_max
        elif code < -code_max - 1:
            code = -code_max - 1
        q = (code + 0.5) * q_step
        q_hist[k] = q
        phase[k] = p_clk
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
                jj = s - nt - d
                if jj >= 0:
                    fb += wd[d] * levels[xl[jj]]
            v = v_ffe - fb
            y_sl[s] = v

            prev = 0.0
            if pr_mode > 0 and s >= 1:
                prev = levels[xl[s - 1]]
            ctl = a_pr * prev
            if nt == 3 and s >= 2:
                ctl = ctl + pr_beta * levels[xl[s - 2]]
            r_sl[s] = v - ctl
            q = 0
            if pr_mode == 2:
                bd = abs(v - pr_levels[0])
                for m in range(1, pr_levels.size):
                    dd = abs(v - pr_levels[m])
                    if dd < bd:
                        bd = dd
                        q = m
                best = q - (dec[s - 1] if s >= 1 else 0)
                best = best % nl
            else:
                u = v - ctl
                best = 0
                bd = abs(u - levels[0])
                for m in range(1, nl):
                    dd = abs(u - levels[m])
                    if dd < bd:
                        bd = dd
                        best = m
            training = s < train_len and ref_idx[s] >= 0
            if training:
                dec[s] = ref_idx[s]
                xl[s] = ref_idx[s]
            else:
                dec[s] = best
                if pr_mode == 2:
                    xc = q - (xl[s - 1] if s >= 1 else 0)
                    if xc < 0:
                        xc = 0
                    elif xc > nl - 1:
                        xc = nl - 1
                    xl[s] = xc
                else:
                    xl[s] = best

            # joint LMS
            if s >= adapt_start and (mu_ffe > 0.0 or mu_dfe > 0.0):
                if pr_mode == 0:
                    e = v - levels[dec[s]]
                elif pr_mode == 2 and not training:
                    e = v - pr_levels[q]
                else:
                    e = v - (levels[xl[s]] + ctl)
                    if mu_alpha > 0.0 and pr_mode == 1 and p_sym > 0.0:
                        a_pr += mu_alpha * e * prev / p_sym
                        if a_pr < 0.0:
                            a_pr = 0.0
                        elif a_pr > 1.0:
                            a_pr = 1.0
                if mu_ffe > 0.0:
                    for i in range(nf):
                        j = k - i
                        if j >= 0:
                            wf[i] -= mu_ffe * e * q_hist[j]
                if mu_dfe > 0.0:
                    for d in range(nd):
                        jj = s - nt - d
                        if jj >= 0:
                            wd[d] += mu_dfe * e * levels[xl[jj]]

            # --- Mueller-Muller PD on symbol s-1 (needs x[s] lookahead) ---
            if s >= 2:
                if pd_use_ffe == 1 and pr_mode > 0:
                    x0 = r_sl[s - 2]
                    x1 = r_sl[s - 1]
                    x2 = r_sl[s]
                elif pd_use_ffe == 1:
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

    alpha_out[0] = a_pr
    return (dec[:n_symbols], y_sl[:n_symbols], phase[:n_symbols], wf, wd,
            lane_of[:n_symbols], q_hist[:n_symbols])


if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _farrow_local = numba.njit(cache=True, inline="always")(_farrow_py)
        adc_rx = numba.njit(cache=True)(_adc_rx_py)
    except ImportError:
        adc_rx = _adc_rx_py
else:
    adc_rx = _adc_rx_py
