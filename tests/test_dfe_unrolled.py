"""Speculative (loop-unrolled) DFE tap-1 tests.

Physics under test:
1. with ideal comparators and an ideal summing node, unrolled == direct
   (identical decision sequences — unrolling is an implementation move,
   not an algorithm change);
2. with a bandwidth-limited summing node, unrolled escapes the tap-1
   settling penalty and wins;
3. per-branch comparator offsets are an unrolled-specific impairment and
   degrade the link as they grow;
4. loop_delay_ui > 1 kills tap-1 only in direct mode.
"""

import dataclasses

import numpy as np

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link


def _cfg(dfe: DfeConfig, noise=0.003, n_symbols=60_000) -> LinkConfig:
    return LinkConfig(
        modulation="nrz", symbol_rate=32e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.25, rdc=3.0,
                              r_skin=2.5e-3, loss_tangent=0.015),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="mixed_signal",
                    ctle=CtleConfig(enable=True, peak_db=9.0),
                    dfe=dfe, noise_rms=noise),
        sim=SimConfig(n_symbols=n_symbols, seed=8, pattern="prbs31"),
    )


def test_unrolled_equals_direct_when_ideal():
    """No sum_bw limit, no comparator offsets: bit-identical decisions."""
    d_direct = DfeConfig(n_taps=3, adapt="none", tap1_mode="direct")
    d_unroll = DfeConfig(n_taps=3, adapt="none", tap1_mode="unrolled")
    r0 = run_time_link(_cfg(d_direct))
    r1 = run_time_link(_cfg(d_unroll))
    assert np.allclose(r0.y_slicer, r1.y_slicer, atol=1e-12)
    assert r0.ber.n_errors == r1.ber.n_errors


def test_unrolled_escapes_summing_node_bandwidth():
    """Kernel-level, postcursor-only synthetic channel so the settling error
    is not masked by other ISI: slow summing node hurts the direct tap-1,
    the unrolled tap-1 (threshold mux) is immune."""
    from halo_serdes.cdr import ms_rx
    from halo_serdes.tx.jitter import jittered_zoh

    UI, OSR = 1 / 32e9, 16
    rng = np.random.default_rng(9)
    n = 40_000
    levels = np.array([-0.5, 0.5])
    sym = rng.integers(0, 2, size=n)
    v = levels[sym]
    post = 0.45
    vv = v.copy()
    vv[1:] += post * v[:-1]
    y = jittered_zoh(vv, OSR, np.zeros(n + 1), UI)
    y = np.concatenate([np.zeros(4 * OSR), y, np.zeros(4 * OSR)])
    ref = np.full(n - 1000, -1, dtype=np.int64)

    def run(unrolled, sum_alpha):
        return ms_rx(y, OSR, 4 * OSR + OSR / 2.0, n - 1000, levels,
                     np.array([post]), 0.0, 100, OSR / 32, OSR / 2048, 0.0,
                     sum_alpha, ref, 0, 0, unrolled, np.zeros(2))

    slow = 0.5  # summing node settles to 50% in 1 UI
    dec_d, ys_d, *_ = run(0, slow)
    dec_u, ys_u, *_ = run(1, slow)
    err_d = np.abs(ys_d[2000:] - levels[sym[2000: dec_d.size]]).std()
    err_u = np.abs(ys_u[2000:] - levels[sym[2000: dec_u.size]]).std()
    # direct: residual settling error ~ (1-alpha)*delta_fb; unrolled: none
    assert err_u < 0.3 * err_d, (err_d, err_u)
    # and with an ideal summing node both modes coincide
    dec_d1, ys_d1, *_ = run(0, 1.0)
    dec_u1, ys_u1, *_ = run(1, 1.0)
    assert np.allclose(ys_d1, ys_u1, atol=1e-12)


def test_comparator_offsets_degrade_unrolled():
    base = DfeConfig(n_taps=2, adapt="none", tap1_mode="unrolled",
                     comparator_offset_sigma=0.0)
    bad = dataclasses.replace(base, comparator_offset_sigma=0.04)
    r0 = run_time_link(_cfg(base))
    r1 = run_time_link(_cfg(bad))
    # 40 mV per-branch offsets vs ~±30 mV slicer levels: must clearly hurt
    assert r1.slicer_snr_db < r0.slicer_snr_db - 1.0, (
        r0.slicer_snr_db, r1.slicer_snr_db)


def test_loop_delay_kills_tap1_only_in_direct_mode():
    d_direct = DfeConfig(n_taps=2, adapt="none", tap1_mode="direct",
                         loop_delay_ui=1.3)
    d_unroll = DfeConfig(n_taps=2, adapt="none", tap1_mode="unrolled",
                         loop_delay_ui=1.3)
    r0 = run_time_link(_cfg(d_direct))
    r1 = run_time_link(_cfg(d_unroll))
    # direct: tap-1 zeroed by the constraint -> residual postcursor ISI
    assert r0.extras["w_dfe0"][0] == 0.0
    assert r1.extras["w_dfe0"][0] != 0.0
    assert r1.slicer_snr_db > r0.slicer_snr_db
