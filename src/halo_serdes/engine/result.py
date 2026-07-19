"""Explicit result containers (deliberately not a God-object: the engine
returns these; nothing is stashed on shared mutable state)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.prbs import BerResult


@dataclass
class SimResult:
    """Time-domain (or static-link) simulation outcome."""

    ber: BerResult
    ser: float
    slicer_snr_db: float
    n_symbols: int
    ffe_taps: np.ndarray | None = None
    dfe_taps: np.ndarray | None = None
    sample_phase: int = 0
    # small waveform windows kept for plotting (streaming engine keeps only these)
    eye_data: np.ndarray | None = None      # (n_traces, 2*osr) folded segments
    y_slicer: np.ndarray | None = None      # slicer-input samples (window)
    extras: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (f"n={self.n_symbols}  BER={self.ber.ber:.3e} "
                f"({self.ber.n_errors}/{self.ber.n_checked})  SER={self.ser:.3e}  "
                f"slicer SNR={self.slicer_snr_db:.1f} dB")
