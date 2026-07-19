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
        x = _farrow_local(y, p) * gains[lane] + offsets[lane] + noise[k]
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


if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _farrow_local = numba.njit(cache=True, inline="always")(_farrow_py)
        adc_rx = numba.njit(cache=True)(_adc_rx_py)
    except ImportError:
        adc_rx = _adc_rx_py
else:
    adc_rx = _adc_rx_py
