"""Analytic (RLGC / ABCD cascade) channel modeling.

Port of serdespy ``chmodel.py`` (itself from Carusone's Wireline-ChModel-Matlab),
vectorized over frequency. All two-ports are represented as ABCD matrices of
shape ``(n_freq, 2, 2)`` (complex).
"""

from __future__ import annotations

import numpy as np


def tline_abcd(r, l, g, c, d: float, f: np.ndarray) -> np.ndarray:
    """ABCD of a transmission line of length ``d`` [m] from per-unit-length
    RLGC (scalars or frequency-shaped arrays)."""
    w = 2 * np.pi * np.asarray(f, dtype=np.float64)
    zser = r + 1j * w * l
    ypar = g + 1j * w * c
    gamma_d = d * np.sqrt(zser * ypar)
    with np.errstate(divide="ignore", invalid="ignore"):
        z0 = np.sqrt(np.divide(zser, ypar, out=np.full_like(zser, np.inf, dtype=complex),
                               where=np.abs(ypar) > 0))
    abcd = np.zeros((f.size, 2, 2), dtype=complex)
    abcd[:, 0, 0] = np.cosh(gamma_d)
    abcd[:, 0, 1] = z0 * np.sinh(gamma_d)
    abcd[:, 1, 0] = np.sinh(gamma_d) / z0
    abcd[:, 1, 1] = abcd[:, 0, 0]
    return abcd


def series_z_abcd(z: np.ndarray) -> np.ndarray:
    """ABCD of a series impedance."""
    z = np.atleast_1d(np.asarray(z, dtype=complex))
    abcd = np.zeros((z.size, 2, 2), dtype=complex)
    abcd[:, 0, 0] = 1.0
    abcd[:, 0, 1] = z
    abcd[:, 1, 1] = 1.0
    return abcd


def shunt_y_abcd(y: np.ndarray) -> np.ndarray:
    """ABCD of a shunt admittance."""
    y = np.atleast_1d(np.asarray(y, dtype=complex))
    abcd = np.zeros((y.size, 2, 2), dtype=complex)
    abcd[:, 0, 0] = 1.0
    abcd[:, 1, 0] = y
    abcd[:, 1, 1] = 1.0
    return abcd


def shunt_cap_abcd(c: float, f: np.ndarray) -> np.ndarray:
    return shunt_y_abcd(1j * 2 * np.pi * np.asarray(f) * c)


def series_cap_abcd(c: float, f: np.ndarray) -> np.ndarray:
    w = 2 * np.pi * np.asarray(f, dtype=np.float64)
    with np.errstate(divide="ignore"):
        z = 1.0 / (1j * w * c)
    z[~np.isfinite(z)] = 1e18  # DC open approximated by a huge impedance
    return series_z_abcd(z)


def cascade_abcd(*networks: np.ndarray) -> np.ndarray:
    """Cascade ABCD matrices (frequency-wise matmul, left to right = source to load)."""
    out = networks[0]
    for n in networks[1:]:
        out = np.matmul(out, n)
    return out


def sparam_to_abcd(s11, s12, s21, s22, z0: float) -> np.ndarray:
    """2-port S-parameters -> ABCD (serdespy ``sparam``)."""
    det = s11 * s22 - s12 * s21
    abcd = np.zeros((np.asarray(s11).size, 2, 2), dtype=complex)
    abcd[:, 0, 0] = (1 + s11 - s22 - det) / (2 * s21)
    abcd[:, 0, 1] = z0 * (1 + s11 + s22 + det) / (2 * s21)
    abcd[:, 1, 0] = (1 - s11 - s22 + det) / (2 * z0 * s21)
    abcd[:, 1, 1] = (1 - s11 + s22 - det) / (2 * s21)
    return abcd


def abcd_to_transfer(abcd: np.ndarray, zs: complex, zl: complex) -> np.ndarray:
    """Voltage transfer Vout/Vsource of a terminated ABCD two-port:
    H = Zl / (A*Zl + B + Zs*(C*Zl + D))."""
    A, B = abcd[:, 0, 0], abcd[:, 0, 1]
    C, D = abcd[:, 1, 0], abcd[:, 1, 1]
    return zl / (A * zl + B + zs * (C * zl + D))


def skin_effect_rlgc(f: np.ndarray, rdc: float, r_skin: float, l_per_m: float,
                     c_per_m: float, loss_tangent: float = 0.0,
                     g_per_m: float = 0.0) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """Frequency-dependent RLGC for a lossy PCB trace.

    R(f) = rdc + r_skin*sqrt(f)  (skin effect)
    G(f) = g + 2*pi*f*C*tan_delta  (dielectric loss)
    """
    f = np.asarray(f, dtype=np.float64)
    r = rdc + r_skin * np.sqrt(f)
    g = g_per_m + 2 * np.pi * f * c_per_m * loss_tangent
    c = np.full_like(f, c_per_m)
    return r, l_per_m, g, c
