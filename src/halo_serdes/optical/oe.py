"""Opto-electric front end: photodiode + linear TIA, and the optical level scale.

Sources:

- Linear TIAs for 53 GBd PAM4: 40-43 GHz bandwidth, 10-13 pA/sqrt(Hz)
  input-referred noise, up to 5 kohm differential transimpedance (Renesas
  HXR45100 / HXR45400 datasheets); < 15 pA/sqrt(Hz), 34-36 GHz, 0.1-5.4 kohm
  (Qorvo TGA4875). A ~0.75 x baud bandwidth with a second-order roll-off is
  what those parts present; 802.3's reference receiver for TDECQ is a
  fourth-order Bessel-Thomson at 0.5 x baud, which is a measurement filter,
  not a device.
- Responsivity 0.5-0.8 A/W for 850 nm GaAs / 1310 nm InGaAs PIN diodes:
  textbook range (Agrawal ch. 4.1), approximate, unsourced to a clause.
- Level powers: 802.3db / 802.3dj define OMA_outer = P3 - P0 and
  ER = P3 / P0 for PAM4, which fixes both outer levels; the inner levels
  are placed at thirds (ideal RLM) because stage 1 has no L-I curve.
"""

from __future__ import annotations

import numpy as np


def response(cfg, f: np.ndarray) -> np.ndarray:
    """Second-order Butterworth PD + TIA response, 3 dB at ``tia_bw_hz``."""
    s = 1j * np.asarray(f, dtype=np.float64) / cfg.tia_bw_hz
    return 1.0 / (s * s + np.sqrt(2.0) * s + 1.0)


def level_powers(cfg, modulation: str) -> np.ndarray:
    """Optical power [W] of each transmitted level, ascending."""
    n = 4 if modulation == "pam4" else 2
    return cfg.p_low_w + cfg.oma_w * np.arange(n) / (n - 1)


def output_swing_v(cfg) -> float:
    """Physical TIA output swing for the outer levels, R * OMA * Tz [V];
    reporting only -- the simulation keeps its electrical scale."""
    return cfg.responsivity_a_w * cfg.oma_w * cfg.tz_ohm
