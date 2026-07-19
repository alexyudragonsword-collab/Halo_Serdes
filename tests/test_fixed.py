"""Fixed-point framework tests: RTL shift/round semantics, bit-true
arbitration against an independent integer reference, float convergence."""

import numpy as np
import pytest

from halo_serdes.config.schema import NumericConfig, QFormat
from halo_serdes.core.fixed import from_int, q_limits, rshift_round, saturate, to_int
from halo_serdes.dsp.fixed_datapath import (
    calculate_ffe_shift,
    ffe_dfe_fixed,
    run_fixed_datapath,
)


def test_rshift_round_semantics():
    # floor = Verilog >>> (arithmetic, floors toward -inf)
    assert rshift_round(np.int64(7), 2, "floor") == 1
    assert rshift_round(np.int64(-7), 2, "floor") == -2
    # round-half-up: +2^(s-1) then shift
    assert rshift_round(np.int64(7), 2, "round") == 2     # (7+2)>>2
    assert rshift_round(np.int64(6), 2, "round") == 2     # exactly half rounds up
    assert rshift_round(np.int64(-7), 2, "round") == -2   # -1.75 -> -2 ((-7+2)>>2)
    assert rshift_round(np.int64(-6), 2, "round") == -1   # -1.5 half rounds up
    assert rshift_round(np.int64(5), 0, "round") == 5


def test_to_from_int_roundtrip_and_saturation():
    q = QFormat(10, 8)
    x = np.array([0.5, -0.25, 2.5, -2.0, 0.123])
    i = to_int(x, q)
    lo, hi = q_limits(q)
    assert i.max() <= hi and i.min() >= lo
    back = from_int(i, q)
    # within representable range the round-trip error is <= LSB/2
    inr = (x < hi / 256) & (x > lo / 256)
    assert np.abs(back[inr] - x[inr]).max() <= 0.5 / 256 + 1e-12
    # saturation engaged for 2.5 (max representable ~1.996)
    assert i[2] == hi
    assert saturate(np.int64(1 << 20), 10) == (1 << 9) - 1


def _independent_int_reference(codes, w_ffe, ffe_shift, w_dfe, dfe_shift,
                               levels_out, n_pre, out_bits):
    """Deliberately different implementation (numpy convolution + python
    loop for DFE) used as the bit-true arbiter."""
    codes = [int(c) for c in codes]
    n = len(codes)
    nf = len(w_ffe)
    dec, v_out = [], []
    lim_hi = (1 << (out_bits - 1)) - 1
    lim_lo = -(1 << (out_bits - 1))

    def rnd_shift(v, s):
        if s > 0:
            v += 1 << (s - 1)
        return v >> s

    for s in range(n - nf):
        k = s + n_pre
        acc = sum(int(w_ffe[i]) * codes[k - i] for i in range(nf) if 0 <= k - i < n)
        acc = max(lim_lo, min(lim_hi, rnd_shift(acc, ffe_shift)))
        fb = sum(int(w_dfe[d]) * int(levels_out[dec[s - 1 - d]])
                 for d in range(len(w_dfe)) if s - 1 - d >= 0)
        fb = rnd_shift(fb, dfe_shift)
        v = max(lim_lo, min(lim_hi, acc - fb))
        v_out.append(v)
        dec.append(int(np.argmin([abs(v - int(l)) for l in levels_out])))
    return np.array(dec), np.array(v_out)


def test_bit_true_vs_independent_reference():
    rng = np.random.default_rng(30)
    codes = rng.integers(-128, 128, size=800)
    w_ffe = rng.integers(-100, 256, size=7)
    w_dfe = rng.integers(-40, 40, size=2)
    levels = np.array([-300, -100, 100, 300], dtype=np.int64)
    a_dec, a_v = ffe_dfe_fixed(codes.astype(np.int64), w_ffe.astype(np.int64), 6,
                               w_dfe.astype(np.int64), 8, levels, 3, 12, 1)
    b_dec, b_v = _independent_int_reference(codes, w_ffe, 6, w_dfe, 8,
                                            levels, 3, 12)
    assert np.array_equal(a_dec, b_dec)
    assert np.array_equal(a_v, b_v)


def test_wide_wordlength_converges_to_float():
    """With generous widths, fixed decisions must equal the float datapath."""
    rng = np.random.default_rng(31)
    n = 4000
    levels_f = np.array([-0.3, -0.1, 0.1, 0.3])
    sym = rng.integers(0, 4, size=n)
    v = levels_f[sym]
    y = v.copy()
    y[1:] += 0.15 * v[:-1]
    y += rng.normal(scale=0.02, size=n)
    # "ADC" codes: 12-bit over fullscale 1.0
    code_bits = 12
    codes = np.clip(np.round(y / (1.0 / (1 << code_bits))), -(1 << 11), (1 << 11) - 1)
    w_ffe = np.array([0.02, 1.0, -0.05])
    w_dfe = np.array([0.15])
    numeric = NumericConfig(ffe_weight=QFormat(18, 14), dfe_weight=QFormat(18, 14))
    dec_fx, v_fx, art = run_fixed_datapath(codes, w_ffe, w_dfe, levels_f,
                                           n_pre=1, numeric=numeric,
                                           code_fullscale=1.0, code_bits=code_bits)
    # float reference datapath
    from halo_serdes.dsp.kernels import dfe_static
    from halo_serdes.dsp import apply_ffe

    y_ffe = apply_ffe(y, w_ffe, 1)
    dec_fl, _ = dfe_static(y_ffe, w_dfe, levels_f)
    n_cmp = min(dec_fx.size, dec_fl.size) - 2
    mismatch = np.mean(dec_fx[:n_cmp] != dec_fl[:n_cmp])
    assert mismatch < 2e-3, mismatch


def test_narrow_weights_degrade_gracefully():
    """Shrinking weight word length must not crash and should eventually
    change decisions (BER wall exists)."""
    rng = np.random.default_rng(32)
    n = 3000
    levels_f = np.array([-0.5, 0.5])
    sym = rng.integers(0, 2, size=n)
    v = levels_f[sym]
    y = v + rng.normal(scale=0.05, size=n)
    codes = np.clip(np.round(y / (1.0 / 256)), -128, 127)
    w_ffe = np.array([0.03, 1.0, -0.07, 0.02])
    mismatches = []
    for wl in (12, 8, 5, 3):
        numeric = NumericConfig(ffe_weight=QFormat(wl, wl - 2),
                                dfe_weight=QFormat(wl, wl - 2))
        dec_fx, _, _ = run_fixed_datapath(codes, w_ffe, np.zeros(0), levels_f,
                                          n_pre=1, numeric=numeric,
                                          code_fullscale=1.0, code_bits=8)
        ref = sym[: dec_fx.size]
        mismatches.append(np.mean(dec_fx != ref))
    assert mismatches[0] < 0.01           # generous width: near-clean
    assert mismatches[-1] >= mismatches[0]  # 3-bit weights: no better


def test_calculate_ffe_shift_prevents_overflow():
    w = np.array([0.2, 1.0, -0.4])
    q = QFormat(10, 8)
    s = calculate_ffe_shift(w, q, code_bits=8, out_bits=12)
    w_int = to_int(w, q)
    acc_max = int(np.abs(w_int).sum()) * 127
    assert (acc_max >> s) <= (1 << 11) - 1
    assert s == 0 or (acc_max >> (s - 1)) > (1 << 11) - 1  # minimal shift


def test_dump_vectors_roundtrip(tmp_path):
    from halo_serdes.dsp.fixed_datapath import dump_vectors

    rng = np.random.default_rng(33)
    codes = rng.integers(-128, 128, size=100)
    numeric = NumericConfig()
    dec, v, art = run_fixed_datapath(codes, np.array([1.0]), np.zeros(0),
                                     np.array([-0.25, 0.25]), 0, numeric,
                                     1.0, 8)
    out = dump_vectors(tmp_path / "vec", art)
    loaded = np.load(out / "vectors.npz")
    assert np.array_equal(loaded["dec"], dec)
    assert (out / "codes.txt").exists()
    assert (out / "params.txt").read_text().startswith("ffe_shift")
