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
