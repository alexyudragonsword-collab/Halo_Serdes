"""Tx building blocks: level mapping and the symbol-spaced FIR. The
transmitter assembled from them is ``tx.pipeline.TxPipeline``.
"""

from __future__ import annotations

import numpy as np

from ..config.schema import LinkConfig
from ..core.mapping import nrz_levels, pam4_levels
from ..core.waveform import Waveform
from .driver import apply_single_pole  # noqa: F401  (re-exported; it lives with the driver)


def symbols_to_voltages(symbols: np.ndarray, cfg: LinkConfig) -> np.ndarray:
    """Level indices -> voltages using the configured modulation/swing/RLM."""
    if cfg.modulation == "pam4":
        levels = pam4_levels(cfg.tx.swing, cfg.tx.rlm)
    else:
        levels = nrz_levels(cfg.tx.swing)
    return levels[np.asarray(symbols, dtype=np.int64)]


def tx_fir(v_baud: np.ndarray, taps: tuple[float, ...] | np.ndarray, n_pre: int) -> np.ndarray:
    """Apply the symbol-spaced Tx FIR.

    ``taps`` are ordered [pre..., main, post...] with ``n_pre`` precursors.
    Output is aligned so sample k still corresponds to symbol k.
    """
    taps = np.asarray(taps, dtype=np.float64)
    full = np.convolve(v_baud, taps)
    return full[n_pre: n_pre + v_baud.size]


def build_tx_waveform(symbols: np.ndarray, cfg: LinkConfig) -> Waveform:
    """Symbols -> oversampled ideal-edge Tx waveform. Kept for callers of the
    old API; the transmitter itself is ``tx.pipeline.TxPipeline``."""
    from .pipeline import TxPipeline

    pipe = TxPipeline.from_config(cfg)
    return pipe.waveform(pipe.symbol_stage(symbols))
