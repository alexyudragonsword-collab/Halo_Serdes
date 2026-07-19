"""MLSD, sliding detector, RS-FEC, and jitter-decomposition tests."""

import numpy as np
import pytest

from halo_serdes.analysis.jitter import calc_jitter
from halo_serdes.analysis.metrics import qfunc
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import TxConfig
from halo_serdes.dsp.kernels import slice_nearest
from halo_serdes.dsp.mlsd import sliding_detector, viterbi_mlsd
from halo_serdes.fec import (
    bits_to_gf_symbols,
    gf_symbols_to_bits,
    pre_to_post_fec_ber,
    rs_kp4,
    rs_kr4,
)
from halo_serdes.tx.jitter import edge_jitter_seq, jittered_zoh


# ------------------------------------------------------------------- MLSD ---


def test_viterbi_on_1plusD_duobinary_channel():
    """1+D channel (h=[1,1]): symbol-by-symbol slicing is hopeless at high
    noise, MLSE recovers most of the 2.1 dB d_min advantage."""
    rng = np.random.default_rng(11)
    n = 30_000
    levels = np.array([-1.0, 1.0])
    sym = rng.integers(0, 2, size=n)
    v = levels[sym]
    y_clean = v.copy()
    y_clean[1:] += v[:-1]  # 1+D
    sigma = 0.55
    y = y_clean + rng.normal(scale=sigma, size=n)

    dec_v = viterbi_mlsd(y, levels, np.array([1.0, 1.0]))
    ber_v = np.mean(dec_v[1:] != sym[1:])
    # naive: slice y - feedback of *sliced* history (DFE-like, error prop)
    dec_n = slice_nearest(y - np.concatenate([[0.0], levels[sym[:-1]]]), levels)
    ber_ideal_fb = np.mean(dec_n[1:] != sym[1:])  # even with genie feedback
    assert ber_v < 0.5 * ber_ideal_fb, (ber_v, ber_ideal_fb)
    # MLSE should approach matched-filter bound territory
    assert ber_v < 3 * qfunc(1.0 / sigma)


def test_viterbi_ideal_channel_reduces_to_slicer():
    rng = np.random.default_rng(12)
    n = 5_000
    levels = np.array([-1.0, -1 / 3, 1 / 3, 1.0])
    sym = rng.integers(0, 4, size=n)
    y = levels[sym] + rng.normal(scale=0.05, size=n)
    dec = viterbi_mlsd(y, levels, np.array([1.0]))
    assert np.array_equal(dec, slice_nearest(y, levels))


def test_sliding_detector_corrects_errors():
    """Residual postcursor channel: the error-event detector must beat the
    plain slicer."""
    rng = np.random.default_rng(13)
    n = 60_000
    levels = np.array([-1.0, 1.0])
    sym = rng.integers(0, 2, size=n)
    v = levels[sym]
    resid = 0.3
    y = v.copy()
    y[1:] += resid * v[:-1]
    y += rng.normal(scale=0.42, size=n)
    # initial decisions: DFE-style with decided feedback
    dec0 = np.zeros(n, dtype=np.int64)
    prev = 0.0
    for k in range(n):
        val = y[k] - resid * prev
        dec0[k] = 1 if val > 0 else 0
        prev = levels[dec0[k]]
    ber0 = np.mean(dec0 != sym)
    # two passes (converged); margin=0 is the pure ML two-hypothesis rule
    dec1 = sliding_detector(y, dec0, levels, resid, 3, 0.0)
    dec1 = sliding_detector(y, dec1, levels, resid, 3, 0.0)
    ber1 = np.mean(dec1 != sym)
    # low-cost error-event detector: modest but real gain (~0.8x); full MLSE
    # gains are covered by test_viterbi_on_1plusD_duobinary_channel
    assert ber1 < 0.85 * ber0, (ber0, ber1)


# -------------------------------------------------------------------- FEC ---


def test_kp4_corrects_up_to_t15():
    rs = rs_kp4()
    rng = np.random.default_rng(14)
    msg = rng.integers(0, 1024, size=514)
    cw = rs.encode(msg)
    assert cw.size == 544
    assert np.array_equal(cw[:514], msg)  # systematic
    # t errors: corrected
    bad = cw.copy()
    pos = rng.choice(544, size=15, replace=False)
    bad[pos] ^= rng.integers(1, 1024, size=15)
    dec, n_err = rs.decode(bad)
    assert np.array_equal(dec, msg)
    assert n_err == 15
    # t+1 errors: uncorrectable (flagged or mis-decoded)
    bad2 = cw.copy()
    pos2 = rng.choice(544, size=16, replace=False)
    bad2[pos2] ^= rng.integers(1, 1024, size=16)
    dec2, n_err2 = rs.decode(bad2)
    assert n_err2 == -1 or not np.array_equal(dec2, msg)


def test_kr4_corrects_up_to_t7():
    rs = rs_kr4()
    rng = np.random.default_rng(15)
    msg = rng.integers(0, 1024, size=514)
    cw = rs.encode(msg)
    assert cw.size == 528
    bad = cw.copy()
    pos = rng.choice(528, size=7, replace=False)
    bad[pos] ^= rng.integers(1, 1024, size=7)
    dec, n_err = rs.decode(bad)
    assert np.array_equal(dec, msg)


def test_bit_symbol_packing_roundtrip():
    rng = np.random.default_rng(16)
    bits = rng.integers(0, 2, size=5140).astype(np.int8)
    syms = bits_to_gf_symbols(bits)
    assert syms.size == 514
    back = gf_symbols_to_bits(syms)
    assert np.array_equal(back, bits)


def test_post_fec_projection_monotonic():
    pres = [1e-3, 3e-4, 1e-4, 3e-5]
    posts = [pre_to_post_fec_ber(p, "kp4") for p in pres]
    assert all(a > b for a, b in zip(posts, posts[1:]))
    # KP4 at 1e-4 pre-FEC must be waterfall-deep
    assert pre_to_post_fec_ber(1e-4, "kp4") < 1e-12
    # KR4 (t=7) is weaker than KP4 (t=15)
    assert pre_to_post_fec_ber(3e-4, "kr4") > pre_to_post_fec_ber(3e-4, "kp4")


# ----------------------------------------------------------------- jitter ---


def test_jitter_decomposition_recovers_injected():
    """Inject known RJ + SJ + DCD, recover each within tolerance."""
    ui = 1 / 32e9
    osr = 64
    rj_in, sj_in, dcd_in = 0.008, 0.03, 0.04  # UI units
    cfg = LinkConfig(modulation="nrz", symbol_rate=32e9, osr=osr,
                     tx=TxConfig(rj_ui=rj_in, sj_ui=sj_in, sj_freq=50e6,
                                 dcd_ui=dcd_in))
    rng = np.random.default_rng(17)
    n_sym = 40_000
    pattern_len = 127
    from halo_serdes.core.prbs import prbs_bits

    bits = prbs_bits(7, n_sym)  # repeating PRBS7: pattern averaging applies
    v = np.where(bits > 0, 0.5, -0.5)
    jit = edge_jitter_seq(n_sym, cfg, rng, v)
    y = jittered_zoh(v, osr, jit, ui)
    jr = calc_jitter(y, ui / osr, ui, pattern_len)

    assert abs(jr.rj / ui - rj_in) / rj_in < 0.25, jr.summary(ui)
    assert abs(jr.pj / ui - 2 * sj_in) / (2 * sj_in) < 0.3, jr.summary(ui)  # pk-pk = 2A
    assert abs(jr.dcd / ui - dcd_in) / dcd_in < 0.3, jr.summary(ui)
    # ideal square wave has no ISI jitter
    assert jr.isi / ui < 0.02


def test_jitter_clean_waveform_is_quiet():
    ui = 1 / 32e9
    osr = 32
    from halo_serdes.core.prbs import prbs_bits

    bits = prbs_bits(7, 5_000)
    v = np.where(bits > 0, 0.5, -0.5)
    y = jittered_zoh(v, osr, np.zeros(v.size + 1), ui)
    jr = calc_jitter(y, ui / osr, ui, 127)
    assert jr.rj / ui < 5e-3
    assert jr.pj / ui < 2e-2
