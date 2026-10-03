"""Memoryless large-signal compression curve shared by the Tx driver and the
E/O, so both are measured with the same compression parameter c.

It lives in ``core`` because two packages that must not import each other use
it: ``optical/`` (laser / modulator, ``optical/eo.py`` re-exports it) and
``tx/`` (the driver, ``tx/driver.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class StaticCurve:
    """Memoryless transfer in normalised units: input ``u`` with the outer
    levels at -1 and +1, output ``g(u)`` with the outer levels at -1 and +1.

    Pinning both ends keeps the outer swing (for light: OMA and ER) exactly
    what the configuration states; what the curve changes is where the inner
    levels land and how the waveform between symbols is bent -- the part of a
    real transmitter a linear model cannot have.

    ``compression`` c = 1 - (smaller end slope / larger end slope) on [-1, 1]:

    - ``"rollover"``: g = u + kappa (1 - u^2), kappa = c / (2 (2 - c)); concave,
      the top level is compressed. For a laser this is the second-order L-I
      curve with thermal rollover; past the peak u = 1 / (2 kappa) the output
      holds there rather than falling.
    - ``"eam"``: g = 2 (exp(gamma (u + 1) / 2) - 1) / (exp(gamma) - 1) - 1,
      gamma = -ln(1 - c); convex, the bottom level is compressed (the
      exponential absorption edge of an electro-absorption modulator).

    ``floor`` clips the output from below (zero optical power for light,
    -inf for a driver). Approximate, unsourced to a clause: second-order L-I
    and exponential EAM transmission are the textbook shapes (Coldren,
    Corzine and Masanovic, Diode Lasers and Photonic Integrated Circuits,
    ch. 2 and 8); 802.3 constrains their result through RLM and TDECQ.
    """

    kind: str
    compression: float
    floor: float

    @property
    def kappa(self) -> float:
        c = self.compression
        return c / (2.0 * (2.0 - c))

    @property
    def gamma(self) -> float:
        return float(-np.log1p(-self.compression))

    def __call__(self, u):
        u = np.asarray(u, dtype=np.float64)
        if self.kind == "rollover":
            k = self.kappa
            if k > 0.0:
                u = np.minimum(u, 0.5 / k)
            g = u + k * (1.0 - u * u)
        else:
            gm = self.gamma
            g = 2.0 * np.expm1(gm * (u + 1.0) / 2.0) / np.expm1(gm) - 1.0
        return np.maximum(g, self.floor)

    def apply(self, y: np.ndarray, amplitude: float) -> np.ndarray:
        """The curve on a waveform whose outer levels sit at +-``amplitude``."""
        return amplitude * self(np.asarray(y) / amplitude)


def rlm(levels) -> float:
    """Level separation mismatch ratio of four ascending PAM4 levels.

    IEEE 802.3 120D.3.1.2: with V_mid = (V0 + V3) / 2,
    ES1 = (V1 - V_mid) / (V0 - V_mid), ES2 = (V2 - V_mid) / (V3 - V_mid),
    R_LM = min(3 ES1, 3 ES2, 2 - 3 ES1, 2 - 3 ES2); 1 for equal spacing.
    """
    v0, v1, v2, v3 = (float(x) for x in levels)
    mid = 0.5 * (v0 + v3)
    es1 = (v1 - mid) / (v0 - mid)
    es2 = (v2 - mid) / (v3 - mid)
    return min(3 * es1, 3 * es2, 2 - 3 * es1, 2 - 3 * es2)
