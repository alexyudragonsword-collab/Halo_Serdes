"""A profile clock's smear is a run's, read off a curve taken between samples.

ROADMAP P2 #28. Holding the DFE under the smear on a profile clock (as P2 #27
does on white ones) read example 31's ADC link 6.6-15x high against the time
engine, though its equalisers do not follow the wander either. Three things,
each measured against the time engine:

* the loop model's sigma was the steady state; a run of 200k symbols starts
  the loop from rest, and an underdamped loop (kp_shift 8-9, its error
  response peaking 8-16x) does not ring up within it -- the steady state read
  the 16-seed RMS 1.34-1.70x. Now the run's lines, from rest, over its window
  (``ErrorResponse.run_mean_sq``), and its error at points over the run, as
  it grows;
* the held curve was read linearly between whole samples, 1.6-3.6x high half
  a sample off the lock; it is now also computed between them;
* the profile clock now holds the taps like a white one.
"""

import dataclasses
import warnings

import numpy as np
import pytest

from halo_serdes.cdr.linear import ErrorResponse, LoopParams, error_response
from halo_serdes.channel import ChannelModel
from halo_serdes.config.schema import ClockConfig
from halo_serdes.engine import run_time_link
import halo_serdes.engine.statistical as st


def _loop(kp_shift, ki_shift=15, f_update=7e9):
    return LoopParams(f_update=f_update, kp_ui=2.0 ** -kp_shift, ki_ui=2.0 ** -ki_shift)


def _simulate(loop, k_pd, f, n_updates, phase):
    """The kernels' PI loop driven by cos(w n + phase), from rest: e_n per update."""
    w = 2 * np.pi * f / loop.f_update
    th = integ = 0.0
    err = np.empty(n_updates)
    for n in range(n_updates):
        err[n] = th - np.cos(w * n + phase)
        pd = -k_pd * err[n]
        integ += loop.ki_ui * pd
        th += loop.kp_ui * pd + integ
    return err


@pytest.mark.parametrize("kp_shift, f", [(5, 1.0e6), (9, 1.1e6), (9, 3.0e6), (9, 0.3e6)])
def test_run_mean_sq_is_the_loop_from_rest(kp_shift, f):
    """Closed form against the loop run on a sinusoid: the mean square over
    a window, averaged over the input's phase (two quadratures)."""
    loop, k = _loop(kp_shift), 0.0321
    er = ErrorResponse(loop, k)
    first, last = 125, 12_499
    sim = np.mean([np.mean(_simulate(loop, k, f, last + 1, ph)[first:] ** 2)
                   for ph in (0.0, np.pi / 2)])
    # a unit sinusoid has power 1/2 on each quadrature's mean
    assert float(er.run_mean_sq(np.array([f]), first, last)[0]) / 2.0 == pytest.approx(sim, rel=1e-6)
    at = er.run_sq_at(np.array([f]), np.array([first, last]))[0]
    sim_at = [np.mean([_simulate(loop, k, f, n + 1, ph)[n] ** 2 for ph in (0.0, np.pi / 2)])
              for n in (first, last)]
    assert at / 2.0 == pytest.approx(sim_at, rel=1e-6)


def test_a_damped_loop_forgets_its_start_and_a_ringing_one_does_not():
    """kp_shift 4 settles within the window: the run is within 10 % of the
    steady state (its first updates still settling).
    kp_shift 9 peaks 16x near 1.1 MHz; over the same window it has rung up
    to 4 % of that power."""
    f = np.array([1.1e6])
    for kp, lo, hi in ((4, 0.9, 1.0), (9, 0.02, 0.1)):
        er = ErrorResponse(_loop(kp), 0.0321)
        ratio = er.run_mean_sq(f, 125, 12_499)[0] / error_response(f, er.loop, er.k_pd)[0] ** 2
        assert lo < ratio < hi, (kp, ratio)
        # and, far beyond the ring-down, the steady state
        assert er.run_mean_sq(f, 10 ** 7, 10 ** 7 + 1000)[0] == pytest.approx(
            error_response(f, er.loop, er.k_pd)[0] ** 2, rel=1e-3)


def test_pulse_between_samples_is_exact_on_a_quadratic():
    y = 0.3 + 0.2 * np.arange(20.0) - 0.01 * np.arange(20.0) ** 2
    pos = np.array([3.0, 3.25, 7.5, 11.875])
    assert st._pulse_at(y, pos) == pytest.approx(0.3 + 0.2 * pos - 0.01 * pos ** 2, rel=1e-12)
    assert st._pulse_at(y, np.array([-3.0, 40.0])) == pytest.approx([0.0, 0.0])


def _ex31(kp_shift, noise, clock="SSPLL 200 fs"):
    from pathlib import Path

    src = (Path(__file__).parent.parent / "examples" / "31_pll_clock_profile.py").read_text()
    ns = {"__file__": str(Path(__file__).parent.parent / "examples" / "31_pll_clock_profile.py"),
          "__name__": "ex31_head"}
    exec(compile(src[: src.index("cm = ChannelModel.from_config(BASE)")], "ex31", "exec"), ns)
    base = ns["BASE"]
    return dataclasses.replace(
        base, tx=dataclasses.replace(base.tx, clock=ns["CLOCKS"][clock]),
        rx=dataclasses.replace(base.rx, noise_rms=noise,
                               cdr=dataclasses.replace(base.rx.cdr, kp_shift=kp_shift)))


@pytest.mark.parametrize("kp_shift, noise", [(5, 0.0028), (8, 0.0015)])
@pytest.mark.needs_jit      # 40 s a case without numba (2026-10-22)
def test_invariant3_on_a_profile_clock_with_the_taps_held(kp_shift, noise):
    """Example 31's link, SSPLL profile, against eight seeds of the time
    engine: over twelve seeds 0.74x at kp_shift 5 (2.8 mV) and 0.83x at 8
    (1.5 mV). Read linearly between samples with the run's sigma 1.6x at
    both; with the steady state too, 9.1x at 8; with the taps re-adapted under
    the smear (the read until now), 0.44x and 0.29x."""
    cfg = _ex31(kp_shift, noise)
    cm = ChannelModel.from_config(cfg)
    n_err = n_chk = 0
    for seed in range(1, 9):
        c = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, seed=seed))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t = run_time_link(c, channel=cm)
        n_err += t.ber.n_errors
        n_chk += t.ber.n_checked
    assert n_err >= 40, n_err
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat = st.run_statistical(cfg, channel=cm, ffe_taps=t.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = stat.ber / (n_err / n_chk)
    assert 0.5 < ratio < 2.0, (ratio, stat.ber, n_err)


def test_a_white_clock_has_no_run_profile():
    """Nothing rings up without a profile: one smear, as before."""
    cfg = _ex31(6, 0.0015)
    cfg = dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, clock=ClockConfig(rj_ui=0.02)))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = st.run_statistical(cfg)
    assert s.extras["clock_loop"] is None
