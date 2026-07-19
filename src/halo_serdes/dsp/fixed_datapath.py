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


def _ffe_dfe_fixed_py(codes: np.ndarray, w_ffe: np.ndarray, ffe_shift: int,
                      w_dfe: np.ndarray, dfe_shift: int,
                      levels_out: np.ndarray, n_pre: int,
                      out_bits: int, do_round: int):
    """Bit-true datapath: all-int64 in, decisions + fixed slicer values out.

    codes: ADC codes (int); w_ffe/w_dfe: integer weights; levels_out: slicer
    levels in the *output* scale (int). Output v[s] = sat(rshift(sum_i
    w_ffe[i]*codes[s+n_pre-i]) ) - rshift(sum_d w_dfe[d]*levels_out[dec]).
    """
    n = codes.size
    nf = w_ffe.size
    nd = w_dfe.size
    nl = levels_out.size
    n_out = n - nf
    dec = np.zeros(n_out, dtype=np.int64)
    v_out = np.zeros(n_out, dtype=np.int64)
    lim_hi = (1 << (out_bits - 1)) - 1
    lim_lo = -(1 << (out_bits - 1))
    half_f = 1 << (ffe_shift - 1) if ffe_shift > 0 else 0
    half_d = 1 << (dfe_shift - 1) if dfe_shift > 0 else 0
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
            jj = s - 1 - d
            if jj >= 0:
                fb += w_dfe[d] * levels_out[dec[jj]]
        if do_round == 1:
            fb += half_d
        fb >>= dfe_shift
        v = acc - fb
        if v > lim_hi:
            v = lim_hi
        elif v < lim_lo:
            v = lim_lo
        v_out[s] = v
        best = 0
        bd = abs(v - levels_out[0])
        for m in range(1, nl):
            dd = abs(v - levels_out[m])
            if dd < bd:
                bd = dd
                best = m
        dec[s] = best
    return dec, v_out


ffe_dfe_fixed = _ffe_dfe_fixed_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        ffe_dfe_fixed = numba.njit(cache=True)(_ffe_dfe_fixed_py)
    except ImportError:
        pass


def run_fixed_datapath(codes_int: np.ndarray, w_ffe_float: np.ndarray,
                       w_dfe_float: np.ndarray, levels_float: np.ndarray,
                       n_pre: int, numeric: NumericConfig,
                       code_fullscale: float, code_bits: int):
    """Float weights/levels -> quantize -> bit-true replay. Returns
    (dec, v_float, artifacts dict for dump_vectors)."""
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
    dec, v_out = ffe_dfe_fixed(codes_int.astype(np.int64), w_ffe_int, ffe_shift,
                               w_dfe_int, dfe_shift, levels_out, n_pre,
                               out_bits, do_round)
    art = {"codes": codes_int.astype(np.int64), "w_ffe_int": w_ffe_int,
           "w_dfe_int": w_dfe_int, "levels_out": levels_out,
           "ffe_shift": np.int64(ffe_shift), "dfe_shift": np.int64(dfe_shift),
           "out_bits": np.int64(out_bits), "n_pre": np.int64(n_pre),
           "dec": dec, "v_out": v_out}
    return dec, v_out * out_lsb, art


def dump_vectors(path: str | Path, artifacts: dict) -> Path:
    """Write RTL testbench lockstep vectors (.npz + flat .txt per signal)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path / "vectors.npz", **artifacts)
    for name in ("codes", "dec", "v_out", "w_ffe_int", "w_dfe_int", "levels_out"):
        if name in artifacts:
            np.savetxt(path / f"{name}.txt", np.atleast_1d(artifacts[name]),
                       fmt="%d")
    with open(path / "params.txt", "w", encoding="utf-8") as fh:
        for k in ("ffe_shift", "dfe_shift", "out_bits", "n_pre"):
            fh.write(f"{k} {int(artifacts[k])}\n")
    return path


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
    }
    with open(path / "dims.svh", "w", encoding="utf-8") as fh:
        fh.write("// generated by halo_serdes.dsp.fixed_datapath.dump_sv_package\n")
        for k, v in params.items():
            fh.write(f"localparam int {k} = {v};\n")
    return path
