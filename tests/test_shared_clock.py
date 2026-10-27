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
    with pytest.raises(ValueError, match="one receiver architecture"):
        run_shared_clock([cfg, adc], [0.0, 0.1])
    fixed = dataclasses.replace(adc, numeric=dataclasses.replace(adc.numeric, mode="fixed"))
    with pytest.raises(NotImplementedError, match="float receiver"):
        run_shared_clock(fixed, [0.0])


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


def test_a_shared_tx_clock_moves_every_lanes_edges_with_the_leads():
    """``shared_tx_clock``: the lead lane's run is unchanged, and the others'
    transmitters take its clock (RJ here) in place of their own draws, keeping
    their own DCD and their generators' later numbers."""
    from halo_serdes.analysis.cdr_tracking import tx_edge_offsets_s
    from halo_serdes.engine.shared_clock import _tx_clock_s
    from halo_serdes.tx.jitter import edge_jitter_seq

    cfg = _preset("NRZ 16G mixed-signal", 0.018, 8_000)
    cfg = dataclasses.replace(cfg, tx=dataclasses.replace(
        cfg.tx, clock=dataclasses.replace(cfg.tx.clock, rj_ui=0.05, dcd_ui=0.0)))
    clk = _tx_clock_s(cfg, lane_symbols(cfg, 0))
    assert np.array_equal(clk, tx_edge_offsets_s(cfg))
    own = run_shared_clock(cfg, [0.0, 0.1], "master")
    shared = run_shared_clock(cfg, [0.0, 0.1], "master", shared_tx_clock=True)
    a, b = own.lanes, shared.lanes
    assert np.array_equal(a[0].extras["phase_track"], b[0].extras["phase_track"])
    assert np.array_equal(a[0].y_slicer, b[0].y_slicer)
    assert not np.array_equal(a[1].y_slicer, b[1].y_slicer)
    # the edge sequence: the given clock plus the lane's own DCD, the draws made
    dcd = dataclasses.replace(cfg, tx=dataclasses.replace(
        cfg.tx, clock=dataclasses.replace(cfg.tx.clock, dcd_ui=0.1)))
    v = np.where(lane_symbols(cfg, 1) > 0, 1.0, -1.0)
    r1, r2 = np.random.default_rng(3), np.random.default_rng(3)
    j_own = edge_jitter_seq(v.size, dcd, r1, v)
    j_sh = edge_jitter_seq(v.size, dcd, r2, v, clock_s=clk)
    assert r1.random() == r2.random()
    rj_own = edge_jitter_seq(v.size, cfg, np.random.default_rng(3), v)
    assert np.allclose(j_sh - clk, j_own - rj_own, rtol=0, atol=1e-18)


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


# ------------------------------------------------------- the ADC receiver (P3 #9d)

def test_one_adc_lane_is_the_time_engine():
    """The Mueller-Muller detector read back from the lane's samples, summed
    over the interleaver's block and taken through the kernel's loop and
    latency queue, is the kernel's own loop: the same decisions and FFE, the
    sampler to rounding (the kernel adds the step to the period first)."""
    cfg = _preset("PAM4 224G ADC (106 GBd)", 0.0025, 20_000)
    a = run_time_link(cfg, symbols=lane_symbols(cfg, 0))
    b = run_shared_clock(cfg, [0.0]).lanes[0]
    assert np.allclose(b.extras["phase_track"], a.extras["phase_track"], rtol=0, atol=1e-6)
    assert b.ber.n_errors == a.ber.n_errors and b.ber.n_checked == a.ber.n_checked
    assert np.array_equal(b.ffe_taps, a.ffe_taps)


def test_an_averaging_loop_over_adc_lanes_has_no_pull_to_their_mean_skew():
    """On the PAM4 ADC presets the detector reads the equalised samples, and
    every lane's FFE re-adapts where it samples: the averaged detector does
    not see the lanes' offsets, so the clock stays where it starts -- lane
    0's lock here -- not at the mean skew. ``lane_curve`` marks the curve
    (``pd_sees_offset``) and ``shared_cdr("average")`` places the clock so."""
    from halo_serdes.channel import ChannelModel
    from halo_serdes.engine.cascade import initial_ffe_taps

    cfg = _preset("PAM4 224G ADC (106 GBd)", 0.0025, 30_000)
    skews = [0.0, 0.125, -0.125, 0.25]
    r = run_shared_clock(cfg, skews, "average")
    solo = run_shared_clock(cfg, [0.0])
    tail = slice(r.displacement.size // 2, None)
    # the mean skew is 2 samples; the clock is where the lane alone would be
    assert abs(r.displacement[tail].mean() - solo.displacement[tail].mean()) < 0.5
    ch = ChannelModel.from_config(cfg)
    c = lane_curve(cfg, ch, n_phases=2, ffe_taps=lambda at: initial_ffe_taps(cfg, ch, at_ui=at))
    assert not c.pd_sees_offset
    assert shared_cdr(dataclasses.replace(c, voters=4), skews, "average").phase_ui == 0.0
    raw = dataclasses.replace(cfg, rx=dataclasses.replace(
        cfg.rx, cdr=dataclasses.replace(cfg.rx.cdr, pd_input="adc")))
    assert lane_curve(raw, ch, n_phases=2,
                      ffe_taps=lambda at: initial_ffe_taps(raw, ch, at_ui=at)).pd_sees_offset


@pytest.mark.needs_jit
def test_invariant3_adc_lanes_on_the_masters_clock():
    """ADC lanes on the master's clock at their skews against the statistical
    engine at those offsets with the FFE solved there: the 224G preset at
    2.5 mV, -1/4 ... +1/4 UI, 0.90-1.02 over three seeds of 200k symbols
    (cairn/多lane建模.md §9.4)."""
    from halo_serdes.channel import ChannelModel
    from halo_serdes.engine.cascade import initial_ffe_taps

    cfg = _preset("PAM4 224G ADC (106 GBd)", 0.0025, 120_000)
    ch = ChannelModel.from_config(cfg)
    skews = (0.0, 0.125, -0.25)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = run_shared_clock(cfg, skews, "master", channel=ch)
        for s, ln in zip(skews, r.lanes):
            st = run_statistical(cfg, channel=ch, phase_ui=-s, ffe_pre=cfg.rx.ffe.n_pre,
                                 ffe_taps=initial_ffe_taps(cfg, ch, at_ui=-s)).ber
            assert ln.ber.n_errors >= 30, (s, ln.ber.n_errors)
            assert 0.5 < st / ln.ber.ber < 2.0, (s, st, ln.ber.ber)
