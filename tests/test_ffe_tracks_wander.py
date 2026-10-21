"""An ADC receiver's LMS FFE follows slow wander; the slicer holds its thresholds.

ROADMAP P2 #29. On example 31's ADC link the statistical engine read the
time engine 0.7-0.8x at the default LMS step and up to ~12x at mu 1e-2. Two
things, each measured against the time engine:

* under the smear the PAM4 slicer's thresholds stay where they were set, at
  the lock phase's main cursor, like the DFE (P2 #27) -- on a mixed-signal
  receiver at the pulse peak's, where its levels are set (P2 #30); re-placed at each
  phase's own main cursor, the curve read the frozen time engine 0.70-0.74x
  1-2 samples early of the lock;
* the LMS FFE moves its taps along their timing derivative and takes up part
  of a sampling offset itself, ``g = n_lanes * mu * lambda'`` per loop
  update: on the detector's input a second tracking path beside the loop
  (``LoopParams.ffe_track``). Without it the loop model read the CDR error
  1.3x high at mu 1e-2, kp_shift 9, and the BER ~12x.
"""

import dataclasses
import warnings
from pathlib import Path

import numpy as np
import pytest

from halo_serdes.analysis.cdr_tracking import cdr_tracking_error_s
from halo_serdes.cdr.linear import ErrorResponse, LoopParams, error_response
from halo_serdes.channel import ChannelModel
from halo_serdes.engine import run_time_link, timedomain
import halo_serdes.engine.statistical as st

_EX31 = Path(__file__).parent.parent / "examples" / "31_pll_clock_profile.py"


def _ex31():
    src = _EX31.read_text()
    ns = {"__file__": str(_EX31), "__name__": "ex31_head"}
    exec(compile(src[: src.index("cm = ChannelModel.from_config(BASE)")], "ex31", "exec"), ns)
    return ns["BASE"], ns["CLOCKS"]


def _with(base, clock=None, noise=None, kp_shift=None, mu=None, seed=None):
    rx = base.rx
    if noise is not None:
        rx = dataclasses.replace(rx, noise_rms=noise)
    if kp_shift is not None:
        rx = dataclasses.replace(rx, cdr=dataclasses.replace(rx.cdr, kp_shift=kp_shift))
    if mu is not None:
        rx = dataclasses.replace(rx, ffe=dataclasses.replace(rx.ffe, mu=mu))
    cfg = dataclasses.replace(base, rx=rx)
    if clock is not None:
        cfg = dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, clock=clock))
    if seed is not None:
        cfg = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, seed=seed))
    return cfg


def _simulate(loop, k_pd, f, n_updates, phase):
    """The kernels' PI loop (its latency included) driven by cos(w n + phase),
    from rest, with an FFE taking up ``c += g (e - c)`` of the error: the
    equalised error ``e - c`` per update."""
    w = 2 * np.pi * f / loop.f_update
    g, lat = loop.ffe_track, loop.latency_updates
    th = integ = c = 0.0
    pending = [0.0] * lat
    out = np.empty(n_updates)
    for n in range(n_updates):
        e = th - np.cos(w * n + phase)
        s = e - c
        pending.append(-k_pd * (s if loop.ffe_in_loop else e))
        pd = pending.pop(0)
        integ += loop.ki_ui * pd
        th += loop.kp_ui * pd + integ
        c += g * s
        out[n] = s
    return out


@pytest.mark.parametrize("in_loop", [True, False])
@pytest.mark.parametrize("kp_shift, lat, f", [(9, 0, 1.1e6), (6, 2, 3.0e6), (9, 1, 0.2e6)])
def test_the_ffe_path_against_the_loop_run_from_rest(in_loop, kp_shift, lat, f):
    """Closed form (``ErrorResponse`` on ``_polys``) against the loop run with
    the FFE's tracking beside it (on the detector's input) or after it (on
    raw samples): the window's mean square, over the input's phase."""
    loop = LoopParams(f_update=7e9, kp_ui=2.0 ** -kp_shift, ki_ui=2.0 ** -15,
                      latency_updates=lat, ffe_track=3e-4, ffe_in_loop=in_loop)
    k = 0.0321
    first, last = 125, 12_499
    sim = np.mean([np.mean(_simulate(loop, k, f, last + 1, ph)[first:] ** 2)
                   for ph in (0.0, np.pi / 2)])
    er = ErrorResponse(loop, k)
    # the closed form sums residue products that cancel (poles within 1e-3 of
    # each other and of 1): a few 1e-6 of the result
    assert float(er.run_mean_sq(np.array([f]), first, last)[0]) / 2.0 == pytest.approx(sim, rel=2e-5)
    # far beyond the ring-down, the steady state |E|^2
    assert er.run_mean_sq(np.array([f]), 10 ** 7, 10 ** 7 + 1000)[0] == pytest.approx(
        error_response(np.array([f]), loop, k)[0] ** 2, rel=1e-3)


def test_on_the_detector_input_the_ffe_is_more_proportional_gain():
    """Without latency, ``g (z - 1)`` added to the loop's denominator is
    ``k kp`` grown by ``g``; a vanishing ``g`` is the loop alone."""
    f = np.geomspace(1e3, 3.5e9, 300)
    k, g = 0.0321, 3e-4
    loop = LoopParams(f_update=7e9, kp_ui=2.0 ** -9, ki_ui=2.0 ** -15)
    with_ffe = dataclasses.replace(loop, ffe_track=g)
    more_kp = dataclasses.replace(loop, kp_ui=loop.kp_ui + g / k)
    assert error_response(f, with_ffe, k) == pytest.approx(error_response(f, more_kp, k), rel=1e-9)
    tiny = dataclasses.replace(loop, ffe_track=1e-14)
    assert error_response(f, tiny, k) == pytest.approx(error_response(f, loop, k), rel=1e-8)
    fr = np.array([1.1e6])
    assert ErrorResponse(tiny, k).run_mean_sq(fr, 125, 12_499) == pytest.approx(
        ErrorResponse(loop, k).run_mean_sq(fr, 125, 12_499), rel=1e-6)


def test_on_raw_samples_the_ffe_is_a_high_pass_after_the_loop():
    f = np.geomspace(1e3, 3.5e9, 300)
    k, g = 0.0321, 3e-4
    loop = LoopParams(f_update=7e9, kp_ui=2.0 ** -9, ki_ui=2.0 ** -15, latency_updates=1)
    after = dataclasses.replace(loop, ffe_track=g, ffe_in_loop=False)
    z = np.exp(2j * np.pi * f / loop.f_update)
    hp = np.abs((z - 1.0) / (z - 1.0 + g))
    assert error_response(f, after, k) == pytest.approx(error_response(f, loop, k) * hp, rel=1e-9)


def test_the_tracking_rate_is_the_lms_step_times_the_timing_direction():
    """Example 31's link: 2.4e-5 per update at mu 1e-3 (the time engine's CDR
    error fits 3e-5), in proportion to mu, and nothing without an LMS FFE."""
    base, clocks = _ex31()
    base = _with(base, clock=clocks["SSPLL 200 fs"])
    # the rate is the LMS's on the channel, not the taps': any taps put the FFE in
    ffe = base.rx.ffe
    taps = np.r_[np.zeros(ffe.n_pre), 1.0, np.zeros(ffe.n_post)]

    def track(cfg):
        return st.run_statistical(cfg, ffe_taps=taps, ffe_pre=ffe.n_pre).extras["clock_loop"].ffe_track

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g = [track(_with(base, mu=mu)) for mu in (1e-3, 1e-2)]
        g0 = track(dataclasses.replace(base, rx=dataclasses.replace(
            base.rx, ffe=dataclasses.replace(ffe, adapt="none"))))
    assert g[0] == pytest.approx(2.4e-5, rel=0.05)
    assert g[1] == pytest.approx(10.0 * g[0], rel=1e-9)
    assert g0 == 0.0


def test_invariant3_with_the_ffe_tracking_the_wander():
    """mu 1e-2, kp_shift 9, SSPLL profile, twelve seeds: the time engine's
    CDR error 0.505 samples RMS, the loop model with the FFE's path 0.496
    (0.67 without it); BER 1.14x the time engine (~12x without)."""
    base, clocks = _ex31()
    cfg = _with(base, clock=clocks["SSPLL 200 fs"], noise=0.0015, kp_shift=9, mu=1e-2)
    cm = ChannelModel.from_config(cfg)
    n_err = n_chk = 0
    sq = []
    for seed in range(1, 13):
        c = _with(cfg, seed=seed)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t = run_time_link(c, channel=cm)
        n_err += t.ber.n_errors
        n_chk += t.ber.n_checked
        sq.append(np.mean((cdr_tracking_error_s(c, t) / c.dt) ** 2))
    assert n_err >= 15, n_err
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat = st.run_statistical(cfg, channel=cm, ffe_taps=t.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    sol = stat.extras["clock_loop"]
    assert sol.ffe_track > 0
    assert sol.sigma_ui * cfg.osr == pytest.approx(np.sqrt(np.mean(sq)), rel=0.08)
    ratio = stat.ber / (n_err / n_chk)
    assert 0.5 < ratio < 2.0, (ratio, stat.ber, n_err)


def test_the_slicer_thresholds_are_held_with_the_taps(monkeypatch):
    """The held curve against the time engine frozen (loop, FFE and DFE) 1.5
    and 2 samples early of its lock, 2.8 mV: 1.05-1.10x over 1.2 M symbols;
    with the thresholds re-placed at each phase, 0.70x."""
    base, clocks = _ex31()
    cfg = dataclasses.replace(_with(base, noise=0.0028, seed=5),
                              sim=dataclasses.replace(base.sim, n_symbols=300_000, seed=5))
    cm = ChannelModel.from_config(cfg)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = run_time_link(cfg, channel=cm)
    ph = np.asarray(t.extras["phase_track"], dtype=np.float64)
    k = np.arange(ph.size)
    lock = float(np.mean((ph - k * cfg.osr)[ph.size // 2:])) - ph[0]
    frozen = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, cdr=dataclasses.replace(cfg.rx.cdr, kp_shift=60, ki_shift=60),
        ffe=dataclasses.replace(cfg.rx.ffe, adapt="none"),
        dfe=dataclasses.replace(cfg.rx.dfe, adapt="none")))

    def at(d):
        monkeypatch.setattr(timedomain, "rx_clock_offsets_samples",
                            lambda n, c_, rng, _c=lock + d: np.full(n, _c))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return run_time_link(frozen, channel=cm)

    r0 = at(0.0)
    held = []
    smeared = st._smeared_at

    def capture(curve, phi, center, sigma):
        held.append((np.array(curve), np.array(phi), center))
        return smeared(curve, phi, center, sigma)

    monkeypatch.setattr(st, "_smeared_at", capture)
    profile = _with(cfg, clock=clocks["SSPLL 200 fs"], kp_shift=6)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        st.run_statistical(profile, channel=cm, ffe_taps=r0.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    curve, phi, center = held[0]
    for d in (-2.0, -1.5):
        r = at(d)
        assert r.ber.n_errors >= 200, (d, r.ber.n_errors)
        ratio = float(np.interp(center + d, phi, curve)) / r.ber.ber
        assert 0.85 < ratio < 1.3, (d, ratio, r.ber.n_errors)


def test_mixed_signal_pam4_holds_its_thresholds_too(monkeypatch):
    """The PAM4 32G preset at 6 mV, the time engine frozen (loop and DFE) 6
    samples early and 3-4 late of its lock, its DFE taps and slicer levels
    those of the lock -- where the adapting receiver's levels end up (dLev,
    ROADMAP P2 #31) and where the statistical engine holds its thresholds
    since: the held curve within 0.85-1.3x over 2.4 M symbols. Until
    2026-10-21 the frozen levels stayed at the pulse peak's main cursor, as
    the time engine then set them, 1.02-1.20x (ROADMAP P2 #30); with the
    thresholds re-placed at each phase 0.42-0.79x (ROADMAP P2 #29)."""
    from halo_serdes.config import load_config

    base = load_config(str(Path(__file__).parent.parent / "configs" / "pam4_32g_ms.yaml"))
    cfg = dataclasses.replace(base, rx=dataclasses.replace(base.rx, noise_rms=0.006),
                              sim=dataclasses.replace(base.sim, n_symbols=400_000, seed=5))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = run_time_link(cfg)
    ph = np.asarray(t.extras["phase_track"], dtype=np.float64)
    k = np.arange(ph.size)
    lock = float(np.mean((ph - k * cfg.osr)[ph.size // 2:])) - ph[0]
    frozen = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, cdr=dataclasses.replace(cfg.rx.cdr, kp_shift=60, ki_shift=60),
        dfe=dataclasses.replace(cfg.rx.dfe, adapt="none")))
    # the time engine starts its DFE and levels at the pulse peak's cursors
    # and its sign-sign LMS moves both to the lock's, two samples after;
    # frozen, it is given the lock's cursors outright (taps and levels)
    cursors = timedomain.channel_cursors
    shift = int(round(lock))

    def lock_taps(pulse, osr, a, b, peak_idx=None):
        return cursors(pulse, osr, a, b, peak_idx=peak_idx + shift)

    monkeypatch.setattr(timedomain, "channel_cursors", lock_taps)
    held = []
    smeared = st._smeared_at

    def capture(curve, phi, center, sigma):
        held.append((np.array(curve), np.array(phi), center))
        return smeared(curve, phi, center, sigma)

    monkeypatch.setattr(st, "_smeared_at", capture)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        st.run_statistical(cfg)
    curve, phi, center = held[0]
    for d, n_seeds in ((-6.0, 3), (3.0, 2), (4.0, 1)):
        n_err = n_chk = 0
        for seed in range(1, n_seeds + 1):
            monkeypatch.setattr(timedomain, "rx_clock_offsets_samples",
                                lambda n, c_, rng, _c=lock + d: np.full(n, _c))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                r = run_time_link(dataclasses.replace(
                    frozen, sim=dataclasses.replace(frozen.sim, seed=seed)))
            n_err += r.ber.n_errors
            n_chk += r.ber.n_checked
        assert n_err >= 90, (d, n_err)
        ratio = float(np.interp(center + d, phi, curve)) / (n_err / n_chk)
        assert 0.85 < ratio < 1.3, (d, ratio, n_err)
