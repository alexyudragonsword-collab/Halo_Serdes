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
    def __init__(self, cfg: AdcConfig, osr: int, rng: np.random.Generator,
                 fg_seed: int = 0) -> None:
        self.cfg = cfg
        n = cfg.n_lanes
        self.n_lanes = n
        self.q_step = cfg.fullscale / (2 ** cfg.n_bits)
        self.code_max = 2 ** (cfg.n_bits - 1) - 1
        # drawn whether or not ``calibrated`` zeroes them: skipping the draws
        # would hand the skews, the ENOB noise and the Rx clock other values,
        # and the ideal-calibration run would compare a different link
        offsets = rng.normal(scale=cfg.offset_sigma, size=n) if cfg.offset_sigma else np.zeros(n)
        gains = 1.0 + (rng.normal(scale=cfg.gain_sigma, size=n) if cfg.gain_sigma else np.zeros(n))
        self.offsets = np.zeros(n) if cfg.calibrated else offsets
        self.gains = np.ones(n) if cfg.calibrated else gains
        self.skews = (rng.normal(scale=cfg.skew_sigma_ui, size=n) * osr
                      if cfg.skew_sigma_ui else np.zeros(n))
        # what the link was built with, before any foreground correction
        self.true_offsets, self.true_gains = self.offsets.copy(), self.gains.copy()
        self.fg = None
        if cfg.cal.mode == "foreground":
            self._foreground(np.random.default_rng([int(fg_seed), 0xCA1]))

    def _foreground(self, rng: np.random.Generator) -> None:
        """Power-up calibration: measure each lane through its own quantiser
        and noise, then fold the frozen correction (q - o_est) / g_est into
        the lane's offset and gain. Folding it before the quantiser instead
        of after is the approximation: it leaves the quantiser step as it is.
        """
        cal, n = self.cfg.cal, self.n_lanes
        sig = self.noise_sigma
        m = int(cal.fg_samples)
        vref = cal.fg_ref * self.cfg.fullscale / 2.0

        def convert(v):                  # (lanes, m) analog values -> dequantised
            x = self.true_gains[:, None] * v + self.true_offsets[:, None]
            if sig > 0:
                x = x + rng.normal(scale=sig, size=x.shape)
            code = np.clip(np.floor(x / self.q_step), -self.code_max - 1, self.code_max)
            return (code + 0.5) * self.q_step

        o_est = convert(np.zeros((n, m))).mean(axis=1)
        g_est = (convert(np.full((n, m), vref)).mean(axis=1)
                 - convert(np.full((n, m), -vref)).mean(axis=1)) / (2.0 * vref)
        self.fg = {"offsets": o_est, "gains": g_est}
        self.offsets = (self.true_offsets - o_est) / g_est
        self.gains = self.true_gains / g_est

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
