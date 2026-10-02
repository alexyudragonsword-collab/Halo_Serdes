"""Tx driver: memoryless compression, then the driver pole (Hammerstein).

The nonlinearity acts on the waveform after the zero-order hold, before the
bandwidth: the output stage saturates on its instantaneous drive and its
load then rounds the edges.

``kind``:

- ``"curve"``: the shared ``core.static_curve.StaticCurve`` ("rollover"
  form), with ``compression`` c defined exactly as the E/O's
  ``optical.li_compression``. One parameter for both, so in an optical
  topology the driver and the laser can be compared on one scale. The outer
  levels are pinned at +-``fullscale``; the top level compresses.
- ``"tanh"``: y = V tanh(x / V), V set so a single tone of amplitude
  ``p1db_v`` sees 1 dB gain compression. Symmetric (differential stage).
- ``"cubic"``: y = x - c3 x^3, c3 = 4 / (3 OIP3^2) from ``oip3_v`` (the
  output-referred third-order intercept amplitude of a unity-gain stage);
  input clamped at the gain peak 1 / sqrt(3 c3). Has the HD3 closed form
  ``hd3_cubic_db``.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..core.static_curve import StaticCurve
from ..core.waveform import Waveform


def apply_single_pole(wave: Waveform, f3db: float) -> Waveform:
    """Single-pole lowpass H(f) = 1/(1 + jf/f3db), applied via rFFT."""
    n = wave.y.size
    f = np.fft.rfftfreq(n, d=wave.dt)
    H = 1.0 / (1.0 + 1j * f / f3db)
    y = np.fft.irfft(np.fft.rfft(wave.y) * H, n=n)
    return Waveform(y, wave.dt, wave.t0)


def _fundamental_gain(r: float, n: int = 1024) -> float:
    th = 2 * np.pi * (np.arange(n) + 0.5) / n
    s = np.sin(th)
    return float(2.0 * np.mean(np.tanh(r * s) * s) / r)


@lru_cache(maxsize=1)
def _tanh_p1db_ratio() -> float:
    """r = A_1dB / V for y = V tanh(x / V): fundamental gain 10^(-1/20)."""
    target = 10.0 ** (-1.0 / 20.0)
    lo, hi = 1e-3, 5.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _fundamental_gain(mid) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def cubic_c3(oip3_v: float) -> float:
    return 4.0 / (3.0 * oip3_v * oip3_v)


def hd3_cubic_db(amplitude: float, oip3_v: float) -> float:
    """HD3 [dB] of y = x - c3 x^3 for a tone of ``amplitude``: third harmonic
    c3 A^3 / 4 over fundamental A - 3 c3 A^3 / 4."""
    c3 = cubic_c3(oip3_v)
    a = amplitude
    return float(20 * np.log10((c3 * a ** 3 / 4.0) / (a - 0.75 * c3 * a ** 3)))


class DriverNl:
    def __init__(self, kind: str, *, compression: float = 0.0, fullscale: float = 1.0,
                 p1db_v: float | None = None, oip3_v: float | None = None) -> None:
        self.kind = kind
        self.fullscale = float(fullscale)
        if kind == "curve":
            self.curve = StaticCurve("rollover", float(compression), -np.inf)
        elif kind == "tanh":
            if not p1db_v:
                raise ValueError("drv_nl='tanh' needs drv_p1db_v")
            self.v_sat = float(p1db_v) / _tanh_p1db_ratio()
        elif kind == "cubic":
            if not oip3_v:
                raise ValueError("drv_nl='cubic' needs drv_oip3_v")
            self.c3 = cubic_c3(float(oip3_v))
            self.x_peak = 1.0 / np.sqrt(3.0 * self.c3)
        elif kind != "none":
            raise ValueError(f"unknown driver nonlinearity {kind!r}")

    def __call__(self, y: np.ndarray) -> np.ndarray:
        y = np.asarray(y, dtype=np.float64)
        if self.kind == "curve":
            return self.curve.apply(y, self.fullscale)
        if self.kind == "tanh":
            return self.v_sat * np.tanh(y / self.v_sat)
        if self.kind == "cubic":
            x = np.clip(y, -self.x_peak, self.x_peak)
            return x - self.c3 * x ** 3
        return y
