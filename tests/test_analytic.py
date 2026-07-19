"""Analytic RLGC / ABCD channel tests against closed-form results."""

import numpy as np

from halo_serdes.channel.analytic import (
    abcd_to_transfer,
    cascade_abcd,
    series_z_abcd,
    shunt_y_abcd,
    tline_abcd,
)


def test_matched_lossless_line_is_allpass_half():
    """Lossless line matched at both ends: |H| = 0.5 exactly (source divider),
    phase = -omega * d * sqrt(LC)."""
    L, C = 2.5e-7, 1.0e-10          # z0 = 50 ohm
    z0 = np.sqrt(L / C)
    d = 0.1
    f = np.linspace(1e6, 50e9, 500)  # avoid f=0 division corner
    abcd = tline_abcd(0.0, L, 0.0, C, d, f)
    H = abcd_to_transfer(abcd, z0, z0)
    assert np.allclose(np.abs(H), 0.5, rtol=1e-9)
    tof = d * np.sqrt(L * C)
    expected_phase = -2 * np.pi * f * tof
    assert np.allclose(np.unwrap(np.angle(H)), expected_phase, rtol=1e-6, atol=1e-6)


def test_series_impedance_divider():
    """Series R between source Zs and load Zl: H = Zl/(Zs + R + Zl)."""
    f = np.linspace(0, 10e9, 11)
    R = 25.0
    abcd = series_z_abcd(np.full(f.size, R, dtype=complex))
    H = abcd_to_transfer(abcd, 50.0, 50.0)
    assert np.allclose(H, 50.0 / 125.0)


def test_shunt_rc_lowpass_pole():
    """Shunt C with Zs=Zl=50: pole at f = 1/(2 pi C (Zs||Zl))."""
    Cs = 1e-12
    f = np.linspace(0, 50e9, 5001)
    abcd = shunt_y_abcd(1j * 2 * np.pi * f * Cs)
    H = abcd_to_transfer(abcd, 50.0, 50.0)
    f3db_expected = 1.0 / (2 * np.pi * Cs * 25.0)
    mag = np.abs(H) / np.abs(H[0])
    f3db_measured = np.interp(1 / np.sqrt(2), mag[::-1], f[::-1])
    assert abs(f3db_measured - f3db_expected) / f3db_expected < 0.01


def test_cascade_two_dividers():
    f = np.linspace(0, 1e9, 3)
    r1 = series_z_abcd(np.full(f.size, 10.0, dtype=complex))
    r2 = series_z_abcd(np.full(f.size, 15.0, dtype=complex))
    both = cascade_abcd(r1, r2)
    H = abcd_to_transfer(both, 50.0, 50.0)
    assert np.allclose(H, 50.0 / 125.0)
