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

from .kernels import MsRxRun, _farrow_py, check_window_status

_farrow_local = _farrow_py


def _adc_rx_core_py(y: np.ndarray, y_off: int, n_total: int, osr: int,
                    k0: int, k1: int,
                    levels: np.ndarray,
                    n_lanes: int, offsets: np.ndarray, gains: np.ndarray,
                    skews: np.ndarray, q_step: float, code_max: int,
                    noise: np.ndarray,
                    wf: np.ndarray, n_pre_ffe: int, mu_ffe: float,
                    wd: np.ndarray, mu_dfe: float,
                    kp: float, ki: float, clamp: float, pd_offset: float,
                    pd_use_ffe: int, loop_latency_blocks: int,
                    ref_idx: np.ndarray, train_len: int, adapt_start: int,
                    rx_clock_offset_samples: np.ndarray,
                    pr_mode: int, pr_levels: np.ndarray,
                    mu_alpha: float, pr_nt: int,
                    cal_mode: int, mu_cal_off: float, mu_cal_gain: float,
                    mu_cal_skew: float, cal: np.ndarray,
                    corr_queue: np.ndarray, fs: np.ndarray, ist: np.ndarray,
                    dec: np.ndarray, xl: np.ndarray, y_sl: np.ndarray,
                    r_sl: np.ndarray, phase: np.ndarray, lane_of: np.ndarray,
                    q_hist: np.ndarray):
    """Symbols ``k0 <= k < k1`` of the loop :func:`_adc_rx_py` describes.

    Everything carried from one symbol to the next is in the arguments: the
    FFE / DFE taps ``wf`` / ``wd`` and the loop-latency queue ``corr_queue``
    (updated in place), ``fs`` = [position, PD accumulator, integrator,
    applied correction, a, b], ``ist`` = [PD block count, queue index], and
    the output arrays the FFE, DFE and detector read back from. Any split of
    [0, n) into consecutive calls is the same arithmetic in the same order
    as one call, bit for bit.

    ``y`` is samples ``[y_off, y_off + y.size)`` of an ``n_total``-sample
    waveform, with the status codes of the mixed-signal core
    (:func:`~halo_serdes.cdr.kernels._ms_rx_core_py`): 0 done, 1 out of
    waveform, 2 needs samples past the window's end, 3 needs dropped ones.
    """
    nl = levels.size
    nf = wf.size
    nd = wd.size
    nt = pr_nt if pr_mode > 0 else 1    # cursors the target accounts for
    a_max = 2.0 if pr_nt == 3 else 1.0
    p_sym = 0.0
    for m in range(nl):
        p_sym += levels[m] * levels[m]
    p_sym = p_sym / nl
    pos = fs[0]
    acc = fs[1]
    integ = fs[2]
    corr_now = fs[3]
    a_pr = fs[4]
    b_pr = fs[5]
    n_acc = ist[0]
    qi = ist[1]
    lat = loop_latency_blocks
    w_end = y_off + y.size

    for k in range(k0, k1):
        lane = k % n_lanes
        p_clk = pos + rx_clock_offset_samples[k]
        # cal[4]: the skew calibration's delay trim for the lane [samples]
        p = p_clk + skews[lane] - cal[4, lane]
        st = 0
        if p + osr + 2 >= n_total or p < 1:
            st = 1
        elif w_end < n_total and int(np.floor(p)) + 2 >= w_end:
            st = 2
        elif y_off > 0 and int(np.floor(p)) - 1 < y_off:
            st = 3
        if st != 0:
            fs[0] = pos
            fs[1] = acc
            fs[2] = integ
            fs[3] = corr_now
            fs[4] = a_pr
            fs[5] = b_pr
            ist[0] = n_acc
            ist[1] = qi
            return k, st
        x = _farrow_local(y, p - y_off) * gains[lane] + offsets[lane] + noise[k]
        # mid-rise quantizer
        code = np.floor(x / q_step)
        if code > code_max:
            code = code_max
        elif code < -code_max - 1:
            code = -code_max - 1
        q = (code + 0.5) * q_step
        if cal_mode == 1:
            # background calibration, digital, after the quantizer: the lane's
            # word is corrected with the estimates as they stand, then they
            # learn from it. cal[0] offset (the lane's mean: the data are
            # zero-mean), cal[1] the lane's power about it, cal[2] the gain
            # that brings it to the lanes' mean power, cal[3] the lane's
            # conversions so far. The powers start at zero and rise together,
            # so their ratios are right from the start, but they are a few
            # samples' worth: the gain holds at 1 until every lane has had
            # 1 / mu_cal_gain conversions (one time constant).
            d = q - cal[0, lane]
            q = d * cal[2, lane]
            cal[0, lane] += mu_cal_off * d
            cal[1, lane] += mu_cal_gain * (d * d - cal[1, lane])
            cal[3, lane] += 1.0
            if mu_cal_gain > 0.0 and cal[3, lane] * mu_cal_gain >= 1.0:
                p_mean = 0.0
                for m in range(n_lanes):
                    p_mean += cal[1, m]
                p_mean = p_mean / n_lanes
                if cal[1, lane] > 0.0:
                    cal[2, lane] = np.sqrt(p_mean / cal[1, lane])
        q_hist[k] = q
        if cal_mode == 1 and mu_cal_skew > 0.0 and k >= 2:
            # skew: a Mueller-Muller detector per lane on the (corrected)
            # ADC words around the lane's sample k - 1 -- positive when that
            # lane samples late. The trims are kept zero-mean across lanes:
            # their common part is the CDR's to move, and two integrators on
            # one phase would wander against each other.
            lk = (k - 1) % n_lanes
            a0 = 1.0 if q_hist[k - 2] > 0 else -1.0
            a2 = 1.0 if q > 0 else -1.0
            step = mu_cal_skew * q_hist[k - 1] * (a2 - a0) / levels[nl - 1]
            for m in range(n_lanes):
                cal[4, m] -= step / n_lanes
            cal[4, lk] += step
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
            prev2 = 0.0
            if nt == 3 and s >= 2:
                prev2 = levels[xl[s - 2]]
                ctl = ctl + b_pr * prev2
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
                        elif a_pr > a_max:
                            a_pr = a_max
                        if nt == 3:
                            b_pr += mu_alpha * e * prev2 / p_sym
                            if b_pr < -1.0:
                                b_pr = -1.0
                            elif b_pr > 1.0:
                                b_pr = 1.0
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

    fs[0] = pos
    fs[1] = acc
    fs[2] = integ
    fs[3] = corr_now
    fs[4] = a_pr
    fs[5] = b_pr
    ist[0] = n_acc
    ist[1] = qi
    return k1, 0


class AdcRxRun:
    """One ADC receiver run, advanced in as many calls as wanted.

    ``advance(k1)`` runs the loop up to symbol ``k1`` (or until the waveform
    runs out); ``result()`` gives what :func:`adc_rx` returns and fills
    ``alpha_out``. One ``advance`` to ``n_symbols`` is :func:`adc_rx`; any
    sequence of smaller ones gives the same arrays bit for bit.
    """

    def __init__(self, y, osr, pos0, n_symbols, levels, n_lanes, offsets, gains,
                 skews, q_step, code_max, noise, w_ffe, n_pre_ffe, mu_ffe,
                 w_dfe, mu_dfe, kp, ki, clamp, pd_offset, pd_use_ffe,
                 loop_latency_blocks, ref_idx, train_len, adapt_start,
                 rx_clock_offset_samples, pr_alpha, pr_mode, pr_levels,
                 mu_alpha, alpha_out, pr_beta, pr_nt, cal_mode=0, mu_cal_off=0.0,
                 mu_cal_gain=0.0, mu_cal_skew=0.0, core=None):
        self.core = core if core is not None else _adc_rx_core
        self.osr = osr
        self.set_window(y)
        self.wf = w_ffe.copy()
        self.wd = w_dfe.copy()
        self.params = (levels, n_lanes, offsets, gains, skews, q_step, code_max,
                       noise, self.wf, n_pre_ffe, mu_ffe, self.wd, mu_dfe, kp, ki,
                       clamp, pd_offset, pd_use_ffe, loop_latency_blocks, ref_idx,
                       train_len, adapt_start, rx_clock_offset_samples, pr_mode,
                       pr_levels, mu_alpha, pr_nt, int(cal_mode), float(mu_cal_off),
                       float(mu_cal_gain), float(mu_cal_skew), self._cal_init(n_lanes),
                       np.zeros(loop_latency_blocks + 1, dtype=np.float64))
        self.fs = np.array([pos0, 0.0, 0.0, 0.0, pr_alpha, pr_beta], dtype=np.float64)
        self.ist = np.zeros(2, dtype=np.int64)
        self.alpha_out = alpha_out
        self.n_symbols = n_symbols
        self.out = (np.zeros(n_symbols, dtype=np.int64),     # dec
                    # line-symbol estimates the DFE and the PR residual
                    # subtract; the same as dec except for precoded 1 + D,
                    # where dec's mod-N chain is right for the user symbol but,
                    # after one wrong composite, wrong for every line symbol
                    # that follows. This chain clips instead, and the
                    # composite's extremes resynchronise it.
                    np.zeros(n_symbols, dtype=np.int64),     # xl
                    np.zeros(n_symbols, dtype=np.float64),   # y_sl
                    np.zeros(n_symbols, dtype=np.float64),   # r_sl: less the controlled cursor
                    np.zeros(n_symbols, dtype=np.float64),   # phase
                    np.zeros(n_symbols, dtype=np.int64),     # lane_of
                    np.zeros(n_symbols, dtype=np.float64))   # q_hist: dequantized samples
        self.done = 0
        self.stopped = False
        self.need_more = False

    set_window = MsRxRun.set_window

    @staticmethod
    def _cal_init(n_lanes: int) -> np.ndarray:
        """Calibration state [offset, power, gain, conversions, delay trim]
        x lane: no offset, no power yet, unit gain, no trim."""
        cal = np.zeros((5, n_lanes), dtype=np.float64)
        cal[2] = 1.0
        return cal

    @property
    def cal(self) -> np.ndarray:
        """The calibration state as the run left it ([offset, power, gain, conversions,
        delay trim] x lane)."""
        return self.params[31]

    def advance(self, k1: int) -> int:
        k1 = min(int(k1), self.n_symbols)
        self.need_more = False
        if self.stopped or k1 <= self.done:
            return self.done
        done, st = self.core(self.y, self.y_off, self.n_total, self.osr, self.done, k1,
                             *self.params, self.fs, self.ist, *self.out)
        self.done = int(done)
        check_window_status(st)
        self.stopped = st == 1
        self.need_more = st == 2
        return self.done

    def next_position(self) -> float:
        k = min(self.done, self.n_symbols - 1)
        return float(self.fs[0] + self.params[22][k])

    @property
    def finished(self) -> bool:
        return self.stopped or self.done >= self.n_symbols

    def result(self):
        n = self.done
        dec, _xl, y_sl, _r_sl, phase, lane_of, q_hist = self.out
        self.alpha_out[0] = self.fs[4]
        if self.alpha_out.size > 1:
            self.alpha_out[1] = self.fs[5]
        return (dec[:n], y_sl[:n], phase[:n], self.wf, self.wd,
                lane_of[:n], q_hist[:n])


def _adc_rx_with(core):
    def adc_rx(y: np.ndarray, osr: int, pos0: float, n_symbols: int,
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
        run = AdcRxRun(y, osr, pos0, n_symbols, levels, n_lanes, offsets, gains,
                       skews, q_step, code_max, noise, w_ffe, n_pre_ffe, mu_ffe,
                       w_dfe, mu_dfe, kp, ki, clamp, pd_offset, pd_use_ffe,
                       loop_latency_blocks, ref_idx, train_len, adapt_start,
                       rx_clock_offset_samples, pr_alpha, pr_mode, pr_levels,
                       mu_alpha, alpha_out, pr_beta, pr_nt, core=core)
        run.advance(n_symbols)
        return run.result()
    return adc_rx


_ADC_RX_DOC = """Returns (dec, y_slicer, phase, w_ffe_out, w_dfe_out, lane_of, q_codes).

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
    ``alpha_out[0]`` returns the a the run ended on (and ``alpha_out[1]``,
    when there is one, the b of a three-cursor target, adapted the same way
    against the decision two symbols back; a in [0, 2], b in [-1, 1] there).

    ``pr_nt`` 3 is a 1 + aD + bD^2 target: ``pr_beta`` times the decision two
    symbols back is subtracted as well, everywhere ``pr_alpha`` times the
    previous one is, and the DFE starts after both controlled cursors.

    The loop itself is :func:`_adc_rx_core_py`; :class:`AdcRxRun` runs it in
    chunks with the same result.
    """


_adc_rx_core = _adc_rx_core_py
#: the pure-Python loop, whatever the JIT does (tests compare the two)
_adc_rx_py = _adc_rx_with(_adc_rx_core_py)
_adc_rx_py.__doc__ = _ADC_RX_DOC
adc_rx = _adc_rx_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _farrow_local = numba.njit(cache=True, inline="always")(_farrow_py)
        _adc_rx_core = numba.njit(cache=True)(_adc_rx_core_py)
        adc_rx = _adc_rx_with(_adc_rx_core)
        adc_rx.__doc__ = _ADC_RX_DOC
    except ImportError:
        pass
