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
from dataclasses import dataclass

import numpy as np

from ..cdr.kernels import _farrow_py
from ..core.fixed import to_int
from .fixed_datapath import calculate_ffe_shift

#: slicer word width (the replayed datapath uses the same)
OUT_BITS = 12


def _ashift(v, sh):
    return v << sh if sh >= 0 else v >> (-sh)


def _digital_step_py(k, xi, n_pre, wf, ffe_shift, wd, dfe_shift, levels_out,
                     out_bits, do_round, pd_ffe, kp_sh, ki_sh, clamp_int, pd_off,
                     n_lanes, lane_shift, lat, xh, dec, v_out, queue, st):
    """One symbol of the digital back end. ``st`` = [phase register,
    integrator, applied correction, PD accumulator, PD count, queue index]."""
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
        fb = 0
        for d in range(wd.size):
            jj = s - 1 - d
            if jj >= 0:
                fb += wd[d] * levels_out[dec[jj]]
        if do_round == 1 and dfe_shift > 0:
            fb += 1 << (dfe_shift - 1)
        fb = fb >> dfe_shift
        v = acc - fb
        if v > lim_hi:
            v = lim_hi
        elif v < lim_lo:
            v = lim_lo
        v_out[s] = v
        best = 0
        bd = abs(v - levels_out[0])
        for m in range(1, levels_out.size):
            dd = abs(v - levels_out[m])
            if dd < bd:
                bd = dd
                best = m
        dec[s] = best

        if s >= 2:
            if pd_ffe == 1:
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


def _fixed_loop_closed_py(y, osr, pos0, n_sym, pi_scale, pi_sh, offsets,
                          gains, skews, q_step, code_max, noise, rx_clk,
                          n_pre, wf, ffe_shift, wd, dfe_shift, levels_out, out_bits,
                          do_round, pd_ffe, kp_sh, ki_sh, clamp_int, pd_off, n_lanes,
                          lane_shift, lat, xh, pi_out, phase_out, dec, v_out, queue, st):
    """The closed loop on waveform ``y``. Returns the symbols run (fewer than
    ``n_sym`` when the sampler leaves the waveform, as the float kernel)."""
    for k in range(n_sym):
        lane = k % n_lanes
        code_pi = st[0] >> pi_sh
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
        _digital_step(k, 2 * int(code) + 1, n_pre, wf, ffe_shift, wd, dfe_shift,
                      levels_out, out_bits, do_round, pd_ffe, kp_sh, ki_sh,
                      clamp_int, pd_off, n_lanes, lane_shift, lat, xh, dec, v_out,
                      queue, st)
    return n_sym


def _fixed_loop_digital_py(xin, pi_sh, n_pre, wf, ffe_shift, wd, dfe_shift,
                           levels_out, out_bits, do_round, pd_ffe, kp_sh, ki_sh,
                           clamp_int, pd_off, n_lanes, lane_shift, lat, xh, pi_out,
                           dec, v_out, queue, st):
    """The digital part alone, from a recorded input-word stream."""
    for k in range(xin.size):
        pi_out[k] = st[0] >> pi_sh
        _digital_step(k, xin[k], n_pre, wf, ffe_shift, wd, dfe_shift, levels_out,
                      out_bits, do_round, pd_ffe, kp_sh, ki_sh, clamp_int, pd_off,
                      n_lanes, lane_shift, lat, xh, dec, v_out, queue, st)
    return xin.size


# numba resolves these globals when it compiles, so rebinding them to the
# jitted versions first is enough (the same pattern as cdr.kernels._farrow)
_ashift_py = _ashift
_digital_step = _digital_step_py
_farrow_fx = _farrow_py
_fixed_loop_closed = _fixed_loop_closed_py
_fixed_loop_digital = _fixed_loop_digital_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _ashift = numba.njit(cache=True, inline="always")(_ashift_py)
        _farrow_fx = numba.njit(cache=True, inline="always")(_farrow_py)
        _digital_step = numba.njit(cache=True)(_digital_step_py)
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

    @property
    def pi_sh(self) -> int:
        return self.ph_frac - self.pi_bits

    def digital_args(self):
        return (self.n_pre, self.wf, self.ffe_shift, self.wd, self.dfe_shift,
                self.levels_out, self.out_bits, self.do_round, self.pd_ffe,
                self.kp_sh, self.ki_sh, self.clamp_int, self.pd_off, self.n_lanes,
                self.lane_shift, self.lat)


def build_fixed_loop(cfg, w_ffe: np.ndarray, w_dfe: np.ndarray, levels: np.ndarray,
                     q_step: float, out_bits: int = OUT_BITS) -> FixedLoop:
    """Quantise a float receiver (weights, levels, CDR settings) for the loop."""
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
        in_lsb=float(in_lsb), out_lsb=float(out_lsb), pd_lsb=float(pd_lsb))


def float_equivalent_gains(fl: FixedLoop, osr: int) -> tuple[float, float, float, float]:
    """(kp, ki, clamp, pd_offset) the float ADC kernel needs to run the loop
    ``fl`` implements -- what it converges to as the words widen."""
    def gain(sh):          # c [UI] = pd_int 2^(sh - ph_frac); pd_f = pd_int pd_lsb / L
        return osr * 2.0 ** (sh - fl.ph_frac) * fl.n_lanes / fl.pd_lsb
    clamp = osr * fl.clamp_int * 2.0 ** -fl.ph_frac
    return gain(fl.kp_sh), gain(fl.ki_sh), clamp, fl.pd_off * fl.pd_lsb / fl.n_lanes


def _state(fl: FixedLoop, n: int):
    return (np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64),
            np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64),
            np.zeros(fl.lat + 1, dtype=np.int64), np.zeros(6, dtype=np.int64))


def run_fixed_loop(fl: FixedLoop, y: np.ndarray, osr: int, pos0: float, n_sym: int,
                   adc, noise: np.ndarray, rx_clk: np.ndarray) -> dict:
    """The closed loop over waveform ``y``; ``adc`` is the run's ``TiAdc``.

    Returns the per-symbol records: ``xin`` (input words), ``pi`` (PI code
    used for symbol k), ``phase`` (sample position, clock included),
    ``dec`` / ``v_out`` (indexed by symbol s = k - n_pre; ``n_dec`` valid),
    and the scalars ``replay_digital`` and the SV testbench need.
    """
    xh, pi, dec, v_out, queue, st = _state(fl, n_sym)
    phase = np.zeros(n_sym)
    done = _fixed_loop_closed(
        np.asarray(y, dtype=np.float64), osr, float(pos0), int(n_sym),
        float(osr) / (1 << fl.pi_bits), fl.pi_sh,
        np.asarray(adc.offsets, dtype=np.float64), np.asarray(adc.gains, dtype=np.float64),
        np.asarray(adc.skews, dtype=np.float64), float(adc.q_step), int(adc.code_max),
        np.asarray(noise, dtype=np.float64), np.asarray(rx_clk, dtype=np.float64),
        *fl.digital_args(), xh, pi, phase, dec, v_out, queue, st)
    done = int(done)
    n_dec = max(done - fl.n_pre, 0)
    return {"xin": xh[:done], "pi": pi[:done], "phase": phase[:done],
            "dec": dec[:n_dec], "v_out": v_out[:n_dec], "n_dec": n_dec}


def replay_digital(fl: FixedLoop, xin: np.ndarray) -> dict:
    """The digital back end alone on a recorded input-word stream: the golden
    model ``rtl/adc_dsp_loop.sv`` is held to."""
    xin = np.asarray(xin, dtype=np.int64)
    n = xin.size
    xh, pi, dec, v_out, queue, st = _state(fl, n)
    _fixed_loop_digital(xin, fl.pi_sh, *fl.digital_args(), xh, pi, dec, v_out, queue, st)
    n_dec = max(n - fl.n_pre, 0)
    return {"pi": pi, "dec": dec[:n_dec], "v_out": v_out[:n_dec], "n_dec": n_dec}


def loop_artifacts(fl: FixedLoop, rec: dict) -> dict:
    """Vectors and parameters for ``dump_loop_vectors`` / the SV testbench."""
    return {"xin": rec["xin"], "pi": rec["pi"], "dec": rec["dec"], "v_out": rec["v_out"],
            "w_ffe_int": fl.wf, "w_dfe_int": fl.wd, "levels_out": fl.levels_out,
            "params": {"N": int(rec["xin"].size), "N_DEC": int(rec["n_dec"]),
                       "NF": int(fl.wf.size), "ND": int(fl.wd.size),
                       "NL": int(fl.levels_out.size), "N_PRE": fl.n_pre,
                       "FFE_SHIFT": fl.ffe_shift, "DFE_SHIFT": fl.dfe_shift,
                       "OUT_BITS": fl.out_bits, "DO_ROUND": fl.do_round,
                       "PD_FFE": fl.pd_ffe, "KP_SH": fl.kp_sh, "KI_SH": fl.ki_sh,
                       "CLAMP": fl.clamp_int, "PD_OFF": fl.pd_off,
                       "LANES": fl.n_lanes, "LANE_SHIFT": fl.lane_shift,
                       "LAT": fl.lat, "PI_SH": fl.pi_sh}}


def dump_loop_vectors(path, art: dict, cw: int = 64):
    """Write the loop's vectors (one integer per line) and ``loop_dims.svh``."""
    from pathlib import Path

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    for name in ("xin", "pi", "dec", "v_out", "w_ffe_int", "w_dfe_int", "levels_out"):
        np.savetxt(path / f"loop_{name}.txt", np.atleast_1d(art[name]), fmt="%d")
    with open(path / "loop_dims.svh", "w", encoding="utf-8") as fh:
        fh.write("// generated by halo_serdes.dsp.fixed_loop.dump_loop_vectors\n")
        for k, v in {**art["params"], "CW": cw}.items():
            fh.write(f"localparam longint {k} = {int(v)};\n")
    return path
