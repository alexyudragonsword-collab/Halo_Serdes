"""White SJ and DCD in the statistical engine (2026-11-01).

Until then the statistical engine left both out (its approximation list
said so), and read 0.04-0.31x the time engine under 0.04-0.1 UI of SJ above
the loop bandwidth, 0.04-0.51x under DCD. A tone is left on the sampler at
``A |E(f)|`` by the loop the receiver has (``LoopSolution.sj_residual``) and
read as its arcsine on top of the smear; the bang-bang loop's gain and noise
take the swing. DCD moves the transmitter's own two edges by half of it with
their sign, the rest linearised, and an adapting receiver's levels follow the
offset (``stat_noise.dcd_absorbed``)."""

import dataclasses
import warnings

import numpy as np
import pytest

from halo_serdes.cdr.linear import LoopParams, error_response, mm_loop_solution
from halo_serdes.engine import stat_loop
from halo_serdes.engine.statistical import run_statistical
from halo_serdes_app.config_bridge import load_preset


def _with(cfg, side="tx", **clock):
    blk = getattr(cfg, side)
    return dataclasses.replace(cfg, **{side: dataclasses.replace(
        blk, clock=dataclasses.replace(blk.clock, **clock))})


def test_a_tone_is_read_as_its_arcsine():
    d, w, rest = stat_loop.sj_phase_offsets(((0.1, 1e9),), osr=16)
    assert w.sum() == pytest.approx(1.0) and rest == 0.0
    assert np.sum(w * d) == pytest.approx(0.0, abs=1e-12)
    assert np.sqrt(np.sum(w * d * d)) == pytest.approx(0.1 * 16 / np.sqrt(2.0), rel=1e-9)
    assert np.max(np.abs(d)) <= 0.1 * 16
    d2, w2, rest2 = stat_loop.sj_phase_offsets(((0.1, 1e9), (0.05, 3e8), (0.02, 1e7)), osr=16)
    assert d2.size == d.size ** 2 and rest2 == pytest.approx(0.02 / np.sqrt(2.0))
    assert stat_loop.sj_phase_offsets((), osr=16)[0].size == 1


def test_a_linear_loop_leaves_each_tone_at_its_error_response():
    lp = LoopParams(f_update=1e9, kp_ui=0.01, ki_ui=1e-4)
    sol = mm_loop_solution(lp, 1.0, 1e-4, lambda e: 0.0, tones=((0.2, 1e5), (0.2, 4e8)))
    (lo, f_lo), (hi, f_hi) = sol.sj_residual
    assert (f_lo, f_hi) == (1e5, 4e8)
    assert lo == pytest.approx(0.2 * error_response(np.array([1e5]), lp, 1.0)[0])
    assert lo < 0.01 and hi > 0.15                     # tracked below the loop, left above it


def test_both_clocks_go_through_the_same_loop():
    """The loop tracks the difference of the two clocks: a tone on either
    side is left the same."""
    cfg = load_preset("NRZ 16G mixed-signal")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tx = run_statistical(_with(cfg, "tx", sj_ui=0.05, sj_freq=3e8))
        rx = run_statistical(_with(cfg, "rx", sj_ui=0.05, sj_freq=3e8))
    assert tx.extras["sj_residual_ui"][0][0] == pytest.approx(rx.extras["sj_residual_ui"][0][0], rel=1e-6)
    assert tx.ber == pytest.approx(rx.ber, rel=1e-6)


def test_a_tone_costs_more_above_the_loop_than_below_it():
    cfg = load_preset("NRZ 16G mixed-signal")
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=0.012))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base = run_statistical(cfg).ber
        slow = run_statistical(_with(cfg, sj_ui=0.1, sj_freq=2e7))
        fast = run_statistical(_with(cfg, sj_ui=0.1, sj_freq=1e9))
        bigger = run_statistical(_with(cfg, sj_ui=0.12, sj_freq=1e9)).ber
    assert slow.extras["sj_residual_ui"][0][0] < 0.02 < 0.09 < fast.extras["sj_residual_ui"][0][0]
    assert base < slow.ber < fast.ber < bigger
    # the swing flattens the detector: the loop's gain falls
    assert fast.extras["clock_loop"].k_pd < slow.extras["clock_loop"].k_pd


def test_a_tone_the_loop_cannot_follow_warns():
    """NRZ 16G at 12 mV: 0.3 UI at 50 MHz has the loop follow it at 0.41 of
    its slew (the kernel slipped, 2 UI RMS); 0.1 UI at 300 MHz at 0.35 (it
    held, its residual tone 0.0947 against the model's 0.0944)."""
    cfg = load_preset("NRZ 16G mixed-signal")
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=0.012))
    with pytest.warns(UserWarning, match="SJ tone at 50.0 MHz"):
        run_statistical(_with(cfg, sj_ui=0.3, sj_freq=5e7))
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        run_statistical(_with(cfg, sj_ui=0.1, sj_freq=3e8))
    assert not [r for r in rec if "SJ tone" in str(r.message) or "RMS of phase" in str(r.message)]


def test_dcd_alone_is_modelled_and_absent_changes_nothing():
    from halo_serdes.engine import stat_noise

    cfg = load_preset("PAM4 32G mixed-signal")
    seen = []
    orig = stat_noise.dcd_absorbed

    def grab(*a, **k):
        seen.append(orig(*a, **k))
        return seen[-1]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base = run_statistical(cfg).ber
        import halo_serdes.engine.statistical as st
        st.dcd_absorbed = grab
        try:
            with_dcd = run_statistical(_with(cfg, dcd_ui=0.08)).ber
        finally:
            st.dcd_absorbed = orig
    assert with_dcd > base
    jit = seen[len(seen) // 2]
    # the adapting levels move to their mean offset: all four down, by mV (time engine
    # at 6 mV: -4.65 / -3.19 / -3.33 / -5.22 mV; the model -4.75 / -3.72 / -4.08 / -5.84)
    assert jit.level_mean is not None and jit.level_mean.shape == (4,)
    assert np.all(jit.level_mean < 0.0) and np.all(jit.level_mean > -0.012)


@pytest.mark.needs_jit
def test_invariant3_adc_under_sj_and_dcd():
    """The 224G preset at 2.5 mV on independent symbols, the FFE the time
    engine adapted: SJ 0.05 UI at 50 MHz (above its 0.3 MHz loop, left
    whole) read 0.97x the time engine (0.52x with the tone left out), DCD
    0.06 UI 0.87x (0.23x) over two seeds of 200k symbols."""
    from halo_serdes.channel import ChannelModel
    from halo_serdes.engine import run_time_link

    b = load_preset("PAM4 224G ADC (106 GBd)")
    b = dataclasses.replace(b, rx=dataclasses.replace(b.rx, noise_rms=0.0025),
                            sim=dataclasses.replace(b.sim, n_symbols=150_000))
    cm = ChannelModel.from_config(b)
    for c in (_with(b, sj_ui=0.05, sj_freq=5e7), _with(b, dcd_ui=0.06)):
        sym = np.random.default_rng(1001).integers(0, 4, c.sim.n_symbols)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t = run_time_link(c, channel=cm, symbols=sym)
            st = run_statistical(c, channel=cm, ffe_taps=t.ffe_taps, ffe_pre=c.rx.ffe.n_pre).ber
        assert t.ber.n_errors > 150, t.ber.n_errors
        assert 0.6 < st / t.ber.ber < 1.6, (st, t.ber.ber)


def test_jtol_reads_the_statistical_engine_at_a_deep_threshold():
    """A threshold a simulation reaches only with ~1e8 symbols: 1e-6 on the
    NRZ 16G preset (2.3e-8 without SJ), the tolerance falling from inside
    the loop to above it."""
    from halo_serdes.analysis.jtol import jitter_tolerance

    cfg = load_preset("NRZ 16G mixed-signal")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        jt = jitter_tolerance(cfg, [2e7, 1e9], ber_threshold=1e-6, amp_lo=0.01, amp_hi=1.0,
                              iters=5, engine="statistical")
    assert jt.tol_ui[0] > jt.tol_ui[1] > 0.0
    with pytest.raises(ValueError, match="engine"):
        jitter_tolerance(cfg, [1e9], engine="kernel")
