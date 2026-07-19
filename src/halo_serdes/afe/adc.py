"""Time-interleaved ADC behavioral model (parameter container).

The actual per-symbol sampling/quantization happens inside the ADC-DSP RX
kernel (cdr/kernels.py) — this class turns AdcConfig into concrete per-lane
mismatch vectors and quantizer constants, and provides the standalone
quantizer used by unit tests.

Non-idealities:
- quantization: mid-rise, ``n_bits``, full-scale ``fullscale`` (differential);
- ENOB: excess Gaussian noise sigma_x = FS * sqrt(2^-2*ENOB - 2^-2*N) / sqrt(12)
  so that total (quantization + thermal/aperture) matches the requested ENOB;
- per-lane offset/gain mismatch (Gaussian draws, or calibrated to zero);
- per-lane sampling skew in UI (drawn Gaussian, converted to samples).
"""

from __future__ import annotations

import numpy as np

from ..config.schema import AdcConfig


class TiAdc:
    def __init__(self, cfg: AdcConfig, osr: int, rng: np.random.Generator) -> None:
        self.cfg = cfg
        n = cfg.n_lanes
        self.n_lanes = n
        self.q_step = cfg.fullscale / (2 ** cfg.n_bits)
        self.code_max = 2 ** (cfg.n_bits - 1) - 1
        if cfg.calibrated:
            self.offsets = np.zeros(n)
            self.gains = np.ones(n)
        else:
            self.offsets = rng.normal(scale=cfg.offset_sigma, size=n) if cfg.offset_sigma else np.zeros(n)
            self.gains = 1.0 + (rng.normal(scale=cfg.gain_sigma, size=n) if cfg.gain_sigma else np.zeros(n))
        self.skews = (rng.normal(scale=cfg.skew_sigma_ui, size=n) * osr
                      if cfg.skew_sigma_ui else np.zeros(n))

    @property
    def noise_sigma(self) -> float:
        """Excess (non-quantization) noise to realize the configured ENOB."""
        if self.cfg.enob is None or self.cfg.enob >= self.cfg.n_bits:
            return 0.0
        fs = self.cfg.fullscale
        var = fs * fs / 12.0 * (2.0 ** (-2 * self.cfg.enob) - 2.0 ** (-2 * self.cfg.n_bits))
        return float(np.sqrt(max(var, 0.0)))

    def quantize(self, x: np.ndarray) -> np.ndarray:
        """Standalone mid-rise quantizer (unit tests / offline use)."""
        code = np.clip(np.round(x / self.q_step - 0.5), -self.code_max - 1, self.code_max)
        return (code + 0.5) * self.q_step

    def snr_of_sine(self, n: int = 65536) -> float:
        """Measured quantizer SNR for a full-scale sine [dB] (test hook:
        ideal N-bit quantizer gives 6.02*N + 1.76 dB)."""
        t = np.arange(n)
        x = (self.cfg.fullscale / 2) * 0.9999 * np.sin(2 * np.pi * t * 0.12345)
        q = self.quantize(x)
        err = q - x
        return float(10 * np.log10(np.mean(x ** 2) / np.mean(err ** 2)))
