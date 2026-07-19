"""Tx jitter injection tests: injected statistics must be recoverable."""

import numpy as np

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import TxConfig
from halo_serdes.tx.jitter import edge_jitter_seq, jittered_zoh


def _crossings(y: np.ndarray, dt: float, thresh: float = 0.0) -> np.ndarray:
    s = np.signbit(y - thresh)
    idx = np.nonzero(s[:-1] != s[1:])[0]
    frac = (thresh - y[idx]) / (y[idx + 1] - y[idx])
    return (idx + frac) * dt


def test_rj_sigma_recovered():
    ui = 1 / 32e9
    osr = 64  # fine grid for accurate crossing timing
    cfg = LinkConfig(modulation="nrz", symbol_rate=32e9, osr=osr,
                     tx=TxConfig(rj_ui=0.02))
    rng = np.random.default_rng(9)
    n_sym = 20_000
    sym = rng.integers(0, 2, size=n_sym)
    v = np.where(sym > 0, 0.5, -0.5)
    jit = edge_jitter_seq(n_sym, cfg, rng, v)
    y = jittered_zoh(v, osr, jit, ui)
    x = _crossings(y, ui / osr)
    # TIE = crossing time minus nearest ideal boundary
    tie = x / ui - np.round(x / ui)
    sigma = np.std(tie)
    assert 0.85 * 0.02 < sigma < 1.15 * 0.02, sigma


def test_dcd_shifts_rise_fall_apart():
    ui = 1 / 32e9
    osr = 64
    dcd = 0.1
    cfg = LinkConfig(modulation="nrz", symbol_rate=32e9, osr=osr,
                     tx=TxConfig(dcd_ui=dcd))
    rng = np.random.default_rng(10)
    n_sym = 5_000
    sym = rng.integers(0, 2, size=n_sym)
    v = np.where(sym > 0, 0.5, -0.5)
    jit = edge_jitter_seq(n_sym, cfg, rng, v)
    y = jittered_zoh(v, osr, jit, ui)
    x = _crossings(y, ui / osr)
    tie = x / ui - np.round(x / ui)
    slope_up = y[np.minimum((x / (ui / osr)).astype(int) + 1, y.size - 1)] > 0
    mean_rise = tie[slope_up].mean()
    mean_fall = tie[~slope_up].mean()
    assert abs((mean_rise - mean_fall) - dcd) < 0.02


def test_zero_jitter_is_ideal_zoh():
    ui = 1 / 32e9
    osr = 16
    v = np.array([0.5, -0.5, -0.5, 0.5, 0.5])
    y = jittered_zoh(v, osr, np.zeros(v.size + 1), ui)
    assert np.array_equal(y, np.repeat(v, osr))


# --- jitter-budget helpers (task ①: wire calc_jitter into the pipeline) ---

def test_pattern_period():
    from halo_serdes.analysis import pattern_period

    assert pattern_period("prbs7", "nrz") == 127
    assert pattern_period("prbs31", "nrz") == 2 ** 31 - 1
    assert pattern_period("prbs13q", "pam4") == 2 ** 13 - 1
    assert pattern_period("prqs10", "pam4") == 1023


def test_total_jitter_monotone_in_ber():
    from halo_serdes.analysis import JitterResult, total_jitter

    jr = JitterResult(tie=np.zeros(0), isi=1e-12, dcd=0.5e-12, pj=0.3e-12,
                      rj=0.2e-12, mu_l=-0.2e-12, mu_r=0.2e-12, rj_dd=0.2e-12)
    # deeper BER -> wider total jitter (more Gaussian tail)
    assert total_jitter(jr, 1e-6) < total_jitter(jr, 1e-12) < total_jitter(jr, 1e-15)
    # bounded deterministic floor
    assert total_jitter(jr, 1e-12) > jr.isi + jr.dcd + jr.pj


def test_stage_budget_recovers_injected_rj():
    """A repeating pattern with known Tx RJ: the 'tx' stage Rj must land near
    the injected sigma, and the channel stage must show more ISI than Tx."""
    from halo_serdes.analysis import stage_jitter_budget
    from halo_serdes.channel import ChannelModel
    from halo_serdes.config.schema import (
        ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig,
    )
    from halo_serdes.engine import run_time_link

    rj = 0.010
    cfg = LinkConfig(
        modulation="nrz", symbol_rate=16e9, osr=32,
        channel=ChannelConfig(kind="analytic", length_m=0.25),
        tx=TxConfig(swing=1.0, rj_ui=rj),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=6.0),
                    dfe=DfeConfig(n_taps=3), noise_rms=0.0),
        sim=SimConfig(n_symbols=127 * 12, seed=2, pattern="prbs7"))
    res = run_time_link(cfg, channel=ChannelModel.from_config(cfg),
                        collect_jitter=True)
    jb = res.extras["jitter_budget"]
    assert set(jb) >= {"tx", "chnl", "ctle"}
    # Tx-stage random jitter recovers the injected sigma within tolerance
    tx_rj_ui = jb["tx"].rj * cfg.symbol_rate
    assert 0.6 * rj < tx_rj_ui < 1.5 * rj, tx_rj_ui
    # channel adds ISI over the (near-clean) Tx output
    assert jb["chnl"].isi > jb["tx"].isi

    # formatting produces a table with one row per stage
    from halo_serdes.analysis import format_jitter_budget

    txt = format_jitter_budget(jb, cfg.ui)
    assert "tx" in txt and "chnl" in txt and "ctle" in txt


def test_budget_none_when_pattern_too_short():
    """Fewer than 4 pattern periods -> a note, not a bogus decomposition."""
    from halo_serdes.config.schema import ChannelConfig, RxConfig, SimConfig
    from halo_serdes.engine import run_time_link

    cfg = LinkConfig(
        modulation="nrz", symbol_rate=16e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.2),
        tx=TxConfig(swing=1.0, rj_ui=0.01),
        rx=RxConfig(arch="mixed_signal", noise_rms=0.0),
        sim=SimConfig(n_symbols=200, seed=1, pattern="prbs7"))
    res = run_time_link(cfg, collect_jitter=True)
    jb = res.extras["jitter_budget"]
    assert jb is not None and "_note" in jb
