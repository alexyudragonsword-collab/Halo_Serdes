"""Bit-true ADC receiver loop: FFE + DFE + slicer + Mueller-Muller CDR in int64.

``fixed_datapath`` replays FFE + DFE + slicer on ADC codes captured from a
float run, so its CDR stayed float and the codes it saw were sampled where
the *float* loop put the sampler. Here the whole digital back end is integer
and closes the loop: the phase register picks the phase-interpolator code,
the sampler reads the waveform there, the ADC quantises, and the integer
datapath and phase detector turn that code into the next phase. What a real
receiver's RTL implements is exactly the part after the ADC code, so:

* ``run_fixed_loop`` runs the closed loop on a waveform (the analog front end
  -- interpolated sampling, lane mismatch, ADC noise and quantiser -- is the
  float kernel's, value for value);
* ``replay_digital`` is the digital part alone, from the recorded code
  stream; it reproduces the closed loop's decisions, slicer values and PI
  codes bit for bit (the codes carry everything the analog side did), and it
  is what ``rtl/adc_dsp_loop.sv`` reimplements for the lockstep.

Semantics, all int64:

* input word ``2 c + 1`` for ADC code ``c``: the mid-rise quantiser's level
  (c + 1/2) q_step in units of q_step / 2, so the half-LSB the float kernel
  carries is not dropped (``fixed_datapath`` feeds ``c`` and does drop it);
* FFE: integer MAC, rounding add + arithmetic right shift, saturation to
  ``out_bits``; DFE: integer weights times the slicer levels in output LSBs,
  same rounding; slicer: nearest level, first minimum on ties;
* MM PD on symbol s - 1: ``x1 (sign x2 - sign x0)`` on the FFE outputs or on
  the input words (``pd_ffe``), summed over one block of ``n_lanes`` symbols;
* loop filter: ``pd = -acc - pd_off``, ``integ += pd <<< ki_sh``,
  ``c = (pd <<< kp_sh) + integ`` (a negative shift is an arithmetic right
  shift), clamp, ``lat`` blocks of latency, and the phase register advances
  by ``c >>> lane_shift`` every symbol -- the float loop's ``c / n_lanes``;
* the phase register counts 2^-ph_frac UI; the PI code is its top bits,
  ``ph >>> (ph_frac - pi_bits)``, and the sampler fires at
  ``pos0 + k osr + code * osr / 2^pi_bits`` (plus the Rx clock and lane skew).

The gains come from ``cdr.kp_shift`` / ``ki_shift`` plus the shift that
converts the PD's LSB into UI (``kp_tot = kp_shift + round(log2(L / x_lsb))``),
so the loop is the float loop's within a factor of sqrt 2;
``float_equivalent_gains`` gives the exact float gains it implements, which
is how the wide-word convergence test compares the two.

Out of scope here: adaptation (weights are the float run's, frozen),
partial-response targets, and the sequence detector (ROADMAP P1 #1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from ..cdr.kernels import _farrow_py
from ..core.fixed import to_int
from .fixed_datapath import calculate_ffe_shift

#: slicer word width (the replayed datapath uses the same)
OUT_BITS = 12


def _ashift(v, sh):
    return v << sh if sh >= 0 else v >> (-sh)


def _rnd_shift(v, sh, do_round):
    """v * 2^sh: a left shift, or an arithmetic right shift with the
    rounding adder when ``do_round``."""
    if sh >= 0:
        return v << sh
    if do_round == 1:
        v += 1 << (-sh - 1)
    return v >> (-sh)


def _digital_step_py(k, xi, n_pre, wf, ffe_shift, wd, dfe_shift, levels_out,
                     out_bits, do_round, pd_ffe, kp_sh, ki_sh, clamp_int, pd_off,
                     n_lanes, lane_shift, lat, lp, ref, wacc_f, wacc_d,
                     pr_lv, prs, xl, r_out, xh, dec, v_out, queue, st):
    """One symbol of the digital back end. ``st`` = [phase register,
    integrator, applied correction, PD accumulator, PD count, queue index];
    ``lp`` = [train_len, adapt_start, FFE LMS on, FFE step shift, DFE LMS
    on, DFE step shift, guard bits, FFE weight min, max, DFE weight min, max,
    PR mode, PR cursors, PR fraction bits, PR LMS on, PR step shift, a max,
    b min, b max]; ``wacc_f`` / ``wacc_d`` the weight accumulators (weights
    carry their top bits, ``wf`` / ``wd`` the weights the datapath uses).

    Partial response, as the float kernel's ``pr_mode``: 1 subtracts
    ``ctl = (a L[x[s-1]] + b L[x[s-2]]) >>> fl`` before slicing (``prs`` holds
    a and b with the guard bits, adapted with the FFE when PR LMS is on);
    2 slices the composite levels ``pr_lv`` and takes the line symbol as
    ``(q - d[s-1]) mod N`` (precoded 1 + D). ``xl`` is the line-symbol
    estimate the DFE and the controlled cursor use, ``r_out`` the slicer
    value less the controlled cursor (the phase detector's input under PR).
    Mode 0 is the delta target, unchanged."""
    xh[k] = xi
    lim_hi = (1 << (out_bits - 1)) - 1
    lim_lo = -(1 << (out_bits - 1))
    s = k - n_pre
    if s >= 0:
        acc = 0
        for i in range(wf.size):
            j = k - i
            if j >= 0:
                acc += wf[i] * xh[j]
        if do_round == 1 and ffe_shift > 0:
            acc += 1 << (ffe_shift - 1)
        acc = acc >> ffe_shift
        if acc > lim_hi:
            acc = lim_hi
        elif acc < lim_lo:
            acc = lim_lo
        pr_mode = lp[11]
        nt = lp[12] if pr_mode > 0 else 1
        g = lp[6]
        fb = 0
        for d in range(wd.size):
            jj = s - nt - d
            if jj >= 0:
                fb += wd[d] * levels_out[xl[jj]]
        if do_round == 1 and dfe_shift > 0:
            fb += 1 << (dfe_shift - 1)
        fb = fb >> dfe_shift
        v = acc - fb
        if v > lim_hi:
            v = lim_hi
        elif v < lim_lo:
            v = lim_lo
        v_out[s] = v
        nl = levels_out.size
        prev = 0
        prev2 = 0
        ctl = 0
        if pr_mode > 0:
            if s >= 1:
                prev = levels_out[xl[s - 1]]
            if nt == 3 and s >= 2:
                prev2 = levels_out[xl[s - 2]]
            ctl = _rnd_shift((prs[0] >> g) * prev + (prs[1] >> g) * prev2, -lp[13], do_round)
        r_out[s] = v - ctl
        q = 0
        if pr_mode == 2:
            bd = abs(v - pr_lv[0])
            for m in range(1, pr_lv.size):
                dd = abs(v - pr_lv[m])
                if dd < bd:
                    bd = dd
                    q = m
            best = (q - (dec[s - 1] if s >= 1 else 0)) % nl
        else:
            u = v - ctl
            best = 0
            bd = abs(u - levels_out[0])
            for m in range(1, nl):
                dd = abs(u - levels_out[m])
                if dd < bd:
                    bd = dd
                    best = m
        # data-aided during training, as the float kernel
        training = s < lp[0] and ref[s] >= 0
        if training:
            dec[s] = ref[s]
            xl[s] = ref[s]
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

        # LMS on the error against the decided level, the float kernel's
        # update with the step a power of two (lp[3], lp[5]) on accumulators
        # lp[6] bits finer than the weights
        if s >= lp[1] and (lp[2] == 1 or lp[4] == 1):
            if pr_mode == 0:
                e = v - levels_out[dec[s]]
            elif pr_mode == 2 and not training:
                e = v - pr_lv[q]
            else:
                e = v - (levels_out[xl[s]] + ctl)
                if lp[14] == 1 and pr_mode == 1:
                    a = prs[0] + _rnd_shift(e * prev, lp[15], do_round)
                    if a < 0:
                        a = 0
                    elif a > (lp[16] << g) + (1 << g) - 1:
                        a = (lp[16] << g) + (1 << g) - 1
                    prs[0] = a
                    if nt == 3:
                        b = prs[1] + _rnd_shift(e * prev2, lp[15], do_round)
                        if b < (lp[17] << g):
                            b = lp[17] << g
                        elif b > (lp[18] << g) + (1 << g) - 1:
                            b = (lp[18] << g) + (1 << g) - 1
                        prs[1] = b
            if lp[2] == 1:
                for i in range(wf.size):
                    j = k - i
                    if j >= 0:
                        a = wacc_f[i] - _rnd_shift(e * xh[j], lp[3], do_round)
                        if a > (lp[8] << g) + (1 << g) - 1:
                            a = (lp[8] << g) + (1 << g) - 1
                        elif a < (lp[7] << g):
                            a = lp[7] << g
                        wacc_f[i] = a
                        wf[i] = a >> g
            if lp[4] == 1:
                for d in range(wd.size):
                    jj = s - nt - d
                    if jj >= 0:
                        a = wacc_d[d] + _rnd_shift(e * levels_out[xl[jj]], lp[5], do_round)
                        if a > (lp[10] << g) + (1 << g) - 1:
                            a = (lp[10] << g) + (1 << g) - 1
                        elif a < (lp[9] << g):
                            a = lp[9] << g
                        wacc_d[d] = a
                        wd[d] = a >> g

        if s >= 2:
            if pd_ffe == 1 and pr_mode > 0:
                x0 = r_out[s - 2]
                x1 = r_out[s - 1]
                x2 = r_out[s]
            elif pd_ffe == 1:
                x0 = v_out[s - 2]
                x1 = v_out[s - 1]
                x2 = v
            else:
                x0 = xh[s - 2]
                x1 = xh[s - 1]
                x2 = xh[s]
            a0 = 1 if x0 > 0 else -1
            a2 = 1 if x2 > 0 else -1
            st[3] += x1 * (a2 - a0)
            st[4] += 1
            if st[4] >= n_lanes:
                pd = -st[3] - pd_off
                st[1] += _ashift(pd, ki_sh)
                c = _ashift(pd, kp_sh) + st[1]
                if clamp_int > 0:
                    if c > clamp_int:
                        c = clamp_int
                    elif c < -clamp_int:
                        c = -clamp_int
                queue[st[5]] = c
                st[5] = (st[5] + 1) % (lat + 1)
                st[2] = queue[st[5]]
                st[3] = 0
                st[4] = 0
    st[0] += st[2] >> lane_shift


def _rnd_py(v, sh):
    """``v / 2^sh`` rounded half up (an arithmetic shift for sh = 0)."""
    if sh == 0:
        return v
    return (v + (1 << (sh - 1))) >> sh


def _cal_word_py(lane, xi, cp, co, cg, cpm):
    """Background calibration of one ADC word (``adc.cal``, offset and gain),
    between the ADC and the FFE. ``cp`` = [on, fraction bits F, gain fraction
    bits B, offset step shift, gain step shift, mean-power step shift, word
    limit] (a negative step shift turns that estimate off); ``co`` / ``cg`` the lanes' offset (word LSBs, F fraction bits) and
    gain (B fraction bits) registers, ``cpm[0]`` the mean corrected power
    (word LSBs squared, F fraction bits), ``cpm[1]`` the gain registers' sum;
    ``cp[9]`` log2 of the lane count.

    The float kernel's offset estimate, in integers: the lane's word less
    its offset, ``d``, is corrected with the registers as they stand and
    then moves the offset by ``d >>> sh_o``. The gain is the hardware form of
    the float one's: no square root or division -- an LMS that drives the
    lane's corrected power to the lanes' running mean, ``g += (pm - y^2)
    >>> sh_g``. Both settle where every lane's power is the mean."""
    if cp[0] == 0:
        return xi
    f = cp[1]
    d = (xi << f) - co[lane]
    # the gain applied: the register less the lanes' mean, plus one. The LMS
    # only sees the lanes' powers against their own mean, so the gains'
    # common part is unobservable -- left free it random-walks on the update
    # rounding (to 0.7 in a 10-bit gain word, 2e5 symbols); pinned it is 1
    # exactly, as the float loop's mean-power ratios keep it
    g = cg[lane] - (cpm[1] >> cp[9]) + (1 << cp[2])
    y = (d * g) >> cp[2]                        # corrected word, F fraction bits
    yw = (y + (1 << (f - 1))) >> f              # rounded to word LSBs
    if yw > cp[6]:
        yw = cp[6]
    elif yw < -cp[6]:
        yw = -cp[6]
    # rounded shifts: a floor shift moves a register down by half an LSB per
    # update on average (and by a whole one for any negative input when the
    # shift is wide), which over 10^5 updates is a drift; a negative shift
    # is "this estimate is off"
    if cp[3] >= 0:
        co[lane] += _rnd(d, cp[3])
    p = yw * yw                                 # word LSBs squared
    if cp[4] >= 0:
        dg = _rnd((cpm[0] >> f) - p, cp[4])
        cg[lane] += dg
        cpm[1] += dg
    if cp[5] >= 0:
        cpm[0] += _rnd((p << f) - cpm[0], cp[5])
    return yw


def _trim_py(lane, cts, lane_shift):
    """The lane's delay trim as applied: its register less the lanes' mean
    (the common phase is the CDR's; the mean is a shift, the lanes a power of
    two, so the applied trims are zero-mean exactly)."""
    return cts[lane] - (cts[cts.size - 1] >> lane_shift)


def _skew_step_py(k, xh, n_lanes, cp, cts):
    """The float kernel's per-lane Mueller-Muller skew detector, in integers:
    on the corrected words around lane (k - 1)'s sample, ``x[k-1] (sign x[k]
    - sign x[k-2])``, scaled by the shift ``cp[8]`` into phase-register units
    (left for a non-negative shift, rounded right otherwise). ``cts`` holds
    one trim per lane and their sum last."""
    if cp[7] == 0 or k < 2:
        return
    a0 = 1 if xh[k - 2] > 0 else -1
    a2 = 1 if xh[k] > 0 else -1
    v = xh[k - 1] * (a2 - a0)
    sh = cp[8]
    if sh >= 0:
        step = v << sh
    else:
        step = (v + (1 << (-sh - 1))) >> (-sh)
    cts[(k - 1) % n_lanes] += step
    cts[cts.size - 1] += step


def _fixed_loop_closed_py(y, osr, pos0, n_sym, pi_scale, pi_sh, offsets,
                          gains, skews, q_step, code_max, noise, rx_clk,
                          n_pre, wf, ffe_shift, wd, dfe_shift, levels_out, out_bits,
                          do_round, pd_ffe, kp_sh, ki_sh, clamp_int, pd_off, n_lanes,
                          lane_shift, lat, lp, ref, wacc_f, wacc_d, pr_lv, prs, xl,
                          r_out, xh, pi_out, phase_out, dec, v_out, queue, st,
                          cp, co, cg, cpm, cts, xraw):
    """The closed loop on waveform ``y``. Returns the symbols run (fewer than
    ``n_sym`` when the sampler leaves the waveform, as the float kernel).
    ``xraw`` records the ADC words before calibration (the replay's input)."""
    for k in range(n_sym):
        lane = k % n_lanes
        code_pi = (st[0] - _trim(lane, cts, lane_shift)) >> pi_sh
        p_clk = pos0 + k * osr + code_pi * pi_scale + rx_clk[k]
        p = p_clk + skews[lane]
        if p + osr + 2 >= y.size or p < 1:
            return k
        x = _farrow_fx(y, p) * gains[lane] + offsets[lane] + noise[k]
        code = np.floor(x / q_step)
        if code > code_max:
            code = code_max
        elif code < -code_max - 1:
            code = -code_max - 1
        pi_out[k] = code_pi
        phase_out[k] = p_clk
        xraw[k] = 2 * int(code) + 1
        _digital_step(k, _cal_word(lane, xraw[k], cp, co, cg, cpm), n_pre, wf, ffe_shift,
                      wd, dfe_shift,
                      levels_out, out_bits, do_round, pd_ffe, kp_sh, ki_sh,
                      clamp_int, pd_off, n_lanes, lane_shift, lat, lp, ref, wacc_f,
                      wacc_d, pr_lv, prs, xl, r_out, xh, dec, v_out, queue, st)
        _skew_step(k, xh, n_lanes, cp, cts)
    return n_sym


def _fixed_loop_digital_py(xin, pi_sh, n_pre, wf, ffe_shift, wd, dfe_shift,
                           levels_out, out_bits, do_round, pd_ffe, kp_sh, ki_sh,
                           clamp_int, pd_off, n_lanes, lane_shift, lat, lp, ref,
                           wacc_f, wacc_d, pr_lv, prs, xl, r_out, xh, pi_out, dec, v_out,
                           queue, st, cp, co, cg, cpm, cts):
    """The digital part alone, from a recorded (raw, uncalibrated) word stream."""
    for k in range(xin.size):
        pi_out[k] = (st[0] - _trim(k % n_lanes, cts, lane_shift)) >> pi_sh
        _digital_step(k, _cal_word(k % n_lanes, xin[k], cp, co, cg, cpm), n_pre, wf,
                      ffe_shift, wd, dfe_shift, levels_out,
                      out_bits, do_round, pd_ffe, kp_sh, ki_sh, clamp_int, pd_off,
                      n_lanes, lane_shift, lat, lp, ref, wacc_f, wacc_d, pr_lv, prs, xl,
                      r_out, xh, dec, v_out, queue, st)
        _skew_step(k, xh, n_lanes, cp, cts)
    return xin.size


# numba resolves these globals when it compiles, so rebinding them to the
# jitted versions first is enough (the same pattern as cdr.kernels._farrow)
_ashift_py = _ashift
_rnd_shift_py = _rnd_shift
_digital_step = _digital_step_py
_cal_word = _cal_word_py
_rnd = _rnd_py
_trim = _trim_py
_skew_step = _skew_step_py
_farrow_fx = _farrow_py
_fixed_loop_closed = _fixed_loop_closed_py
_fixed_loop_digital = _fixed_loop_digital_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _ashift = numba.njit(cache=True, inline="always")(_ashift_py)
        _rnd_shift = numba.njit(cache=True, inline="always")(_rnd_shift_py)
        _farrow_fx = numba.njit(cache=True, inline="always")(_farrow_py)
        _digital_step = numba.njit(cache=True)(_digital_step_py)
        _rnd = numba.njit(cache=True, inline="always")(_rnd_py)
        _cal_word = numba.njit(cache=True, inline="always")(_cal_word_py)
        _trim = numba.njit(cache=True, inline="always")(_trim_py)
        _skew_step = numba.njit(cache=True, inline="always")(_skew_step_py)
        _fixed_loop_closed = numba.njit(cache=True)(_fixed_loop_closed_py)
        _fixed_loop_digital = numba.njit(cache=True)(_fixed_loop_digital_py)
    except ImportError:
        pass


@dataclass(frozen=True)
class FixedLoop:
    """Everything the integer back end needs, already in integers."""

    wf: np.ndarray            # FFE weights (ffe_weight Q format)
    wd: np.ndarray            # DFE weights (dfe_weight Q format)
    ffe_shift: int
    dfe_shift: int
    levels_out: np.ndarray    # slicer levels in output LSBs
    out_bits: int
    do_round: int
    n_pre: int
    pd_ffe: int
    kp_sh: int                # PD -> phase-register shift (negative: right)
    ki_sh: int
    clamp_int: int            # 0: none
    pd_off: int
    n_lanes: int
    lane_shift: int
    lat: int
    pi_bits: int
    ph_frac: int
    in_lsb: float             # volts per input-word LSB (q_step / 2)
    out_lsb: float            # volts per slicer LSB
    pd_lsb: float             # volts per PD-input LSB
    lp: np.ndarray            # LMS / training / PR parameters (see _digital_step_py)
    pr_lv: np.ndarray         # composite slicer levels (PR mode 2) [slicer LSBs]
    pr_ab: np.ndarray         # initial a, b [2^-pr_fl]
    # background calibration parameters (see _cal_word_py; off by default)
    cal: np.ndarray = field(default_factory=lambda: np.array(
        [0, 16, 14, 0, 0, 0, (1 << 20) - 1, 0, 0, 0], dtype=np.int64))
    cal_pm0: int = 0          # its mean-power register's start [word LSBs^2, F fraction bits]

    @property
    def pi_sh(self) -> int:
        return self.ph_frac - self.pi_bits

    def digital_args(self):
        return (self.n_pre, self.wf, self.ffe_shift, self.wd, self.dfe_shift,
                self.levels_out, self.out_bits, self.do_round, self.pd_ffe,
                self.kp_sh, self.ki_sh, self.clamp_int, self.pd_off, self.n_lanes,
                self.lane_shift, self.lat, self.lp)


def _lms_shift(mu: float, scale: float) -> int:
    """The power-of-two step nearest ``mu`` once the update is in integer
    units (``scale`` = volts^2 per product LSB times the accumulator LSBs per
    unit weight)."""
    return int(np.round(np.log2(mu * scale)))


def build_fixed_loop(cfg, w_ffe: np.ndarray, w_dfe: np.ndarray, levels: np.ndarray,
                     q_step: float, out_bits: int = OUT_BITS, train_len: int = 0,
                     adapt_start: int = 0, pr_mode: int = 0, pr_nt: int = 1,
                     alpha: float = 0.0, beta: float = 0.0,
                     pr_levels: np.ndarray | None = None, mu_alpha: float = 0.0,
                     adc_power: float | None = None) -> FixedLoop:
    """Quantise a float receiver (weights, levels, CDR, LMS) for the loop.

    ``w_ffe`` / ``w_dfe`` are the weights it starts from: the float run's
    initial ones when the equaliser adapts (the loop then trains and adapts
    itself, data-aided over ``train_len`` symbols, from ``adapt_start``), its
    final ones when it does not. ``pr_*`` / ``alpha`` / ``beta`` /
    ``mu_alpha`` are the float kernel's partial-response arguments; a and b
    are words with ``dfe_weight.fl`` fraction bits.

    ``adc_power``: the mean square of the ADC's output [V^2] (the float run's),
    with ``adc.cal.mode = "background"``: it starts the mean-power register
    and sets the gain step (an LMS in power needs the power's scale)."""
    num = cfg.numeric
    acfg = cfg.rx.adc
    ccfg = cfg.rx.cdr
    n_lanes = int(acfg.n_lanes)
    lane_shift = int(np.log2(n_lanes))
    if (1 << lane_shift) != n_lanes:
        raise ValueError(f"numeric.mode='fixed' needs a power-of-two rx.adc.n_lanes "
                         f"(the loop divides the correction by a shift), got {n_lanes}")
    wq = num.ffe_weight
    in_lsb = q_step / 2.0
    ffe_shift = calculate_ffe_shift(np.asarray(w_ffe), wq, acfg.n_bits + 1, out_bits)
    out_lsb = in_lsb * 2.0 ** -wq.fl * (1 << ffe_shift)
    pd_ffe = 1 if cfg.mm_pd_input == "ffe" else 0
    pd_lsb = out_lsb if pd_ffe else in_lsb
    conv = int(np.round(np.log2(n_lanes / pd_lsb)))     # PD LSBs -> UI
    ph_frac = num.pi_bits + num.phase_frac_bits
    clamp_int = int(np.round(ccfg.clamp * 2.0 ** ph_frac)) if ccfg.clamp else 0
    g = int(num.lms_guard_bits)
    fcfg, dcfg = cfg.rx.ffe, cfg.rx.dfe
    mu_f = fcfg.mu if fcfg.adapt != "none" else 0.0
    mu_d = dcfg.mu if dcfg.adapt != "none" else 0.0
    dq = num.dfe_weight
    sh_f = _lms_shift(mu_f, out_lsb * in_lsb * 2.0 ** (wq.fl + g)) if mu_f > 0 else 0
    sh_d = _lms_shift(mu_d, out_lsb * out_lsb * 2.0 ** (dq.fl + g)) if mu_d > 0 else 0
    lo_f, hi_f = -(1 << (wq.wl - 1)), (1 << (wq.wl - 1)) - 1
    lo_d, hi_d = -(1 << (dq.wl - 1)), (1 << (dq.wl - 1)) - 1
    pfl = int(dq.fl)
    lv_v = np.asarray(levels, dtype=float)
    p_sym = float(np.mean(lv_v ** 2))
    lms_a = int(mu_alpha > 0 and pr_mode == 1 and (mu_f > 0 or mu_d > 0))
    sh_a = _lms_shift(mu_alpha / p_sym, out_lsb * out_lsb * 2.0 ** (pfl + g)) if lms_a else 0
    a_max = (2 if pr_nt == 3 else 1) << pfl
    lp = np.array([train_len, adapt_start, int(mu_f > 0), sh_f, int(mu_d > 0), sh_d, g,
                   lo_f, hi_f, lo_d, hi_d, pr_mode, pr_nt if pr_mode > 0 else 1, pfl,
                   lms_a, sh_a, a_max, -(1 << pfl), 1 << pfl], dtype=np.int64)
    pr_lv = (np.round(np.asarray(pr_levels, dtype=float) / out_lsb).astype(np.int64)
             if pr_mode == 2 and pr_levels is not None else np.zeros(1, dtype=np.int64))
    pr_ab = np.array([int(np.round(alpha * 2.0 ** pfl)), int(np.round(beta * 2.0 ** pfl))],
                     dtype=np.int64) if pr_mode > 0 else np.zeros(2, dtype=np.int64)
    cal, cal_pm0 = _cal_params(cfg, in_lsb, lane_shift, adc_power, int(ph_frac),
                               float(np.max(np.abs(lv_v))))
    return FixedLoop(
        wf=to_int(w_ffe, wq), wd=to_int(w_dfe, num.dfe_weight),
        ffe_shift=int(ffe_shift), dfe_shift=int(num.dfe_weight.fl),
        levels_out=np.round(np.asarray(levels) / out_lsb).astype(np.int64),
        out_bits=int(out_bits), do_round=1 if wq.rounding == "round" else 0,
        n_pre=int(cfg.rx.ffe.n_pre), pd_ffe=pd_ffe,
        kp_sh=ph_frac - (ccfg.kp_shift + conv), ki_sh=ph_frac - (ccfg.ki_shift + conv),
        clamp_int=clamp_int,
        pd_off=int(np.round(ccfg.pd_offset * n_lanes / pd_lsb)),
        n_lanes=n_lanes, lane_shift=lane_shift,
        lat=max(0, ccfg.loop_latency_symbols // n_lanes),
        pi_bits=int(num.pi_bits), ph_frac=int(ph_frac),
        in_lsb=float(in_lsb), out_lsb=float(out_lsb), pd_lsb=float(pd_lsb), lp=lp,
        pr_lv=pr_lv, pr_ab=pr_ab, cal=cal, cal_pm0=cal_pm0)


def _cal_params(cfg, in_lsb: float, lane_shift: int, adc_power: float | None,
                ph_frac: int, l_outer: float):
    """``_cal_word_py``'s parameters: the float steps as power-of-two shifts.

    Offset: ``o += mu d``, so the shift is log2(1 / mu). Gain: the float loop
    moves a lane's gain by ~mu of its error per conversion; in the LMS form
    ``dg = (pm - y^2) >>> sh`` that is dg / g = mu (1 - y^2 / pm) / 2 with
    ``sh = log2(2 pm / (mu 2^B))``. The mean power averages every lane's
    conversions, so its step is mu / n_lanes. Skew (``cp[7]``, ``cp[8]``): the
    float trim's step as a shift into phase-register units (see below)."""
    acfg = cfg.rx.adc
    num = cfg.numeric
    f, b = int(num.cal_frac_bits), int(num.cal_gain_bits)
    word_max = (1 << int(acfg.n_bits)) - 1
    if acfg.cal.mode != "background":
        # off, or foreground: frozen at power-up, already in the ADC's lanes
        return np.array([0, f, b, 0, 0, 0, word_max, 0, 0, lane_shift], dtype=np.int64), 0
    if adc_power is None or adc_power <= 0:
        raise ValueError("the fixed calibration needs the ADC output power (adc_power)")
    pm = adc_power / in_lsb ** 2                     # word LSBs squared

    def sh(mu, scale=1.0):
        return max(int(np.round(np.log2(scale / mu))), 0) if mu > 0 else -1

    mu_o, mu_g = acfg.cal.mu_offset, acfg.cal.mu_gain
    # skew: the float trim moves mu x[k-1] (a2 - a0) / L_outer samples; in
    # phase-register units (2^-ph_frac UI) with x in words of in_lsb volts
    # that is the word product times mu in_lsb 2^ph_frac / (L_outer osr)
    mu_s = acfg.cal.mu_skew
    sh_s = (int(np.round(np.log2(mu_s * in_lsb * 2.0 ** ph_frac / (l_outer * cfg.osr))))
            if mu_s > 0 else 0)
    cp = np.array([1, f, b, sh(mu_o), sh(mu_g, 2.0 * pm / 2.0 ** b),
                   sh(mu_g) + lane_shift if mu_g > 0 else -1, word_max,
                   int(mu_s > 0), sh_s, lane_shift], dtype=np.int64)
    return cp, int(np.round(pm * 2.0 ** f))


def _cal_state(fl: FixedLoop):
    """Fresh calibration registers: no offset, unit gain, the expected power,
    no delay trim (one per lane, then their sum)."""
    n = fl.n_lanes
    return (np.zeros(n, dtype=np.int64),
            np.full(n, 1 << int(fl.cal[2]), dtype=np.int64),
            np.array([fl.cal_pm0, n << int(fl.cal[2])], dtype=np.int64),
            np.zeros(n + 1, dtype=np.int64))


def _applied_gains(fl: FixedLoop, cg, cpm):
    """The lane gains the calibration applies (registers less their mean, plus one)."""
    return cg - (int(cpm[1]) >> int(fl.cal[9])) + (1 << int(fl.cal[2]))


def float_equivalent_gains(fl: FixedLoop, osr: int) -> tuple[float, float, float, float]:
    """(kp, ki, clamp, pd_offset) the float ADC kernel needs to run the loop
    ``fl`` implements -- what it converges to as the words widen."""
    def gain(sh):          # c [UI] = pd_int 2^(sh - ph_frac); pd_f = pd_int pd_lsb / L
        return osr * 2.0 ** (sh - fl.ph_frac) * fl.n_lanes / fl.pd_lsb
    clamp = osr * fl.clamp_int * 2.0 ** -fl.ph_frac
    return gain(fl.kp_sh), gain(fl.ki_sh), clamp, fl.pd_off * fl.pd_lsb / fl.n_lanes


def float_equivalent_mu(fl: FixedLoop, ffe_fl: int, dfe_fl: int,
                        p_sym: float = 1.0) -> tuple[float, float, float]:
    """The float kernel's LMS steps (FFE, DFE, PR a) the loop's power-of-two
    ones implement; ``p_sym`` is the mean square of the slicer levels [V^2]."""
    g = int(fl.lp[6])
    mu_f = 2.0 ** fl.lp[3] / (fl.out_lsb * fl.in_lsb * 2.0 ** (ffe_fl + g)) if fl.lp[2] else 0.0
    mu_d = 2.0 ** fl.lp[5] / (fl.out_lsb ** 2 * 2.0 ** (dfe_fl + g)) if fl.lp[4] else 0.0
    mu_a = (2.0 ** fl.lp[15] * p_sym / (fl.out_lsb ** 2 * 2.0 ** (fl.lp[13] + g))
            if fl.lp[14] else 0.0)
    return mu_f, mu_d, mu_a


def _weights(fl: FixedLoop):
    """Working copies of the weights, their accumulators and the PR state
    (a, b with the guard bits; line-symbol estimates; slicer less cursor)."""
    g = int(fl.lp[6])
    return (fl.wf.copy(), fl.wd.copy(), fl.wf.astype(np.int64) << g,
            fl.wd.astype(np.int64) << g, fl.pr_ab.astype(np.int64) << g)


def _state(fl: FixedLoop, n: int):
    return (np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64),
            np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64),
            np.zeros(fl.lat + 1, dtype=np.int64), np.zeros(6, dtype=np.int64))


def run_fixed_loop(fl: FixedLoop, y: np.ndarray, osr: int, pos0: float, n_sym: int,
                   adc, noise: np.ndarray, rx_clk: np.ndarray,
                   ref: np.ndarray | None = None) -> dict:
    """The closed loop over waveform ``y``; ``adc`` is the run's ``TiAdc``.

    Returns the per-symbol records: ``xin`` (the ADC's words, before any
    calibration; ``x_cal`` after it), ``pi`` (PI code
    used for symbol k), ``phase`` (sample position, clock included),
    ``dec`` / ``v_out`` (indexed by symbol s = k - n_pre; ``n_dec`` valid),
    and the scalars ``replay_digital`` and the SV testbench need.
    """
    xh, pi, dec, v_out, queue, st = _state(fl, n_sym)
    xraw = np.zeros(n_sym, dtype=np.int64)
    co, cg, cpm, cts = _cal_state(fl)
    phase = np.zeros(n_sym)
    wf, wd, wacc_f, wacc_d, prs = _weights(fl)
    xl, r_out = np.zeros(n_sym, dtype=np.int64), np.zeros(n_sym, dtype=np.int64)
    ref = np.full(n_sym, -1, dtype=np.int64) if ref is None else np.asarray(ref, dtype=np.int64)
    done = _fixed_loop_closed(
        np.asarray(y, dtype=np.float64), osr, float(pos0), int(n_sym),
        float(osr) / (1 << fl.pi_bits), fl.pi_sh,
        np.asarray(adc.offsets, dtype=np.float64), np.asarray(adc.gains, dtype=np.float64),
        np.asarray(adc.skews, dtype=np.float64), float(adc.q_step), int(adc.code_max),
        np.asarray(noise, dtype=np.float64), np.asarray(rx_clk, dtype=np.float64),
        *_with_weights(fl, wf, wd), ref, wacc_f, wacc_d, fl.pr_lv, prs, xl, r_out,
        xh, pi, phase, dec, v_out, queue, st, fl.cal, co, cg, cpm, cts, xraw)
    done = int(done)
    n_dec = max(done - fl.n_pre, 0)
    return {"xin": xraw[:done], "x_cal": xh[:done], "pi": pi[:done], "phase": phase[:done],
            "cal": (co.copy(), _applied_gains(fl, cg, cpm), int(cpm[0]), cts[:-1].copy()),
            "dec": dec[:n_dec], "v_out": v_out[:n_dec], "n_dec": n_dec,
            "ref": ref[:done], "wf": wf, "wd": wd, "ab": prs >> int(fl.lp[6])}


def _with_weights(fl: FixedLoop, wf, wd):
    """``digital_args`` with the working weight copies in place of the
    initial ones (the loop adapts them)."""
    a = list(fl.digital_args())
    a[1], a[3] = wf, wd
    return a


def replay_digital(fl: FixedLoop, xin: np.ndarray, ref: np.ndarray | None = None) -> dict:
    """The digital back end alone on a recorded input-word stream: the golden
    model ``rtl/adc_dsp_loop.sv`` is held to."""
    xin = np.asarray(xin, dtype=np.int64)
    n = xin.size
    xh, pi, dec, v_out, queue, st = _state(fl, n)
    wf, wd, wacc_f, wacc_d, prs = _weights(fl)
    xl, r_out = np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64)
    ref = np.full(n, -1, dtype=np.int64) if ref is None else np.asarray(ref, dtype=np.int64)
    co, cg, cpm, cts = _cal_state(fl)
    _fixed_loop_digital(xin, fl.pi_sh, *_with_weights(fl, wf, wd), ref, wacc_f, wacc_d,
                        fl.pr_lv, prs, xl, r_out, xh, pi, dec, v_out, queue, st,
                        fl.cal, co, cg, cpm, cts)
    n_dec = max(n - fl.n_pre, 0)
    return {"pi": pi, "dec": dec[:n_dec], "v_out": v_out[:n_dec], "n_dec": n_dec,
            "cal": (co, _applied_gains(fl, cg, cpm), int(cpm[0]), cts[:-1]),
            "wf": wf, "wd": wd, "ab": prs >> int(fl.lp[6])}


def loop_artifacts(fl: FixedLoop, xin: np.ndarray, ref: np.ndarray) -> dict:
    """Vectors and parameters for ``dump_loop_vectors`` / the SV testbench:
    the golden is ``replay_digital`` on these words (the closed loop's own
    outputs on the same stretch, bit for bit), final weights included."""
    xin = np.asarray(xin, dtype=np.int64)
    ref = np.asarray(ref, dtype=np.int64)[: xin.size]
    rp = replay_digital(fl, xin, ref)
    lp = [int(v) for v in fl.lp]
    return {"xin": xin, "ref": ref, "pi": rp["pi"], "dec": rp["dec"], "v_out": rp["v_out"],
            "w_ffe_int": fl.wf, "w_dfe_int": fl.wd, "levels_out": fl.levels_out,
            "w_ffe_end": rp["wf"], "w_dfe_end": rp["wd"],
            "pr_lv": fl.pr_lv, "ab": fl.pr_ab, "ab_end": rp["ab"],
            "cal_end": np.concatenate([rp["cal"][0], rp["cal"][1], [rp["cal"][2]],
                                       rp["cal"][3]]),
            "params": {"N": int(xin.size), "N_DEC": int(rp["n_dec"]),
                       "NF": int(fl.wf.size), "ND": int(fl.wd.size),
                       "NL": int(fl.levels_out.size), "N_PRE": fl.n_pre,
                       "FFE_SHIFT": fl.ffe_shift, "DFE_SHIFT": fl.dfe_shift,
                       "OUT_BITS": fl.out_bits, "DO_ROUND": fl.do_round,
                       "PD_FFE": fl.pd_ffe, "KP_SH": fl.kp_sh, "KI_SH": fl.ki_sh,
                       "CLAMP": fl.clamp_int, "PD_OFF": fl.pd_off,
                       "LANES": fl.n_lanes, "LANE_SHIFT": fl.lane_shift,
                       "LAT": fl.lat, "PI_SH": fl.pi_sh,
                       "TRAIN_LEN": lp[0], "ADAPT_START": lp[1], "LMS_F": lp[2],
                       "SH_F": lp[3], "LMS_D": lp[4], "SH_D": lp[5], "G": lp[6],
                       "WF_LO": lp[7], "WF_HI": lp[8], "WD_LO": lp[9], "WD_HI": lp[10],
                       "PR_MODE": lp[11], "NT": lp[12], "PFL": lp[13], "LMS_A": lp[14],
                       "SH_A": lp[15], "A_MAX": lp[16], "B_LO": lp[17], "B_HI": lp[18],
                       "NPL": int(fl.pr_lv.size),
                       "CAL_ON": int(fl.cal[0]), "CAL_F": int(fl.cal[1]),
                       "CAL_B": int(fl.cal[2]), "CAL_SH_O": int(fl.cal[3]),
                       "CAL_SH_G": int(fl.cal[4]), "CAL_SH_P": int(fl.cal[5]),
                       "CAL_WMAX": int(fl.cal[6]), "CAL_PM0": int(fl.cal_pm0),
                       "CAL_SKEW": int(fl.cal[7]), "CAL_SH_S": int(fl.cal[8])}}


def dump_loop_vectors(path, art: dict, cw: int = 64):
    """Write the loop's vectors (one integer per line) and ``loop_dims.svh``."""
    from pathlib import Path

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    for name in ("xin", "ref", "pi", "dec", "v_out", "w_ffe_int", "w_dfe_int", "levels_out",
                 "w_ffe_end", "w_dfe_end", "pr_lv", "ab", "ab_end", "cal_end"):
        np.savetxt(path / f"loop_{name}.txt", np.atleast_1d(art[name]), fmt="%d")
    with open(path / "loop_dims.svh", "w", encoding="utf-8") as fh:
        fh.write("// generated by halo_serdes.dsp.fixed_loop.dump_loop_vectors\n")
        for k, v in {**art["params"], "CW": cw}.items():
            fh.write(f"localparam longint {k} = {int(v)};\n")
    return path
