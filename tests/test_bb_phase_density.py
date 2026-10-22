"""The bang-bang sampler's phase is not Gaussian about the lock (ROADMAP P2 #32).

Its detector saturates -- every transition already voting one way 0.2 UI
out -- so the potential the loop's phase diffuses in turns linear and the
tails exponential, heavier than a Gaussian of the same RMS and asymmetric
where the pulse's two sides differ. On the NRZ 16G preset with white clocks
the kernel's late tail at 0.23 UI was 3-7x the Gaussian's and under its steep
bathtub the statistical BER read 0.25-0.84x the time engine's; smeared with
the stationary density of the detector's own mean curve it reads 0.42-0.91x
(18 / 15 / 12 mV with 0.06 / 0.04 / 0.03 UI of transmit RJ: 0.91 / 0.78 /
0.42x against 0.84 / 0.64 / 0.25x; PAM4 32G 1.02 -> 1.04-1.06x). The rest at
12 mV is the free-running loop's errors sitting on the data that drove it
late: 2.1x the held curve's rate at the same phase, 3.8x the frozen loop's
(five seeds, 66 errors), which no static picture holds.
"""
import dataclasses
import warnings

import numpy as np
import pytest

import halo_serdes.engine.statistical as st
from halo_serdes.cdr.linear import LoopParams, bb_stationary_density, loop_damping
from halo_serdes.channel import ChannelModel
from halo_serdes_app import config_bridge as cb

U = np.linspace(-16.0, 16.0, 1601)
DU = float(U[1] - U[0])


def _moments(p):
    mean = float(np.sum(p * U) * DU)
    return mean, float(np.sqrt(np.sum(p * (U - mean) ** 2) * DU))


def _tail(p, thr, late=True):
    return float(np.sum(p[U > thr] if late else p[U < -thr]) * DU)


def _gauss(sigma):
    g = np.exp(-0.5 * (U / sigma) ** 2)
    return g / (g.sum() * DU)


def _preset(noise, rj, n_symbols=200_000, **cdr):
    cfg = cb.load_preset("NRZ 16G mixed-signal")
    return dataclasses.replace(
        cfg, rx=dataclasses.replace(cfg.rx, noise_rms=noise,
                                    cdr=dataclasses.replace(cfg.rx.cdr, **cdr)),
        tx=dataclasses.replace(cfg.tx, clock=dataclasses.replace(cfg.tx.clock, rj_ui=rj)),
        sim=dataclasses.replace(cfg.sim, n_symbols=n_symbols))


def test_a_linear_detector_gives_the_gaussian():
    """A detector whose pull keeps growing in proportion: the Boltzmann
    density of a quadratic potential is the Gaussian, at the RMS asked for."""
    p = bb_stationary_density(U, -0.05 * U, 2.0)
    assert np.sum(p) * DU == pytest.approx(1.0, abs=1e-6)
    assert np.max(np.abs(p - _gauss(2.0))) < 1e-3 * _gauss(2.0).max()
    mean, rms = _moments(p)
    assert abs(mean) < 1e-6 and rms == pytest.approx(2.0, abs=2e-3)


def test_a_saturating_detector_keeps_the_rms_and_fattens_the_tails():
    """The same RMS (the linearised model's), the shape from the curve: once
    the detector saturates the tails are exponential -- several times the
    Gaussian's mass beyond 3 sigma, symmetric for a symmetric curve."""
    p = bb_stationary_density(U, -0.3 * np.tanh(U / 3.0), 2.0)
    mean, rms = _moments(p)
    assert abs(mean) < 1e-6 and rms == pytest.approx(2.0, abs=3e-3)
    assert _tail(p, 6.0) > 2.0 * _tail(_gauss(2.0), 6.0)
    assert _tail(p, 6.0) == pytest.approx(_tail(p, 6.0, late=False), rel=1e-6)


def test_the_softer_side_gets_the_heavier_tail():
    """A pull that saturates lower on the late side (the NRZ 16G preset's:
    -0.22 against +0.3 to +0.4) skews the density late: the mean moves that
    way and the late tail outweighs the early one."""
    m = np.where(U < 0.0, 0.4 * np.tanh(-U / 3.0), -0.25 * np.tanh(U / 3.0))
    p = bb_stationary_density(U, m, 2.0)
    mean, rms = _moments(p)
    assert rms == pytest.approx(2.0, abs=3e-3)
    assert mean > 0.05
    assert _tail(p, 6.0) > 1.5 * _tail(p, 6.0, late=False)


def test_the_density_ends_where_the_pull_reverses():
    """Beyond the point where the detector's pull reverses (half a UI out,
    where the edge sample becomes the next data sample) a loop slips; the
    stationary density is zero there and still normalised inside."""
    p = bb_stationary_density(U, -np.sin(2.0 * np.pi * U / 24.0), 3.0)
    assert np.sum(p) * DU == pytest.approx(1.0, abs=1e-6)
    assert p[np.abs(U) > 12.01].max() == 0.0 and p[np.abs(U) < 11.9].min() > 0.0


def test_without_a_restoring_slope_it_is_the_gaussian():
    """No pull at all: the Gaussian of the RMS asked for; RMS zero: a delta."""
    assert np.allclose(bb_stationary_density(U, np.zeros_like(U), 1.5), _gauss(1.5))
    p = bb_stationary_density(U, -0.05 * U, 0.0)
    assert p[np.argmin(np.abs(U))] * DU == pytest.approx(1.0) and np.count_nonzero(p) == 1


def test_the_detector_curve_has_the_linearised_gain_and_saturates_late_first():
    """The curve the density is built from, on the NRZ 16G preset: zero at
    the lock, the linearised gain's slope there (0.97x the fixed point's
    k_pd, both near the kernel's 1.92/UI), saturating 0.25 UI out -- lower on
    the late side, where a late clock's data sample slides down the pulse
    and its raw sign flips more often (-0.30 against +0.34 measured)."""
    cfg = _preset(0.018, 0.06)
    s = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg))
    x, m = s.extras["detector_curve"]
    sol = s.extras["clock_loop"]
    assert abs(np.interp(0.0, x, m)) < 0.01
    slope = -(np.interp(0.01, x, m) - np.interp(-0.01, x, m)) / 0.02
    assert 0.8 < slope / sol.k_pd < 1.2, (slope, sol.k_pd)
    late, early = -float(np.interp(0.25, x, m)), float(np.interp(-0.25, x, m))
    assert 0.2 < late < early < 0.5, (late, early)


def test_the_read_uses_the_density_whose_rms_is_the_models():
    """The density the read is taken with: normalised, the RMS of the
    linearised model (jitter_sigma_ui), its late tail beyond 0.23 UI several
    times the Gaussian's (3.9e-4 against 1.2e-4; the kernel's 6.6e-4) and
    above the early one. An ADC receiver, or a mixed-signal one with an FFE
    (the static engine), keeps the Gaussian smear: None here."""
    cfg = _preset(0.018, 0.06)
    s = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg))
    x, p = s.extras["sampling_density"]
    dx = float(x[1] - x[0])
    assert np.sum(p) * dx == pytest.approx(1.0, abs=1e-3)
    sigma = s.extras["jitter_sigma_ui"]
    mean = float(np.sum(p * x) * dx)
    assert float(np.sqrt(np.sum(p * (x - mean) ** 2) * dx)) == pytest.approx(sigma, rel=1e-2)
    late = float(np.sum(p[x > 7.5 / cfg.osr]) * dx)
    early = float(np.sum(p[x < -7.5 / cfg.osr]) * dx)
    from scipy.stats import norm
    gauss = float(norm.sf(7.5 / cfg.osr / sigma))
    assert late > 2.0 * gauss and late > 1.5 * early, (late, early, gauss)
    adc = cb.load_preset("PAM4 224G ADC (106 GBd)")
    assert st.run_statistical(adc).extras["sampling_density"] is None


def test_the_density_reads_more_than_the_gaussian_smear_on_a_steep_bathtub(monkeypatch):
    """Same curve, two smears: the density's heavier late tail reads the
    NRZ 16G preset 1.09-1.66x the Gaussian's (18 / 15 / 12 mV: 1.09 / 1.22 /
    1.66x), which is where the time engine is (0.84 -> 0.91x at 18 mV)."""
    cfg = _preset(0.015, 0.04)
    cm = ChannelModel.from_config(cfg)
    with_density = st.run_statistical(cfg, channel=cm).ber
    monkeypatch.setattr(st, "bb_stationary_density",
                        lambda u, m, sigma: np.exp(-0.5 * (u / sigma) ** 2)
                        / (np.exp(-0.5 * (u / sigma) ** 2).sum() * (u[1] - u[0])))
    gaussian = st.run_statistical(cfg, channel=cm).ber
    assert 1.1 < with_density / gaussian < 1.4, (with_density, gaussian)


def test_loop_damping_is_the_second_order_ratio():
    lp = LoopParams(f_update=16e9, kp_ui=2.0 ** -5, ki_ui=2.0 ** -12)
    assert loop_damping(lp, 1.9) == pytest.approx(2.0 ** -5 * np.sqrt(1.9 / 2.0 ** -12) / 2.0)
    assert loop_damping(LoopParams(f_update=16e9, kp_ui=2.0 ** -5, ki_ui=0.0), 1.9) is None
    assert loop_damping(lp, 0.0) is None


def test_an_underdamped_bang_bang_loop_warns():
    """kp_shift 8 on the NRZ 16G preset: damping 0.17 at the linearised gain,
    and the kernel slipped cycles (0.46 UI RMS of phase, 1.15 UI of drift
    over 200k symbols, BER 8.7e-4 against this engine's 3.9e-4); kp_shift 7,
    at 0.34, held (0.057 UI, 1.1x the model) and does not warn."""
    cfg = _preset(0.018, 0.06, kp_shift=8)
    with pytest.warns(UserWarning, match="damping ratio is 0.1"):
        s = st.run_statistical(cfg)
    assert s.extras["clock_loop"].damping < 0.25
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        s = st.run_statistical(_preset(0.018, 0.06, kp_shift=7))
    assert not [w for w in seen if "damping" in str(w.message)]
    assert 0.3 < s.extras["clock_loop"].damping < 0.4
