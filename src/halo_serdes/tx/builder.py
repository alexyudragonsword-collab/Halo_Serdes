"""Tx behavioral model: symbol-domain FIR (de-emphasis), ZOH oversampling,
optional driver bandwidth. Jitter injection arrives in Phase 2.
"""

from __future__ import annotations

import numpy as np

from ..config.schema import LinkConfig
from ..core.mapping import nrz_levels, pam4_levels
from ..core.waveform import Waveform


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
    """Symbols -> oversampled ideal-edge Tx waveform (ZOH), FIR applied.

    Driver bandwidth (cfg.tx.bw) is applied as a single-pole lowpass in the
    frequency domain (fixing serdespy's tx_bandwidth NameError approach).
    """
    v = symbols_to_voltages(symbols, cfg)
    if len(cfg.tx.fir_taps) > 1:
        v = tx_fir(v, cfg.tx.fir_taps, cfg.tx.fir_n_pre)
    y = np.repeat(v, cfg.osr)
    wave = Waveform(y, cfg.dt)
    if cfg.tx.bw is not None:
        wave = apply_single_pole(wave, cfg.tx.bw)
    return wave


def apply_single_pole(wave: Waveform, f3db: float) -> Waveform:
    """Single-pole lowpass H(f) = 1/(1 + jf/f3db), applied via rFFT."""
    n = wave.y.size
    f = np.fft.rfftfreq(n, d=wave.dt)
    H = 1.0 / (1.0 + 1j * f / f3db)
    y = np.fft.irfft(np.fft.rfft(wave.y) * H, n=n)
    return Waveform(y, wave.dt, wave.t0)
