"""Static skew budget of lanes on one sampling clock (analysis/skew.py, ROADMAP P3 #9c)."""

import dataclasses
import warnings

import numpy as np
import pytest

from halo_serdes.analysis.skew import POLICIES, LaneCurve, lane_curve, shared_cdr
from halo_serdes.engine import run_time_link, timedomain
from halo_serdes.engine.cascade import initial_ffe_taps
from halo_serdes.engine.statistical import run_statistical


def _preset(name, noise, n_symbols=60_000):
    from halo_serdes_app import config_bridge as cb

    cfg = cb.load_preset(name)
    return dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=noise),
                               sim=dataclasses.replace(cfg.sim, n_symbols=n_symbols))


def _vee(floor=-8.0, slope=20.0, centre=0.0, n=32):
    """A synthetic curve: log10 BER = floor + slope * |offset - centre| (cyclic)."""
    x = np.arange(n) / n - 0.5
    d = (x - centre + 0.5) % 1.0 - 0.5
    return LaneCurve(x, 10.0 ** (floor + slope * np.abs(d)))


# --------------------------------------------------------------- the curve itself

def test_curve_reads_between_points_and_wraps():
    c = _vee()
    assert float(c.at(0.0)) == pytest.approx(1e-8)
    # log-linear between grid points: the vee is exact there
    assert float(c.at(0.05)) == pytest.approx(1e-7, rel=1e-9)
    # half a UI on is the next symbol: one UI either way reads the same
    assert float(c.at(0.3)) == pytest.approx(float(c.at(-0.7)), rel=1e-12)
    assert float(c.at(0.45)) == pytest.approx(float(c.at(-0.55)), rel=1e-12)
    assert c.best_offset_ui == pytest.approx(0.0, abs=1e-3)


def test_window_and_budget():
    c = _vee(centre=-0.1)
    lo, hi = c.window(1e-6)
    assert lo == pytest.approx(-0.2, abs=2e-3) and hi == pytest.approx(0.0, abs=2e-3)
    assert c.budget_ui(1e-6) == pytest.approx(0.2, abs=4e-3)
    assert c.window(1e-9) is None and c.budget_ui(1e-9) == 0.0
    assert c.window(1.0) == pytest.approx((-0.5, 0.3), abs=2e-3)   # synthetic: BER > 1 beyond
    assert c.window(1e3) == (-np.inf, np.inf) and c.budget_ui(1e3) == 1.0
    # a window across the half-UI seam is found whole
    lo, hi = _vee(centre=0.45).window(1e-6)
    assert lo == pytest.approx(0.35, abs=2e-3) and hi == pytest.approx(0.55, abs=2e-3)


def test_shared_cdr_policies():
    c = _vee()
    skews = [0.0, 0.2]
    m = shared_cdr(c, skews, "master")
    assert m.phase_ui == 0.0
    assert np.allclose(m.offsets_ui, [0.0, -0.2])
    assert m.ber[0] == pytest.approx(1e-8) and m.ber[1] == pytest.approx(1e-4, rel=1e-6)
    m1 = shared_cdr(c, skews, "master", master=1)
    assert m1.phase_ui == 0.2 and m1.ber[1] == pytest.approx(1e-8)
    # between two lanes on the same curve, the minimax puts each 0.1 UI off
    mm = shared_cdr(c, skews, "minmax")
    assert mm.phase_ui == pytest.approx(0.1, abs=2e-3)
    assert mm.worst == pytest.approx(1e-6, rel=0.05)
    assert mm.worst < m.worst and mm.mean < m.mean
    # the average is taken on the circle: lanes at +-0.45 UI are 0.1 UI apart
    a = shared_cdr(c, [0.45, -0.45], "average")
    assert abs(abs(a.phase_ui) - 0.5) < 1e-9
    assert np.allclose(np.abs(a.offsets_ui), 0.05)
    assert shared_cdr(c, [0.0, 0.1, 0.2], "average").phase_ui == pytest.approx(0.1)
    # one curve per lane
    curves = [_vee(), _vee(centre=0.1)]
    r = shared_cdr(curves, [0.0, 0.0], "minmax")
    assert r.phase_ui == pytest.approx(0.05, abs=2e-3)
    assert set(POLICIES) == {"master", "average", "minmax"}


def test_shared_cdr_rejects_bad_input():
    c = _vee()
    with pytest.raises(ValueError, match="policy"):
        shared_cdr(c, [0.0], "median")
    with pytest.raises(ValueError, match="curves"):
        shared_cdr([c, c], [0.0, 0.1, 0.2])
    with pytest.raises(ValueError, match="master"):
        shared_cdr(c, [0.0, 0.1], "master", master=2)
    with pytest.raises(ValueError, match="skews_ui"):
        shared_cdr(c, [])


# ------------------------------------------------------ the statistical engine's side

def test_phase_ui_reads_the_bathtub_where_it_is_defined():
    """Without a smear on the phase axis (white clocks on an ADC receiver,
    its FFE given) the sampling point set from outside is the bathtub read at
    the lock point plus the offset, to rounding; at 0 it is the engine's own
    BER. With one -- a bang-bang loop hunts on any clock (ROADMAP P2 #27) --
    the read at the offset holds the DFE where it reads, which the bathtub,
    re-adapted at every phase, does not; at 0 it is still the engine's own."""
    from halo_serdes.channel import ChannelModel

    cfg = _preset("PAM4 224G ADC (106 GBd)", 0.0015)
    ch = ChannelModel.from_config(cfg)
    kw = dict(channel=ch, ffe_taps=initial_ffe_taps(cfg, ch), ffe_pre=cfg.rx.ffe.n_pre)
    s0 = run_statistical(cfg, **kw)
    assert s0.extras["jitter_sigma_ui"] == 0.0
    assert run_statistical(cfg, phase_ui=0.0, **kw).ber == pytest.approx(s0.ber, rel=1e-12)
    lock = s0.extras["lock_phase_ui"]
    for d in (-0.25, 0.1, 0.25):
        ref = np.interp((lock + d) * cfg.osr, np.asarray(s0.phi_ui) * cfg.osr, s0.ber_phi)
        assert run_statistical(cfg, phase_ui=d, **kw).ber == pytest.approx(ref, rel=1e-9)
    ms = _preset("PAM4 32G mixed-signal", 0.0085)
    m0 = run_statistical(ms)
    assert m0.extras["jitter_sigma_ui"] > 0.02
    assert run_statistical(ms, phase_ui=0.0).ber == pytest.approx(m0.ber, rel=1e-12)


def test_adc_lane_needs_its_ffe():
    cfg = _preset("PAM4 224G ADC (106 GBd)", 0.0015)
    with pytest.raises(ValueError, match="ffe_taps"):
        lane_curve(cfg)


def test_adc_lane_reads_far_higher_with_the_lock_points_ffe():
    """A lane's FFE re-solved where it samples against the one solved at the
    lock point: the same at the lock, far apart a quarter UI off."""
    from halo_serdes.channel import ChannelModel

    cfg = _preset("PAM4 224G ADC (106 GBd)", 0.0015)
    ch = ChannelModel.from_config(cfg)
    moved = lane_curve(cfg, ch, n_phases=8, ffe_taps=lambda at: initial_ffe_taps(cfg, ch, at_ui=at))
    held = lane_curve(cfg, ch, n_phases=8, ffe_taps=initial_ffe_taps(cfg, ch))
    assert moved.readapt and not held.readapt
    assert float(moved.at(0.0)) == pytest.approx(float(held.at(0.0)), rel=1e-9)
    # 4.5x / 14.6x (with the DFE engaged and its error propagation counted)
    assert float(held.at(0.25)) > 3 * float(moved.at(0.25))
    assert float(held.at(-0.25)) > 5 * float(moved.at(-0.25))


# ------------------------------------------------- invariant 3: against the time engine

def _time_at_offset(cfg, channel, offsets_ui, wander=False):
    """Time-domain BER of a lane sampled at its lock point plus each offset.

    The loop is frozen (gains 2^-60) and held there by a constant receiver
    clock offset; the slicer levels, DFE taps and (ADC) FFE start from the
    cursors where it samples. Started from the pulse peak's, as the engine
    does when its own loop pulls in, the PAM4 32G receiver read 1.5e-1 at
    -3/8 UI against 1.7e-4: the sign-sign LMS cannot move its DFE that far
    in a run, and its slicer levels are not adapted at all.

    ``wander``: on top of the offset, the hunting of the clock the lane
    samples on -- a free-running loop's on the same link, its second half,
    from another seed so that it is not correlated with the held lane's data.
    A lane on another lane's clock inherits that loop's wander, which the
    statistical engine reads in (ROADMAP P2 #27)."""
    osr = cfg.osr
    t = run_time_link(cfg, channel=channel)
    ph = np.asarray(t.extras["phase_track"], dtype=float)
    k = np.arange(ph.size)
    lock = float(np.mean((ph - k * osr)[ph.size // 2:])) - ph[0]   # from the start (the peak)
    dev = np.zeros(1)
    if wander:
        w = run_time_link(dataclasses.replace(cfg, sim=dataclasses.replace(
            cfg.sim, seed=cfg.sim.seed + 1000)), channel=channel)
        pw = np.asarray(w.extras["phase_track"], dtype=float)
        dev = (pw - np.arange(pw.size) * osr)[pw.size // 2:]
        dev = dev - dev.mean()
    frozen = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, cdr=dataclasses.replace(cfg.rx.cdr, kp_shift=60, ki_shift=60)))
    clk, cur = timedomain.rx_clock_offsets_samples, timedomain.channel_cursors
    out = []
    for d in offsets_ui:
        c = lock + d * osr
        sh = int(round(c))
        timedomain.rx_clock_offsets_samples = (
            lambda n, cfg_, rng, _c=c: _c + np.resize(dev, n))
        timedomain.channel_cursors = (lambda p, o, a, b, peak_idx=None, _s=sh:
                                      cur(p, o, a, b, peak_idx=peak_idx + _s))
        try:
            out.append(run_time_link(frozen, channel=channel))
        finally:
            timedomain.rx_clock_offsets_samples, timedomain.channel_cursors = clk, cur
        held = np.asarray(out[-1].extras["phase_track"], dtype=float)
        held = held - np.arange(held.size) * osr - np.resize(dev, held.size)
        assert np.ptp(held) < 1e-6        # the loop stood still
    return out


@pytest.mark.parametrize("name, noise, offsets", [
    ("PAM4 32G mixed-signal", 0.0085, (0.125, 0.25)),
    ("NRZ 28G analytic (COM/xtalk)", 0.022, (-0.375, 0.25)),
])
def test_invariant3_mixed_signal_lane_off_its_lock(name, noise, offsets):
    """The lane's curve against a time-domain lane held at the offset on a
    clock that hunts as a bang-bang loop on that link does; on these two links
    0.67-1.09 over -3/8 ... +1/4 UI on the offsets with 20 errors or more
    (cairn/多lane建模.md §8; 0.92-1.31 before the held curve was taken between
    samples, ROADMAP P2 #28). Until 2026-10-15 the statistical engine left the
    loop's hunting out on white clocks and the reference held the lane still,
    0.49-1.78 (ROADMAP P2 #27); the curve now reads 1.2-38x against a still
    lane, which is not what the receiver sees."""
    from halo_serdes.channel import ChannelModel

    cfg = _preset(name, noise, 100_000)
    ch = ChannelModel.from_config(cfg)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        curve = lane_curve(cfg, ch)
        runs = _time_at_offset(cfg, ch, offsets, wander=True)
    for d, t in zip(offsets, runs):
        assert t.ber.n_errors >= 30, (d, t.ber.n_errors)
        assert 0.5 < float(curve.at(d)) / t.ber.ber < 2.0, (d, float(curve.at(d)), t.ber.ber)


def test_invariant3_adc_lane_with_the_ffe_it_converged_to():
    """Given the FFE the time-domain lane converged to at the offset, the
    statistical engine sampling there reads its BER (cairn/多lane建模.md §8.2);
    so does the statistical-only stand-in, the time engine's own start re-solved
    at the offset (ROADMAP P2 #23: a copy that left the Tx FFE out read
    14-25x high)."""
    from halo_serdes.channel import ChannelModel

    cfg = _preset("PAM4 224G ADC (106 GBd)", 0.0025)
    ch = ChannelModel.from_config(cfg)
    offsets = (-0.25, 0.25)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        runs = _time_at_offset(cfg, ch, offsets)
        for d, t in zip(offsets, runs):
            assert t.ber.n_errors >= 100
            for taps in (t.ffe_taps, initial_ffe_taps(cfg, ch, at_ui=d)):
                s = run_statistical(cfg, channel=ch, ffe_taps=taps, ffe_pre=cfg.rx.ffe.n_pre,
                                    phase_ui=d)
                assert 0.5 < s.ber / t.ber.ber < 2.0, (d, s.ber, t.ber.ber)


@pytest.mark.parametrize("name", ["PAM4 224G ADC (106 GBd)", "PAM4 deep-LR ADC (FFE+DFE8)",
                                  "PAM4 100G/λ LPO (VCSEL + OM4)"])
def test_standin_is_the_time_engines_start(name):
    """``initial_ffe_taps`` is the FFE the time engine starts from, to the
    bit: the 224G preset has a Tx FFE, the LPO one an optical front end."""
    from halo_serdes.channel import ChannelModel

    cfg = _preset(name, 0.0015, 4_000)
    ch = ChannelModel.from_config(cfg)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = run_time_link(cfg, channel=ch)
    assert np.array_equal(initial_ffe_taps(cfg, ch), np.asarray(t.extras["w_ffe0"]))
