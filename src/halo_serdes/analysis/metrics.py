"""Link metrics: slicer-input SNR, SER/BER helpers, Q-function utilities.

For the ADC-based architecture the primary quality metrics are slicer-input
SNR and SER (eye diagrams carry little information after digital EQ).
"""

from __future__ import annotations

import numpy as np
from scipy.special import erfc


def qfunc(x: np.ndarray | float) -> np.ndarray | float:
    """Gaussian tail probability Q(x)."""
    return 0.5 * erfc(np.asarray(x) / np.sqrt(2.0))


def qfunc_inv(p: float) -> float:
    from scipy.special import erfcinv

    return float(np.sqrt(2.0) * erfcinv(2.0 * p))


def slicer_snr_db(y_slicer: np.ndarray, ideal: np.ndarray) -> float:
    """SNR at the slicer input: signal power over residual error power."""
    err = y_slicer - ideal
    return 10.0 * np.log10(np.mean(ideal ** 2) / max(np.mean(err ** 2), 1e-30))


def nrz_ber_awgn(amplitude: float, sigma: float) -> float:
    """Analytic NRZ BER over AWGN: Q(A/sigma)."""
    return float(qfunc(amplitude / sigma))


def pam4_ser_awgn(outer_amplitude: float, sigma: float, rlm: float = 1.0) -> float:
    """Analytic PAM4 symbol error rate over AWGN with nearest-level slicing:
    SER = (3/2) Q(d/sigma), d = distance from level to threshold = A/3 (rlm=1)."""
    d = rlm * outer_amplitude / 3.0
    return float(1.5 * qfunc(d / sigma))


def ber_confidence(n_errors: int, n_bits: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval for a measured BER."""
    if n_bits == 0:
        return (float("nan"), float("nan"))
    p = n_errors / n_bits
    denom = 1 + z ** 2 / n_bits
    center = (p + z ** 2 / (2 * n_bits)) / denom
    half = z * np.sqrt(p * (1 - p) / n_bits + z ** 2 / (4 * n_bits ** 2)) / denom
    return (max(center - half, 0.0), center + half)
