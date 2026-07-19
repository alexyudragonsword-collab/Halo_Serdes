"""Parameterized CTLE: one zero, two poles (library class, not a script —
addressing the serdespy gap where CTLE existed only in examples).

    H(s) = g_dc * (1 + s/wz) / ((1 + s/wp1)(1 + s/wp2))

The zero is placed from the requested peaking: fz = fp1 / 10^(peak_db/20),
so high-frequency boost relative to DC is ~peak_db (fp2 >> fp1 rolls off
above Nyquist). Defaults: fp1 = Nyquist, fp2 = 2*Nyquist (PyBERT make_ctle
convention).
"""

from __future__ import annotations

import numpy as np

from ..config.schema import CtleConfig
from ..core.waveform import Waveform


class Ctle:
    def __init__(self, gdc_db: float = 0.0, peak_db: float = 6.0,
                 fp1: float = 16e9, fp2: float | None = None,
                 fz: float | None = None) -> None:
        self.gdc = 10.0 ** (gdc_db / 20.0)
        self.fp1 = fp1
        self.fp2 = fp2 if fp2 is not None else 2.0 * fp1
        self.fz = fz if fz is not None else fp1 / (10.0 ** (peak_db / 20.0))

    @classmethod
    def from_config(cls, cfg: CtleConfig, f_nyquist: float) -> "Ctle":
        fp1 = cfg.fp1 if cfg.fp1 is not None else f_nyquist
        return cls(gdc_db=cfg.gdc_db, peak_db=cfg.peak_db, fp1=fp1,
                   fp2=cfg.fp2, fz=cfg.fz)

    def transfer(self, f: np.ndarray) -> np.ndarray:
        s = 1j * np.asarray(f, dtype=np.float64)
        return (self.gdc * (1.0 + s / self.fz)
                / ((1.0 + s / self.fp1) * (1.0 + s / self.fp2)))

    def peaking_db(self) -> float:
        """Realized peaking: max |H| over DC |H|, in dB."""
        f = np.linspace(1e6, 4 * self.fp2, 20001)
        mag = np.abs(self.transfer(f))
        return 20.0 * np.log10(mag.max() / abs(self.gdc))

    def apply(self, wave: Waveform) -> Waveform:
        n = wave.y.size
        f = np.fft.rfftfreq(n, d=wave.dt)
        y = np.fft.irfft(np.fft.rfft(wave.y) * self.transfer(f), n=n)
        return Waveform(y, wave.dt, wave.t0)
