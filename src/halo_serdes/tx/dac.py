"""Transmit DAC: one code per UI, uniform quantisation plus static INL.

The Tx-side counterpart of ``afe/adc.py::TiAdc`` (single lane, no
interleaving, but with a per-code output table instead of a per-lane gain):

- ``n_bits`` codes over the peak-to-peak range ``fullscale`` (the same
  convention as ``tx.swing`` and ``adc.fullscale``), mid-rise: with
  h = fullscale / 2, code c drives -h + (c + 1/2) LSB, LSB = fullscale / 2^N.
  Inputs beyond +-h clip to the end codes and are counted
  (``n_over_range``), so a too-small full scale is never silent.
- Static INL from unit-current mismatch. The DAC is built from 2^N - 1 unit
  cells: the low ``N - thermo_msbs`` bits are binary-weighted groups of
  cells, the top ``thermo_msbs`` bits drive 2^m - 1 equal thermometer
  elements. Every cell carries 1 + e, e ~ N(0, unit_sigma) in LSB; the code's
  output is the sum over its active cells. Gain error is removed by an
  endpoint fit (the reference is trimmed to full scale), what is left is the
  INL: Var(INL_c) = unit_sigma^2 c (1 - c / M), M = 2^N - 1, whatever the
  segmentation (which sets DNL and monotonicity, not this variance), and
  averaged over the codes E[INL^2] = unit_sigma^2 (M - 1) / 6.

The mismatch is drawn once at construction, like the ADC's lane mismatch.
"""

from __future__ import annotations

import numpy as np


class TxDac:
    def __init__(self, n_bits: int, fullscale: float, *, thermo_msbs: int = 0,
                 unit_sigma: float = 0.0, rng: np.random.Generator | None = None) -> None:
        if n_bits < 1:
            raise ValueError(f"n_bits must be >= 1, got {n_bits}")
        if not 0 <= thermo_msbs <= n_bits:
            raise ValueError(f"thermo_msbs must be in [0, {n_bits}], got {thermo_msbs}")
        if fullscale <= 0:
            raise ValueError(f"fullscale must be > 0, got {fullscale}")
        self.n_bits = int(n_bits)
        self.fullscale = float(fullscale)
        self.thermo_msbs = int(thermo_msbs)
        self.unit_sigma = float(unit_sigma)
        self.n_codes = 2 ** self.n_bits
        self.code_max = self.n_codes - 1
        self.half = 0.5 * self.fullscale
        self.lsb = self.fullscale / self.n_codes
        self.inl = self._draw_inl(rng)                       # per code, in LSB
        codes = np.arange(self.n_codes, dtype=np.float64)
        self.table = -self.half + self.lsb * (codes + 0.5 + self.inl)

    def _draw_inl(self, rng: np.random.Generator | None) -> np.ndarray:
        if self.unit_sigma <= 0.0:
            return np.zeros(self.n_codes)
        if rng is None:
            raise ValueError("unit_sigma > 0 needs an rng to draw the mismatch from")
        n_bin = self.n_bits - self.thermo_msbs
        cells = 1.0 + rng.normal(scale=self.unit_sigma, size=self.code_max)
        # binary bit b owns cells [2^b - 1, 2^(b+1) - 1); thermometer element j
        # owns the next blocks of 2^n_bin cells each
        bit_sum = np.array([cells[(1 << b) - 1:(1 << (b + 1)) - 1].sum() for b in range(n_bin)])
        n_lo = (1 << n_bin) - 1
        n_th = (1 << self.thermo_msbs) - 1
        th_sum = cells[n_lo:].reshape(n_th, 1 << n_bin).sum(axis=1) if n_th else np.zeros(0)
        th_cum = np.concatenate([[0.0], np.cumsum(th_sum)])
        c = np.arange(self.n_codes)
        lo = c & ((1 << n_bin) - 1)
        bits = (lo[:, None] >> np.arange(n_bin)) & 1 if n_bin else np.zeros((self.n_codes, 0))
        out = bits @ bit_sum + th_cum[c >> n_bin]
        return out - c * out[-1] / self.code_max

    # --------------------------------------------------------------- convert
    def codes(self, x: np.ndarray) -> np.ndarray:
        c = np.floor((np.asarray(x, dtype=np.float64) + self.half) / self.lsb)
        return np.clip(c, 0, self.code_max).astype(np.int64)

    def n_over_range(self, x: np.ndarray) -> int:
        # a value at full scale is not clipped; an FFE trained to exactly fill
        # the range lands there up to rounding
        return int(np.count_nonzero(np.abs(np.asarray(x)) > self.half * (1 + 1e-9)))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self.table[self.codes(x)]

    # ------------------------------------------------------------ test hooks
    @property
    def inl_rms(self) -> float:
        return float(np.sqrt(np.mean(self.inl ** 2)))

    @staticmethod
    def inl_rms_expected(n_bits: int, unit_sigma: float) -> float:
        """E[INL^2] over the codes, square-rooted (module docstring)."""
        m = 2 ** n_bits - 1
        return float(unit_sigma * np.sqrt((m - 1) / 6.0))

    @property
    def sigma_q(self) -> float:
        """Equivalent white error per conversion [V]: LSB^2/12 + E[INL^2]."""
        return float(self.lsb * np.sqrt(1.0 / 12.0 + np.mean(self.inl ** 2)))

    def sqnr_of_sine(self, n: int = 1 << 16, n_amplitudes: int = 16) -> float:
        """SQNR of a full-scale sine [dB]; an ideal N-bit DAC gives 6.02 N + 1.76.

        That closed form assumes the error is uniform over one LSB. A sine
        spends most of its time near its peaks, so for one exact amplitude
        the result depends on where the peaks fall inside the top code: an
        ideal 5-bit quantiser reads 31.48 dB at exactly full scale and 32.07
        dB a quarter LSB below it (closed form 31.86). The hook therefore
        averages the error power over ``n_amplitudes`` sines whose peaks step
        through the top LSB, against the full-scale signal power -- the
        condition the closed form describes.
        """
        t = np.arange(n)
        s = np.sin(2 * np.pi * t * 0.12345678 + 0.3)
        p_err = 0.0
        for k in range(n_amplitudes):
            x = (self.half - self.lsb * (k + 0.5) / n_amplitudes) * s
            p_err += float(np.mean((self(x) - x) ** 2))
        p_sig = 0.5 * self.half ** 2
        return float(10 * np.log10(p_sig / (p_err / n_amplitudes)))
