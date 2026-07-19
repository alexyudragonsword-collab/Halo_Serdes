"""Touchstone import and differential transfer-function extraction.

Ports two battle-tested approaches and cross-validates them in tests:

1. **Primary (PyBERT style)**: single-ended 4-port -> mixed-mode via ``se2mm``
   -> DD-quadrant 2-port -> safe interpolation onto the analysis grid
   (``interp_s2p`` with conservative extrapolation) -> termination by
   renormalizing port reference impedances (generalized S-parameters), voltage
   transfer H = S21 * sqrt(z0_load / z0_source).

2. **Cross-check (serdespy style)**: explicit reflection-coefficient formula
   H = SDD21 (1+GammaL)(1-GammaS) / [(1-SDD22 GammaL)(1-GammaIn GammaS)] / 2
   (``terminate_gamma``). Kept as an independent implementation for tests.

Multi-port conventions follow PyBERT ``import_freq``: 4-port files are
single-ended (+A, -A, +B, -B after optional renumber detection); 8/12-port
files hold N/4 lanes ordered (+A, +B, -A, -B) per lane.
"""

from __future__ import annotations

from cmath import phase, rect

import numpy as np
import skrf as rf

# ---------------------------------------------------------------------------
# mixed-mode conversion
# ---------------------------------------------------------------------------


def se2mm(ntwk: rf.Network, renumber: bool = False) -> rf.Network:
    """4-port single-ended network -> mixed-mode network (DD/DC/CD/CC blocks).

    Port order in: (1+, 2+, 1-, 2-) i.e. ports (0, 2) are the P/N of side 1.
    With ``renumber=True``, detects the common "1->3, 2->4" through ordering by
    comparing |S21| vs |S31| off DC and renumbers accordingly (PyBERT method).
    """
    (_, rows, cols) = ntwk.s.shape
    if rows != cols or rows != 4:
        raise ValueError("se2mm() needs a square 4-port network")
    ntwk = ntwk.copy()
    if renumber:
        ix = max(1, ntwk.s.shape[0] // 20)  # avoid DC-blocked first points
        if abs(ntwk.s[ix, 1, 0]) < abs(ntwk.s[ix, 2, 0]):
            ntwk.renumber((1, 2), (2, 1))
    s = ntwk.s
    m = np.zeros_like(s)
    # differential/common transform, scale 0.5 (PyBERT se2mm)
    m[:, 0, 0] = 0.5 * (s[:, 0, 0] - s[:, 0, 2] - s[:, 2, 0] + s[:, 2, 2])
    m[:, 0, 1] = 0.5 * (s[:, 0, 1] - s[:, 0, 3] - s[:, 2, 1] + s[:, 2, 3])
    m[:, 0, 2] = 0.5 * (s[:, 0, 0] + s[:, 0, 2] - s[:, 2, 0] - s[:, 2, 2])
    m[:, 0, 3] = 0.5 * (s[:, 0, 1] + s[:, 0, 3] - s[:, 2, 1] - s[:, 2, 3])
    m[:, 1, 0] = 0.5 * (s[:, 1, 0] - s[:, 1, 2] - s[:, 3, 0] + s[:, 3, 2])
    m[:, 1, 1] = 0.5 * (s[:, 1, 1] - s[:, 1, 3] - s[:, 3, 1] + s[:, 3, 3])
    m[:, 1, 2] = 0.5 * (s[:, 1, 0] + s[:, 1, 2] - s[:, 3, 0] - s[:, 3, 2])
    m[:, 1, 3] = 0.5 * (s[:, 1, 1] + s[:, 1, 3] - s[:, 3, 1] - s[:, 3, 3])
    m[:, 2, 0] = 0.5 * (s[:, 0, 0] - s[:, 0, 2] + s[:, 2, 0] - s[:, 2, 2])
    m[:, 2, 1] = 0.5 * (s[:, 0, 1] - s[:, 0, 3] + s[:, 2, 1] - s[:, 2, 3])
    m[:, 2, 2] = 0.5 * (s[:, 0, 0] + s[:, 0, 2] + s[:, 2, 0] + s[:, 2, 2])
    m[:, 2, 3] = 0.5 * (s[:, 0, 1] + s[:, 0, 3] + s[:, 2, 1] + s[:, 2, 3])
    m[:, 3, 0] = 0.5 * (s[:, 1, 0] - s[:, 1, 2] + s[:, 3, 0] - s[:, 3, 2])
    m[:, 3, 1] = 0.5 * (s[:, 1, 1] - s[:, 1, 3] + s[:, 3, 1] - s[:, 3, 3])
    m[:, 3, 2] = 0.5 * (s[:, 1, 0] + s[:, 1, 2] + s[:, 3, 0] + s[:, 3, 2])
    m[:, 3, 3] = 0.5 * (s[:, 1, 1] + s[:, 1, 3] + s[:, 3, 1] + s[:, 3, 3])
    z = np.zeros((len(ntwk.f), 4), dtype=complex)
    z[:, 0] = ntwk.z0[:, 0] + ntwk.z0[:, 2]        # differential: 2*z0
    z[:, 1] = ntwk.z0[:, 1] + ntwk.z0[:, 3]
    z[:, 2] = (ntwk.z0[:, 0] + ntwk.z0[:, 2]) / 2  # common: z0/2
    z[:, 3] = (ntwk.z0[:, 1] + ntwk.z0[:, 3]) / 2
    return rf.Network(frequency=ntwk.frequency, s=m, z0=z)


def sdd_2port(ntwk: rf.Network, renumber: bool = False) -> rf.Network:
    """4-port single-ended -> differential (DD) 2-port network."""
    mm = se2mm(ntwk, renumber=renumber)
    return rf.Network(frequency=ntwk.frequency, s=mm.s[:, 0:2, 0:2], z0=mm.z0[:, 0:2])


# ---------------------------------------------------------------------------
# import / interpolation
# ---------------------------------------------------------------------------


def import_diff_network(path: str, renumber: bool = True, lane: int = 0) -> rf.Network:
    """Read a 1/2/4/8/12-port Touchstone file, return a differential 2-port.

    4-port files are assumed single-ended; 8/12-port files hold N/4 lanes with
    ports (+A, +B, -A, -B) per lane (PyBERT convention).
    """
    ntwk = rf.Network(path, f_unit="Hz")
    (_, rows, cols) = ntwk.s.shape
    if rows != cols:
        raise ValueError("non-square Touchstone S-matrix")
    if rows in (8, 12):
        n_lanes = rows // 4
        if not 0 <= lane < n_lanes:
            raise ValueError(f"lane={lane} out of range for {rows}-port file")
        ports = [4 * lane, 4 * lane + 2, 4 * lane + 1, 4 * lane + 3]
        return sdd_2port(ntwk.subnetwork(ports), renumber=renumber)
    if rows == 4:
        return sdd_2port(ntwk, renumber=renumber)
    if rows == 2:
        return ntwk
    if rows == 1:
        from skrf.network import one_port_2_two_port

        return one_port_2_two_port(ntwk)
    raise ValueError(f"unsupported port count {rows} (need 1/2/4/8/12)")


def _cap_mag(z: np.ndarray, max_mag: float = 1.0) -> np.ndarray:
    out = z.copy()
    over = np.abs(out) > max_mag
    out[over] = np.array([rect(max_mag, phase(v)) for v in out[over]])
    return out


def interp_s2p(ntwk: rf.Network, f: np.ndarray) -> rf.Network:
    """Safely interpolate a 2-port network onto grid ``f`` (Hz).

    Conservative extrapolation (PyBERT): reflection terms extrapolated in polar
    coordinates with magnitude capped at 1; transmission terms filled with 0
    beyond the measured band (a channel doesn't come back at high f).
    """
    (_, rows, cols) = ntwk.s.shape
    if rows != cols or rows != 2:
        raise ValueError("interp_s2p() needs a 2-port network")
    extrap = ntwk.interpolate(f, fill_value="extrapolate", coords="polar", assume_sorted=True)
    s11 = _cap_mag(extrap.s[:, 0, 0])
    s22 = _cap_mag(extrap.s[:, 1, 1])
    s12 = ntwk.s12.interpolate(f, fill_value=0, bounds_error=False, coords="polar",
                               assume_sorted=True).s.flatten()
    s21 = ntwk.s21.interpolate(f, fill_value=0, bounds_error=False, coords="polar",
                               assume_sorted=True).s.flatten()
    s = np.empty((len(f), 2, 2), dtype=complex)
    s[:, 0, 0], s[:, 0, 1] = s11, s12
    s[:, 1, 0], s[:, 1, 1] = s21, s22
    return rf.Network(f=f, s=s, z0=extrap.z0, f_unit="Hz")


# ---------------------------------------------------------------------------
# termination -> voltage transfer function
# ---------------------------------------------------------------------------


def terminate_renormalize(ntwk2: rf.Network, zs: complex | np.ndarray,
                          zl: complex | np.ndarray) -> np.ndarray:
    """Voltage transfer function of a terminated 2-port (PyBERT method).

    Renormalizes port reference impedances to the (possibly complex,
    frequency-dependent) source/load impedances — generalized S-parameters —
    then H = S21 * sqrt(z0_load / z0_source). H is Vout/Vsource_available
    (includes the source divider), matching the serdespy formula's convention.
    """
    nf = len(ntwk2.f)
    z_new = np.empty((nf, 2), dtype=complex)
    z_new[:, 0] = zs
    z_new[:, 1] = zl
    n = ntwk2.copy()
    n.renormalize(z_new)
    return n.s[:, 1, 0] * np.sqrt(np.real(n.z0[:, 1]) / np.real(n.z0[:, 0])) / 2.0


def terminate_gamma(sdd: rf.Network, zs: complex, zl: complex) -> np.ndarray:
    """Voltage transfer via explicit reflection coefficients (serdespy method).

    H = SDD21 (1+GammaL)(1-GammaS) / [(1-SDD22 GammaL)(1-GammaIn GammaS)] / 2
    with GammaIn = SDD11 + SDD12*SDD21*GammaL / (1 - SDD22*GammaL).
    ``zs``/``zl`` are differential terminations; np.inf means open.
    Kept as an independent cross-check of ``terminate_renormalize``.
    """
    s = sdd.s
    z0 = sdd.z0[:, 0]
    gl = np.full(len(sdd.f), 1.0 + 0j) if np.isinf(zl) else (zl - z0) / (zl + z0)
    gs = np.full(len(sdd.f), 1.0 + 0j) if np.isinf(zs) else (zs - z0) / (zs + z0)
    g_in = s[:, 0, 0] + s[:, 0, 1] * s[:, 1, 0] * gl / (1.0 - s[:, 1, 1] * gl)
    H = (s[:, 1, 0] * (1.0 + gl) * (1.0 - gs)
         / (1.0 - s[:, 1, 1] * gl) / (1.0 - g_in * gs) / 2.0)
    return H
