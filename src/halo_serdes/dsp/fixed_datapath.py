"""Bit-true fixed-point FFE + DFE + slicer datapath (RTL golden model).

This is the block that becomes RTL. Semantics follow DragonPHY2 comb_ffe:
integer weightxcode products, wide accumulator, per-output arithmetic right
shift with the RTL rounding adder, saturation to the output width. The CDR
stays behavioral (float); the datapath below is replayed bit-true on the ADC
codes captured from a float run.

``dump_vectors`` writes stimulus/response pairs for RTL testbench lockstep
comparison (DragonPHY recorder -> numpy assertion pattern, in reverse).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ..config.schema import NumericConfig, QFormat
from ..core.fixed import to_int


def calculate_ffe_shift(w_float: np.ndarray, w_q: QFormat, code_bits: int,
                        out_bits: int) -> int:
    """Choose the accumulator right-shift so the FFE output uses its full
    output range without overflow (DragonPHY FFEHelper.calculate_shift idea).

    Worst-case |acc| <= sum|w_int| * code_max; pick shift s.t. it fits
    out_bits-1 magnitude bits.
    """
    w_int = to_int(w_float, w_q)
    acc_max = int(np.abs(w_int).sum()) * ((1 << (code_bits - 1)) - 1)
    shift = 0
    lim = (1 << (out_bits - 1)) - 1
    while (acc_max >> shift) > lim:
        shift += 1
    return shift


def _ffe_dfe_pr_fixed_py(codes: np.ndarray, w_ffe: np.ndarray, ffe_shift: int,
                         w_dfe: np.ndarray, dfe_shift: int,
                         levels_out: np.ndarray, n_pre: int,
                         out_bits: int, do_round: int,
                         pr_mode: int, nt: int, pr_abc: np.ndarray, pr_fl: int,
                         pr_lv: np.ndarray):
    """Bit-true datapath: all-int64 in, decisions + fixed slicer values out.

    codes: ADC codes (int); w_ffe/w_dfe: integer weights; levels_out: slicer
    levels in the *output* scale (int). Output v[s] = sat(sat(rshift(sum_i
    w_ffe[i]*codes[s+n_pre-i])) - rshift(sum_d w_dfe[d]*levels_out[x[s-nt-d]])).

    Partial response, as ``fixed_loop._digital_step_py`` (``pr_mode``): 0 is
    the delta target (``x`` the decisions, ``nt`` 1); 1 subtracts
    ``ctl = rshift(a L[x[s-1]] + b L[x[s-2]] + c L[x[s-3]], pr_fl)`` before
    slicing (``pr_abc`` the words, ``nt`` the target's length: the DFE starts
    after the controlled cursors); 2 slices the composite levels ``pr_lv`` and
    decodes the precoded 1 + D symbol as ``(q - dec[s-1]) mod N``, the line
    symbol estimate ``x`` clipped from ``q - x[s-1]``. ``v`` is the slicer
    value before the controlled cursor, as in the loop."""
    n = codes.size
    nf = w_ffe.size
    nd = w_dfe.size
    nl = levels_out.size
    n_out = n - nf
    dec = np.zeros(n_out, dtype=np.int64)
    xl = np.zeros(n_out, dtype=np.int64)
    v_out = np.zeros(n_out, dtype=np.int64)
    lim_hi = (1 << (out_bits - 1)) - 1
    lim_lo = -(1 << (out_bits - 1))
    half_f = 1 << (ffe_shift - 1) if ffe_shift > 0 else 0
    half_d = 1 << (dfe_shift - 1) if dfe_shift > 0 else 0
    half_p = 1 << (pr_fl - 1) if pr_fl > 0 else 0
    gap = nt if pr_mode > 0 else 1
    for s in range(n_out):
        k = s + n_pre
        acc = 0
        for i in range(nf):
            j = k - i
            if 0 <= j < n:
                acc += w_ffe[i] * codes[j]
        if do_round == 1:
            acc += half_f
        acc >>= ffe_shift
        if acc > lim_hi:
            acc = lim_hi
        elif acc < lim_lo:
            acc = lim_lo
        fb = 0
        for d in range(nd):
            jj = s - gap - d
            if jj >= 0:
                fb += w_dfe[d] * levels_out[xl[jj]]
        if do_round == 1:
            fb += half_d
        fb >>= dfe_shift
        v = acc - fb
        if v > lim_hi:
            v = lim_hi
        elif v < lim_lo:
            v = lim_lo
        v_out[s] = v
        ctl = 0
        if pr_mode == 1:
            for t in range(1, nt):
                if s - t >= 0:
                    ctl += pr_abc[t - 1] * levels_out[xl[s - t]]
            if do_round == 1:
                ctl += half_p
            ctl >>= pr_fl
        if pr_mode == 2:
            q = 0
            bd = abs(v - pr_lv[0])
            for m in range(1, pr_lv.size):
                dd = abs(v - pr_lv[m])
                if dd < bd:
                    bd = dd
                    q = m
            dec[s] = (q - (dec[s - 1] if s >= 1 else 0)) % nl
            xc = q - (xl[s - 1] if s >= 1 else 0)
            if xc < 0:
                xc = 0
            elif xc > nl - 1:
                xc = nl - 1
            xl[s] = xc
        else:
            u = v - ctl
            best = 0
            bd = abs(u - levels_out[0])
            for m in range(1, nl):
                dd = abs(u - levels_out[m])
                if dd < bd:
                    bd = dd
                    best = m
            dec[s] = best
            xl[s] = best
    return dec, v_out


def _ffe_dfe_fixed_py(codes: np.ndarray, w_ffe: np.ndarray, ffe_shift: int,
                      w_dfe: np.ndarray, dfe_shift: int,
                      levels_out: np.ndarray, n_pre: int,
                      out_bits: int, do_round: int):
    """The delta target (``_ffe_dfe_pr_fixed_py`` with ``pr_mode`` 0)."""
    return ffe_dfe_pr_fixed(codes, w_ffe, ffe_shift, w_dfe, dfe_shift, levels_out, n_pre,
                            out_bits, do_round, 0, 1, np.zeros(3, dtype=np.int64), 0,
                            np.zeros(1, dtype=np.int64))


ffe_dfe_pr_fixed = _ffe_dfe_pr_fixed_py
ffe_dfe_fixed = _ffe_dfe_fixed_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        ffe_dfe_pr_fixed = numba.njit(cache=True)(_ffe_dfe_pr_fixed_py)
        ffe_dfe_fixed = numba.njit(cache=True)(_ffe_dfe_fixed_py)
    except ImportError:
        pass


def pr_words(target, numeric: NumericConfig, precode: bool, adapt: str,
             levels_float: np.ndarray, out_lsb: float):
    """The partial-response arguments of the bit-true datapath for a target
    (the float run's ``extras['pr_target']`` when the target adapted): mode
    (``engine.timedomain``'s rule -- precoded 1 + D at a = 1 without PR LMS
    is the composite slicer, 2; any other target 1), target length, the
    controlled cursors as words with ``dfe_weight.fl`` fraction bits (as
    ``fixed_loop``), and the composite levels in the output scale."""
    t = tuple(float(c) for c in target)
    nt = len(t)
    if nt < 2:
        return 0, 1, np.zeros(3, dtype=np.int64), 0, np.zeros(1, dtype=np.int64)
    if nt > 4:
        raise ValueError(f"pr target {t}: the bit-true datapath takes up to three "
                         "controlled cursors")
    pfl = int(numeric.dfe_weight.fl)
    abc = np.zeros(3, dtype=np.int64)
    for i, c in enumerate(t[1:]):
        abc[i] = int(np.round(c * 2.0 ** pfl))
    mode = 2 if (precode and nt == 2 and t[1] == 1.0 and adapt == "none") else 1
    pr_lv = np.zeros(1, dtype=np.int64)
    if mode == 2:
        lv = np.asarray(levels_float, dtype=float)
        n_lv = lv.size
        comp = np.array([np.mean([lv[i] + lv[q - i]
                                  for i in range(max(0, q - n_lv + 1), min(q, n_lv - 1) + 1)])
                         for q in range(2 * n_lv - 1)])
        pr_lv = np.round(comp / out_lsb).astype(np.int64)
    return mode, nt, abc, pfl, pr_lv


def run_fixed_datapath(codes_int: np.ndarray, w_ffe_float: np.ndarray,
                       w_dfe_float: np.ndarray, levels_float: np.ndarray,
                       n_pre: int, numeric: NumericConfig,
                       code_fullscale: float, code_bits: int, pr=None,
                       pr_target=None, precode: bool = False):
    """Float weights/levels -> quantize -> bit-true replay. Returns
    (dec, v_float, artifacts dict for dump_vectors).

    ``pr``: the link's ``PrConfig``; with a partial-response target the
    replay is that receiver's slicer (``_ffe_dfe_pr_fixed_py``, the loop's
    rules, 2026-11-03 -- it refused before). ``pr_target``: the target the
    float run ended on (``extras['pr_target']``, which differs from
    ``pr.target`` when it adapted); ``precode``: the link's ``precode``.
    The weights are fixed (the float run's), so are a, b and c."""
    wq = numeric.ffe_weight
    w_ffe_int = to_int(w_ffe_float, wq)
    w_dfe_int = to_int(w_dfe_float, numeric.dfe_weight)
    out_bits = 12
    ffe_shift = calculate_ffe_shift(w_ffe_float, wq, code_bits, out_bits)
    # output LSB: code_lsb * w_lsb * 2^ffe_shift
    code_lsb = code_fullscale / (1 << code_bits)
    out_lsb = code_lsb * (2.0 ** -wq.fl) * (1 << ffe_shift)
    levels_out = np.round(levels_float / out_lsb).astype(np.int64)
    # DFE weights are normalized to main; feedback fb = w*level_out needs the
    # same output scale: shift = dfe fl
    dfe_shift = numeric.dfe_weight.fl
    do_round = 1 if wq.rounding == "round" else 0
    active = pr is not None and pr.active
    target = (pr_target if pr_target is not None else pr.target) if active else (1.0,)
    pr_mode, nt, pr_abc, pr_fl, pr_lv = pr_words(
        target, numeric, precode, pr.adapt if active else "none", levels_float, out_lsb)
    dec, v_out = ffe_dfe_pr_fixed(codes_int.astype(np.int64), w_ffe_int, ffe_shift,
                                  w_dfe_int, dfe_shift, levels_out, n_pre,
                                  out_bits, do_round, pr_mode, nt, pr_abc, pr_fl, pr_lv)
    art = {"codes": codes_int.astype(np.int64), "w_ffe_int": w_ffe_int,
           "w_dfe_int": w_dfe_int, "levels_out": levels_out,
           "ffe_shift": np.int64(ffe_shift), "dfe_shift": np.int64(dfe_shift),
           "out_bits": np.int64(out_bits), "n_pre": np.int64(n_pre),
           "pr_mode": np.int64(pr_mode), "pr_nt": np.int64(nt), "pr_fl": np.int64(pr_fl),
           "pr_abc": pr_abc, "pr_lv": pr_lv,
           "dec": dec, "v_out": v_out}
    return dec, v_out * out_lsb, art


def dump_vectors(path: str | Path, artifacts: dict) -> Path:
    """Write RTL testbench lockstep vectors (.npz + flat .txt per signal)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path / "vectors.npz", **artifacts)
    # the PR words and composite levels only under a PR target: the delta
    # target's files stay what they were (the testbench reads absent ones as 0)
    pr_on = int(artifacts.get("pr_mode", 0)) > 0
    for name in ("codes", "dec", "v_out", "w_ffe_int", "w_dfe_int", "levels_out",
                 "pr_abc", "pr_lv"):
        if name in ("pr_abc", "pr_lv") and not pr_on:
            continue
        if name in artifacts:
            np.savetxt(path / f"{name}.txt", np.atleast_1d(artifacts[name]),
                       fmt="%d")
    with open(path / "params.txt", "w", encoding="utf-8") as fh:
        for k in ("ffe_shift", "dfe_shift", "out_bits", "n_pre", "pr_mode", "pr_nt", "pr_fl"):
            if k in artifacts:
                fh.write(f"{k} {int(artifacts[k])}\n")
    return path


# Localparams the RTL only ever uses as a shift amount (or passes as one to a helper).
# They are emitted as 32-bit ``int``: a 64-bit shift amount buys nothing, and Verilator
# 5.020 constant-folds ``longint >>> <longint localparam>`` to 0, which made
# tb_adc_dsp_loop's final-calibration check fail on equal values whenever the summed
# register was a propagated constant (issue #136). Everything else stays ``longint``:
# bounds and values such as CAL_PM0 or CLAMP need the width.
SV_SHIFT_PARAMS = frozenset({
    "FFE_SHIFT", "DFE_SHIFT", "OUT_BITS", "KP_SH", "KI_SH", "LANE_SHIFT", "PI_SH",
    "SH_F", "SH_D", "SH_A", "G", "PFL", "CAL_F", "CAL_B", "CAL_SH_O", "CAL_SH_G",
    "CAL_SH_P", "CAL_SH_S", "SQ_SHIFT", "C_FL",
})


def sv_localparam(name: str, value: int) -> str:
    """One ``localparam`` line of a ``*_dims.svh``: ``int`` for shift amounts,
    ``longint`` for the rest (see ``SV_SHIFT_PARAMS``)."""
    kind = "int" if name in SV_SHIFT_PARAMS else "longint"
    return f"localparam {kind} {name} = {int(value)};\n"


def dump_sv_package(path: str | Path, artifacts: dict, cw: int = 32) -> Path:
    """Emit a SystemVerilog include (``dims.svh``) of localparams for the RTL
    datapath — the single-source YAML/config -> SV package hook. Dimensions and
    fixed-point shifts come straight from the golden-model artifacts so the RTL
    and the Python golden cannot drift.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    n = int(np.atleast_1d(artifacts["codes"]).size)
    n_out = int(np.atleast_1d(artifacts["dec"]).size)
    params = {
        "N": n, "N_OUT": n_out,
        "NF": int(np.atleast_1d(artifacts["w_ffe_int"]).size),
        "ND": int(np.atleast_1d(artifacts["w_dfe_int"]).size),
        "NL": int(np.atleast_1d(artifacts["levels_out"]).size),
        "N_PRE": int(artifacts["n_pre"]),
        "FFE_SHIFT": int(artifacts["ffe_shift"]),
        "DFE_SHIFT": int(artifacts["dfe_shift"]),
        "OUT_BITS": int(artifacts["out_bits"]),
        "DO_ROUND": 1, "CW": int(cw),
        # partial response (0 / 1 / 2 as _ffe_dfe_pr_fixed_py); the words are
        # in pr_abc.txt, the composite levels in pr_lv.txt
        "PR_MODE": int(artifacts.get("pr_mode", 0)),
        "PR_NT": int(artifacts.get("pr_nt", 1)),
        "PR_FL": int(artifacts.get("pr_fl", 0)),
        "NPL": int(np.atleast_1d(artifacts.get("pr_lv", np.zeros(1))).size),
    }
    with open(path / "dims.svh", "w", encoding="utf-8") as fh:
        fh.write("// generated by halo_serdes.dsp.fixed_datapath.dump_sv_package\n")
        for k, v in params.items():
            fh.write(f"localparam int {k} = {v};\n")
    return path
