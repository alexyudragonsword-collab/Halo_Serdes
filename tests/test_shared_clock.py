"""Lanes on one sampling clock, run together (engine/shared_clock.py, ROADMAP P3 #9c)."""

import dataclasses
import warnings

import numpy as np
import pytest

from halo_serdes.analysis.skew import LaneCurve, lane_curve, shared_cdr
from halo_serdes.engine import run_time_link
from halo_serdes.engine.shared_clock import lane_symbols, run_shared_clock
from halo_serdes.engine.statistical import run_statistical


def _preset(name, noise, n_symbols):
    from halo_serdes_app import config_bridge as cb

    cfg = cb.load_preset(name)
    return dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=noise),
                               sim=dataclasses.replace(cfg.sim, n_symbols=n_symbols))


def test_one_lane_is_the_time_engine():
    """With one lane the shared loop is the kernel's own, moved from outside:
    the same sampling positions and decisions as ``run_time_link`` on the
    lane's symbols."""
    cfg = _preset("NRZ 16G mixed-signal", 0.018, 20_000)
    a = run_time_link(cfg, symbols=lane_symbols(cfg, 0))
    b = run_shared_clock(cfg, [0.0]).lanes[0]
    assert np.allclose(b.extras["phase_track"], a.extras["phase_track"], rtol=0, atol=1e-6)
    assert b.ber.n_errors == a.ber.n_errors and b.ber.n_checked == a.ber.n_checked


def test_lanes_move_together_at_their_skews():
    """Every lane's sampler takes the same step each symbol, so the offsets
    set at the start hold; the receiver clock is the master's on all of them,
    and their data is their own."""
    cfg = _preset("NRZ 16G mixed-signal", 0.018, 6_000)
    osr = cfg.osr
    for policy in ("master", "average"):
        r = run_shared_clock(cfg, [0.0, 0.25, -0.125], policy, master=0)
        p = [np.asarray(ln.extras["phase_track"], dtype=float) for ln in r.lanes]
        for pi, s in zip(p[1:], (0.25, -0.125)):
            d = pi - p[0]
            assert np.ptp(d) < 1e-6
            assert d[0] == pytest.approx(-s * osr, abs=1e-9)
        assert r.displacement.size == p[0].size
    assert not np.array_equal(lane_symbols(cfg, 0), lane_symbols(cfg, 1))


def test_shared_clock_rejects_bad_input():
    cfg = _preset("NRZ 16G mixed-signal", 0.018, 2_000)
    with pytest.raises(ValueError, match="policy"):
        run_shared_clock(cfg, [0.0, 0.1], "minmax")
    with pytest.raises(ValueError, match="master"):
        run_shared_clock(cfg, [0.0, 0.1], master=2)
    with pytest.raises(ValueError, match="UI apart"):
        run_shared_clock(cfg, [-0.6, 0.5])
    adc = _preset("PAM4 224G ADC (106 GBd)", 0.0015, 2_000)
    with pytest.raises(NotImplementedError):
        run_shared_clock(adc, [0.0])


def test_another_lanes_clock_has_no_pattern_phase_correlation():
    """On a clock its own data does not vote, the NRZ lane with a DFE reads
    the loop's own phase density (the chain's marginal) without the
    correlation between its data and that phase: its own-loop BER over the
    other-clock one is ``factor / factor_marginal`` (1.62 on the 16G preset
    at 15 mV). PAM4 has no factor."""
    cfg = _preset("NRZ 16G mixed-signal", 0.015, 60_000)
    own = run_statistical(cfg)
    other = run_statistical(cfg, phase_ui=0.0, own_clock=False)
    pp = other.extras["pattern_phase"]
    assert pp is own.extras["pattern_phase"]                  # one chain, cached
    assert 1.15 < pp.factor < 1.45 and 0.7 < pp.factor_marginal < 0.95
    assert own.ber / other.ber == pytest.approx(pp.factor / pp.factor_marginal, rel=1e-9)
    ms = _preset("PAM4 32G mixed-signal", 0.0085, 60_000)
    assert run_statistical(ms, phase_ui=0.0, own_clock=False).ber == run_statistical(ms).ber


def test_master_lane_reads_its_own_loop():
    """``shared_cdr("master")`` reads the master lane's ``own_ber``, every
    other lane its curve (``own_ber`` is None where there is no factor: PAM4,
    the ADC receiver)."""
    x = np.arange(16) / 16 - 0.5
    c = LaneCurve(x, 10.0 ** (-8.0 + 20.0 * np.abs(x)), own_ber=1.3e-8)
    m = shared_cdr(c, [0.0, 0.0, 0.1], "master")
    assert m.ber[0] == 1.3e-8
    assert m.ber[1] == pytest.approx(1e-8) and m.ber[2] == pytest.approx(1e-6, rel=1e-6)
    assert shared_cdr(c, [0.0, 0.0], "average").ber[0] == pytest.approx(1e-8)
    cfg = _preset("NRZ 16G mixed-signal", 0.015, 60_000)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lc = lane_curve(cfg, n_phases=2)
    assert lc.own_ber == pytest.approx(run_statistical(cfg).ber, rel=1e-12)
    assert lc.own_ber > 1.3 * float(lc.at(0.0))


@pytest.mark.needs_jit
def test_invariant3_lanes_on_the_masters_clock():
    """Lanes sampled by the master's loop at their skews against the
    statistical engine at those offsets on another lane's clock: NRZ 28G at
    22 mV, -3/8 ... +1/4 UI, 0.73-1.11 over six seeds of 100k symbols
    (cairn/多lane建模.md §9). The reference before this was a frozen lane fed
    a free-running loop's replayed wander (tests/test_skew.py)."""
    cfg = _preset("NRZ 28G analytic (COM/xtalk)", 0.022, 400_000)
    skews = (0.0, 0.375, -0.125, -0.25)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = run_shared_clock(cfg, skews, "master")
        for s, ln in zip(skews[1:], r.lanes[1:]):
            st = run_statistical(cfg, phase_ui=-s, own_clock=False).ber
            assert ln.ber.n_errors >= 30, (s, ln.ber.n_errors)
            assert 0.5 < st / ln.ber.ber < 2.0, (s, st, ln.ber.ber)


@pytest.mark.needs_jit
def test_one_loop_over_n_lanes_hunts_sqrt_n_less():
    """One loop averaging N lanes' detectors: each lane's data and noise are
    its own, so the detector noise the loop follows is 1/N, its hunting
    1/sqrt(N) -- 1.92 / 1.33 / 0.92 / 0.65 samples RMS on 1 / 2 / 4 / 8 lanes
    of NRZ 16G at 15 mV, the statistical engine's loop 1.02-1.05x of it
    (``run_statistical(voters=)``, cairn/多lane建模.md §9)."""
    cfg = _preset("NRZ 16G mixed-signal", 0.015, 200_000)
    osr = cfg.osr
    for n in (1, 4):
        r = run_shared_clock(cfg, [0.0] * n, "average")
        d = r.displacement[r.displacement.size // 2:]
        sig = run_statistical(cfg, voters=n).extras["jitter_sigma_ui"] * osr
        assert 0.9 < sig / d.std() < 1.15, (n, sig, d.std())
        if n == 1:
            one = d.std()
    assert 0.42 < d.std() / one < 0.55
