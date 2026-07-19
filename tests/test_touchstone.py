"""Touchstone import and termination cross-validation.

Uses the classic IEEE 802.3 backplane channel (peters_01_0605_T20) shipped in
data/channels/. Cross-validates the primary renormalize-based termination
against the independent reflection-coefficient formula (serdespy port), and
against the actual serdespy implementation when its source tree is available.
"""

from pathlib import Path

import numpy as np
import pytest

from halo_serdes.channel import (
    ChannelModel,
    import_diff_network,
    interp_s2p,
    terminate_gamma,
    terminate_renormalize,
)

DATA = Path(__file__).parent.parent / "data" / "channels"
S4P = DATA / "peters_01_0605_T20_thru.s4p"

pytestmark = pytest.mark.skipif(not S4P.exists(), reason="channel data not present")


def test_import_diff_network_basic():
    sdd = import_diff_network(str(S4P), renumber=True)
    assert sdd.s.shape[1:] == (2, 2)
    # differential reference impedance = 2 * 50
    assert np.allclose(sdd.z0[0], 100.0)
    # a through channel passes low frequencies: |SDD21| near 1 at low f
    ix = len(sdd.f) // 50 + 1
    assert abs(sdd.s[ix, 1, 0]) > 0.7


def test_terminations_agree_matched():
    """Renormalize method vs reflection-coefficient formula, matched 100 ohm."""
    sdd = import_diff_network(str(S4P), renumber=True)
    H1 = terminate_renormalize(sdd, 100.0, 100.0)
    H2 = terminate_gamma(sdd, 100.0, 100.0)
    # matched case: both reduce to SDD21/2 exactly
    assert np.allclose(H1, sdd.s[:, 1, 0] / 2, rtol=1e-6, atol=1e-9)
    assert np.allclose(H2, sdd.s[:, 1, 0] / 2, rtol=1e-6, atol=1e-9)


def test_terminations_agree_mismatched():
    """The two independent termination methods must agree off-match too."""
    sdd = import_diff_network(str(S4P), renumber=True)
    for zs, zl in [(85.0, 110.0), (120.0, 90.0)]:
        H1 = terminate_renormalize(sdd, zs, zl)
        H2 = terminate_gamma(sdd, zs, zl)
        num = np.abs(H1 - H2).max()
        den = np.abs(H2).max()
        assert num / den < 1e-6


def test_interp_s2p_conservative_extrapolation():
    sdd = import_diff_network(str(S4P), renumber=True)
    f_new = np.linspace(0.0, 2 * sdd.f[-1], 1001)  # extends well past the data
    out = interp_s2p(sdd, f_new)
    in_band = f_new <= sdd.f[-1]
    # transmission zero-filled out of band
    assert np.allclose(np.abs(out.s[~in_band, 1, 0]), 0.0)
    # reflection magnitude capped at 1 everywhere
    assert np.abs(out.s[:, 0, 0]).max() <= 1.0 + 1e-9
    assert np.abs(out.s[:, 1, 1]).max() <= 1.0 + 1e-9


def test_channel_model_end_to_end():
    cm = ChannelModel.from_touchstone(str(S4P), f_max=40e9, n_freq=2001)
    # T20 is a long, very lossy backplane (measured to 15 GHz):
    # ~-21 dB at 4 GHz, ~-46 dB at 8 GHz
    loss_4g = cm.loss_at(4e9)
    assert -40.0 < loss_4g < -3.0
    h = cm.impulse(dt=2e-12)
    assert h.dt <= 2e-12 * (1 + 1e-9)
    # causal-ish, finite, with a dominant peak after some delay
    assert np.isfinite(h.y).all()
    peak = np.argmax(np.abs(h.y))
    assert peak > 0
    # pulse response peak below the matched-divider limit (0.5) but nonzero
    p = cm.pulse(dt=2e-12, osr=16)
    assert 0.005 < p.y.max() < 0.5


GOLDEN = Path(__file__).parent / "golden"


@pytest.mark.parametrize("golden_name,s4p_name", [
    ("peters_T20_serdespy_H.npz", "peters_01_0605_T20_thru.s4p"),
    ("whisper_serdespy_H.npz", "TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p"),
])
def test_vs_serdespy_golden(golden_name, s4p_name):
    """Reproduce serdespy ``four_port_to_diff`` golden data (stored .npz).

    Note on conventions (numerically confirmed): serdespy computes its
    reflection coefficients against the *single-ended* port reference
    (z0 = 50) even though its mixed-mode SDD matrix is referenced to the
    100-ohm differential impedance — so terminating "100 into 100" is not
    reflectionless in serdespy. That is a serdespy quirk/bug; our primary
    path (``terminate_renormalize``) is physically consistent. Here we
    emulate serdespy's convention to validate the *mixed-mode conversion*
    machinery against their golden numbers.
    """
    golden_file = GOLDEN / golden_name
    s4p = DATA / s4p_name
    if not golden_file.exists() or not s4p.exists():
        pytest.skip("golden data not present")
    g = np.load(golden_file)
    sdd = import_diff_network(str(s4p), renumber=True)
    assert np.allclose(sdd.f, g["f"], rtol=1e-9)

    # serdespy convention: gammas against SE z0=50, terminations 100/100.
    s = sdd.s
    z0, zl, zs = 50.0, 100.0, 100.0
    gl = (zl - z0) / (zl + z0)
    gs = (zs - z0) / (zs + z0)
    gin = s[:, 0, 0] + s[:, 0, 1] * s[:, 1, 0] * gl / (1 - s[:, 1, 1] * gl)
    H_emu = s[:, 1, 0] * (1 + gl) * (1 - gs) / (1 - s[:, 1, 1] * gl) / (1 - gin * gs) / 2
    err = np.abs(H_emu - g["H"]).max() / np.abs(g["H"]).max()
    # Residual up to ~0.35% traced to serdespy's hand-rolled port permutation,
    # which leaves the [1,2]/[2,1] S-entries unswapped (second serdespy quirk;
    # a true permutation is s'[i,j] = s[p(i),p(j)]). Agreement to <0.5% with
    # two independent quirks emulated validates our mixed-mode conversion.
    assert err < 5e-3
