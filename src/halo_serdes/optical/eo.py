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

import numpy as np

# the curve is shared with the Tx driver (one compression parameter for both)
from ..core.static_curve import StaticCurve, rlm  # noqa: F401  (re-exported)


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

def static_curve(cfg) -> StaticCurve | None:
    """The configured large-signal curve, or None for a linear E/O."""
    if cfg.kind == "none" or cfg.li_compression <= 0.0:
        return None
    er = 10.0 ** (cfg.er_db / 10.0)
    return StaticCurve(kind="rollover" if cfg.kind == "vcsel_mmf" else "eam",
                       compression=float(cfg.li_compression),
                       floor=-(er + 1.0) / (er - 1.0))


def optical_rlm(cfg, drive_levels=(-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0)) -> float:
    """R_LM of the optical levels the curve makes from the given drive levels.

    This is the sense in which ``tx.rlm`` becomes a derived quantity once the
    E/O is nonlinear: ``tx.rlm`` sets the driver's inner levels, the curve
    decides where the light ends up."""
    curve = static_curve(cfg)
    u = np.asarray(drive_levels, dtype=np.float64)
    return rlm(curve(u) if curve is not None else u)
