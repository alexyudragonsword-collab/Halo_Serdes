"""Electro-optic (laser / modulator) small-signal response.

Sources for the magnitudes the defaults and examples use:

- VCSEL: 850 nm devices qualified for 100GBASE-SR1 (IEEE 802.3db) show a
  3 dB modulation bandwidth above 24 GHz with the resonance peak heavily
  damped (Chalmers / Coherent 100G PAM4 VCSEL reports); the relaxation
  frequency itself is a device parameter the standard does not state, so
  ``f_r_hz`` 20-25 GHz with ``damping_hz`` of the same order is approximate,
  literature, not a clause.
- EML: 802.3dj 200GBASE-DR1 bounds the transmitter by transition time
  (8.5 ps max, 20-80 %) and TDECQ, not by a bandwidth; a single pole with
  the 3 dB point at 0.35 / t_r ~ 40 GHz reproduces that transition time.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def vcsel_response(f: np.ndarray, f_r_hz: float, damping_hz: float) -> np.ndarray:
    """Second-order laser rate-equation response, unity at DC.

    H(f) = f_r^2 / (f_r^2 - f^2 + j f gamma), gamma = ``damping_hz`` (the
    damping rate over 2 pi). Peaks at sqrt(f_r^2 - gamma^2 / 2) when
    underdamped (Agrawal, Fiber-Optic Communication Systems, ch. 3.5).
    """
    f = np.asarray(f, dtype=np.float64)
    return f_r_hz ** 2 / (f_r_hz ** 2 - f ** 2 + 1j * f * damping_hz)


def eml_response(f: np.ndarray, bw_hz: float) -> np.ndarray:
    """Single-pole modulator + driver response, 3 dB at ``bw_hz``."""
    f = np.asarray(f, dtype=np.float64)
    return 1.0 / (1.0 + 1j * f / bw_hz)


def vcsel_bandwidth_hz(f_r_hz: float, damping_hz: float) -> float:
    """Closed-form 3 dB bandwidth of ``vcsel_response`` (|H|^2 = 1/2)."""
    a = 2.0 * f_r_hz ** 2 - damping_hz ** 2
    x = 0.5 * (a + np.sqrt(a * a + 4.0 * f_r_hz ** 4))
    return float(np.sqrt(x))


def response(cfg, f: np.ndarray) -> np.ndarray:
    """E/O transfer for an ``OpticalConfig`` on grid ``f`` [Hz]."""
    if cfg.kind == "vcsel_mmf":
        return vcsel_response(f, cfg.f_r_hz, cfg.damping_hz)
    if cfg.kind == "eml_smf":
        return eml_response(f, cfg.f_r_hz)
    raise ValueError(f"no E/O response for optical.kind {cfg.kind!r}")


# --- large-signal curve (stage 3) --------------------------------------------

@dataclass(frozen=True)
class StaticCurve:
    """Memoryless E/O transfer in normalised units: drive ``u`` with the outer
    PAM4 drive levels at -1 and +1, power ``g(u)`` with the outer optical
    levels at -1 and +1.

    Pinning both ends keeps OMA and ER exactly what ``OpticalConfig`` states;
    what the curve changes is where the inner levels land and how the
    waveform between symbols is bent -- which is the part of a real
    transmitter a linear model cannot have.

    ``compression`` c = 1 - (smaller end slope / larger end slope) on [-1, 1]:

    - ``"rollover"`` (VCSEL): P = u + kappa (1 - u^2), the second-order L-I
      curve with thermal rollover, kappa = c / (2 (2 - c)); concave, the top
      level is compressed. Past the rollover peak u = 1 / (2 kappa) the power
      holds there rather than falling.
    - ``"eam"`` (EML): P = 2 (exp(gamma (u + 1) / 2) - 1) / (exp(gamma) - 1) - 1,
      the exponential absorption edge, gamma = -ln(1 - c); convex, the bottom
      level is compressed.

    Both clip at zero optical power (``floor``, below threshold / full
    extinction). The link applies the curve to the E/O's small-signal output
    (Wiener order; ``ChannelModel.from_topology`` says why). Approximate, unsourced to a clause: second-order L-I and
    exponential EAM transmission are the textbook shapes (Coldren, Corzine and
    Masanovic, Diode Lasers and Photonic Integrated Circuits, ch. 2 and 8);
    802.3 constrains their result through RLM and TDECQ, not the curve.
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
        """The curve on a waveform whose outer drive levels sit at +-``amplitude``."""
        return amplitude * self(np.asarray(y) / amplitude)


def static_curve(cfg) -> StaticCurve | None:
    """The configured large-signal curve, or None for a linear E/O."""
    if cfg.kind == "none" or cfg.li_compression <= 0.0:
        return None
    er = 10.0 ** (cfg.er_db / 10.0)
    return StaticCurve(kind="rollover" if cfg.kind == "vcsel_mmf" else "eam",
                       compression=float(cfg.li_compression),
                       floor=-(er + 1.0) / (er - 1.0))


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


def optical_rlm(cfg, drive_levels=(-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0)) -> float:
    """R_LM of the optical levels the curve makes from the given drive levels.

    This is the sense in which ``tx.rlm`` becomes a derived quantity once the
    E/O is nonlinear: ``tx.rlm`` sets the driver's inner levels, the curve
    decides where the light ends up."""
    curve = static_curve(cfg)
    u = np.asarray(drive_levels, dtype=np.float64)
    return rlm(curve(u) if curve is not None else u)
