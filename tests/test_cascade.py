"""Retimed links (optical stage 2): ``symbols=`` hand-off and the segment cascade.

A retimer is a receiver whose decisions become the next transmitter's
symbols. The engines take that stream through ``symbols=``; the cascade runs
the segments in series and scores the host's decisions against the host's
own stream. Closed forms: the pattern's own stream reproduces the plain run
bit for bit, and independent segments combine as 1 - prod(1 - p_i).
"""

import dataclasses

import numpy as np
import pytest

from halo_serdes.analysis.metrics import ber_confidence
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    OpticalConfig, RxConfig, SimConfig, TopologyConfig, TxConfig,
)
from halo_serdes.engine import run_static_link, run_time_link
from halo_serdes.engine.cascade import (
    end_to_end_ber, run_cascade, run_cascade_statistical, segment_configs,
)
from halo_serdes.engine.static_link import check_symbols, make_pattern


def _same(a, b) -> bool:
    return (a.ber.n_errors == b.ber.n_errors and a.ber.n_checked == b.ber.n_checked
            and a.ser == b.ser and a.slicer_snr_db == b.slicer_snr_db
            and np.array_equal(a.y_slicer, b.y_slicer)
            and np.array_equal(a.extras["decisions"], b.extras["decisions"]))


_MS = LinkConfig(channel=ChannelConfig(kind="analytic", length_m=0.2, rdc=5.0, r_skin=2e-3),
                 rx=RxConfig(noise_rms=0.02), sim=SimConfig(n_symbols=20_000, seed=3))
_ADC = LinkConfig(modulation="pam4", symbol_rate=53.125e9, osr=16,
                  channel=ChannelConfig(kind="analytic", length_m=0.1, rdc=5.0, r_skin=2e-3),
                  rx=RxConfig(arch="adc_dsp", noise_rms=0.01, ffe=FfeConfig(n_pre=2, n_post=6),
                              adc=AdcConfig(fullscale=0.6)),
                  sim=SimConfig(n_symbols=20_000, seed=3, pattern="prbs13q"))


@pytest.mark.parametrize("cfg", [_MS, _ADC], ids=["mixed_signal", "adc_dsp"])
def test_symbols_of_the_pattern_itself_change_nothing(cfg):
    """``symbols=`` with the stream the pattern would generate is the same
    run, byte for byte, on both time-engine paths and the static link."""
    assert _same(run_time_link(cfg), run_time_link(cfg, symbols=make_pattern(cfg)))
    assert _same(run_static_link(cfg), run_static_link(cfg, symbols=make_pattern(cfg)))


def test_decisions_line_up_with_the_symbols_from_warmup_on():
    """The decisions a segment hands on are the user symbols from ``warmup``
    on -- exactly, on a noiseless link -- so the next segment's stream is
    aligned to the host's by the sum of the warm-ups."""
    cfg = dataclasses.replace(_MS, rx=dataclasses.replace(_MS.rx, noise_rms=0.0))
    res = run_time_link(cfg)
    sent = make_pattern(cfg)
    dec, warm = res.extras["decisions"], res.extras["warmup"]
    assert np.array_equal(dec, sent[warm: warm + dec.size])


def test_symbols_must_be_symbol_indices():
    """Invariant #5: a waveform handed in as symbols is refused, not sampled."""
    with pytest.raises(TypeError, match="integer"):
        check_symbols(_MS, np.zeros(10))
    with pytest.raises(ValueError, match=r"\[0, 2\)"):
        check_symbols(_MS, np.array([0, 1, 2]))
    with pytest.raises(ValueError, match=r"\[0, 4\)"):
        check_symbols(_ADC, np.array([0, 4]))
    with pytest.raises(ValueError, match="1-D"):
        check_symbols(_ADC, np.zeros((2, 3), dtype=np.int64))
    assert check_symbols(_ADC, np.array([0, 1, 2, 3], dtype=np.int8)).dtype == np.int64


# ------------------------------------------------------------- cascade ---

def _seg(loss_len=0.08):
    return ChannelConfig(kind="analytic", length_m=loss_len, rdc=5.0, r_skin=2e-3,
                         loss_tangent=0.012, n_freq=4096)


def _adc_rx(noise, fullscale=0.6):
    return RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=3.0),
                    adc=AdcConfig(n_bits=10, n_lanes=16, enob=None, fullscale=fullscale),
                    ffe=FfeConfig(n_pre=3, n_post=8, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=noise)


def _retimed(retimer="both", n_sym=120_000, er_db=4.5, oma_dbm=-6.0, host_noise=0.012,
             retimer_noise=0.012, mod="pam4", arch="adc_dsp"):
    if arch == "adc_dsp":
        host_rx, ret_rx = _adc_rx(host_noise), _adc_rx(retimer_noise)
    else:
        host_rx = RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=3.0),
                           dfe=DfeConfig(n_taps=2), noise_rms=host_noise)
        ret_rx = dataclasses.replace(host_rx, noise_rms=retimer_noise)
    return LinkConfig(
        modulation=mod, symbol_rate=53.125e9 if arch == "adc_dsp" else 26.5625e9, osr=16,
        tx=TxConfig(swing=1.0), rx=host_rx,
        sim=SimConfig(n_symbols=n_sym, seed=11, pattern="prbs13q" if mod == "pam4" else "prbs13"),
        topology=TopologyConfig(
            seg_a=_seg(), seg_b=_seg(), retimer=retimer, retimer_rx=ret_rx,
            retimer_tx=TxConfig(swing=1.0),
            optical=OpticalConfig(kind="vcsel_mmf", f_r_hz=22e9, damping_hz=30e9, er_db=er_db,
                                  oma_dbm=oma_dbm, rin_db_hz=-140.0, length_m=100.0,
                                  modal_bw_mhz_km=4700.0, responsivity_a_w=0.7,
                                  tia_bw_hz=40e9, tia_noise_pa_sqrthz=12.0)))


def test_no_retimer_is_the_plain_link():
    cfg = _retimed(retimer="none", n_sym=40_000)
    r = run_cascade(cfg)
    plain = run_time_link(cfg)
    assert len(r.segments) == 1 and r.segments[0].name == "link"
    assert r.ber.n_errors == plain.ber.n_errors and r.ber.n_checked == plain.ber.n_checked
    assert r.ber_product == pytest.approx(plain.ber.ber)


def test_segments_are_cut_where_the_retimers_sit():
    cfg = _retimed()
    segs = dict(segment_configs(cfg))
    assert list(segs) == ["host->retimer (seg A)", "optics", "retimer->host (seg B)"]
    a, o, b = segs.values()
    # segment A: host TX, segment A trace, retimer RX, no optics
    assert a.topology is None and a.channel == cfg.topology.seg_a
    assert a.tx == cfg.tx and a.rx == cfg.topology.retimer_rx
    # optics: retimer TX and RX around the optical blocks on ideal traces
    assert o.topology is not None and o.topology.optical == cfg.topology.optical
    assert o.topology.retimer == "none"
    assert o.topology.seg_a.length_m == 0.0 and o.topology.seg_b.length_m == 0.0
    assert o.tx == cfg.topology.retimer_tx and o.rx == cfg.topology.retimer_rx
    # segment B: retimer TX, segment B trace, host RX
    assert b.topology is None and b.channel == cfg.topology.seg_b and b.rx == cfg.rx
    # independent noise per segment
    assert len({a.sim.seed, o.sim.seed, b.sim.seed}) == 3


@pytest.mark.parametrize("arch,mod", [("adc_dsp", "pam4"), ("mixed_signal", "nrz")])
def test_end_to_end_ber_is_the_product_of_the_segments(arch, mod):
    """Three segments with their own errors: the host sees 1 - prod(1 - p_i),
    within the Wilson interval of its own count (invariant #2: both
    receiver architectures run the cascade)."""
    # receiver noise sized so the two electrical segments err at ~1e-3 and the
    # optics (the same retimer RX on half the amplitude) at ~1e-2
    # (the mixed-signal pair was 0.10 / -13 dBm until the time engine's noise
    # stopped thinning out between samples; that left every segment ~10x past
    # this operating point, where segment errors stop being independent)
    noise, oma = (0.02, -3.0) if arch == "adc_dsp" else (0.065, -10.0)
    cfg = _retimed(arch=arch, mod=mod, host_noise=noise, retimer_noise=noise,
                   oma_dbm=oma, n_sym=150_000)
    r = run_cascade(cfg)
    assert len(r.segments) == 3
    assert all(s.result.ber.n_errors > 20 for s in r.segments), r.summary()
    assert r.ber.n_errors > 100, r.summary()
    lo, hi = ber_confidence(r.ber.n_errors, r.ber.n_checked, z=3.0)
    assert lo <= r.ber_product <= hi, r.summary()
    # the chain is never better than its worst segment
    assert r.ber.ber >= 0.8 * max(r.segment_bers)
    # the hand-off is aligned: offsets accumulate the warm-ups
    assert [s.offset for s in r.segments] == list(np.cumsum(
        [s.result.extras["warmup"] for s in r.segments]))


def test_retimed_optical_segment_is_cleaner_than_the_whole_lpo_link():
    """The thesis of retiming: the optics no longer carry the segments' ISI,
    so the optical segment alone does better than the same optics behind the
    same two traces in one unretimed link (direction only, by design)."""
    lpo = _retimed(retimer="none", n_sym=120_000, host_noise=1e-4)
    ret = _retimed(retimer="both", n_sym=120_000, host_noise=1e-4, retimer_noise=1e-4)
    r_lpo = run_cascade(lpo)
    r_ret = run_cascade(ret)
    optics = next(s for s in r_ret.segments if s.name == "optics")
    assert optics.result.ber.ber < r_lpo.ber.ber
    assert r_ret.ber.ber < r_lpo.ber.ber


def test_statistical_cascade_matches_the_time_engine_within_2x():
    """Invariant #3 on the cascade: the product of the statistical per-segment
    BERs against the measured end-to-end count."""
    cfg = _retimed(n_sym=150_000)
    r = run_cascade(cfg, statistical=True)
    assert r.ber.n_errors > 100, r.summary()
    assert r.stat_ber is not None
    ratio = r.stat_ber / r.ber.ber
    assert 0.5 < ratio < 2.0, (ratio, r.summary(), [s.stat_ber for s in r.segments])
    # the statistical-only cascade (MMSE starting FFE, no time run) lands on the
    # same order of magnitude
    rs = run_cascade_statistical(cfg)
    assert 0.2 < rs.stat_ber / r.ber.ber < 5.0, (rs.stat_ber, r.ber.ber)


def test_end_to_end_scoring_counts_bits_like_the_engines():
    cfg_p = _ADC
    sent = np.array([0, 1, 2, 3, 0, 1, 2, 3])
    got = np.array([0, 1, 2, 3, 3, 1, 2, 3])          # one 0->3 jump: 2 Gray bits
    b = end_to_end_ber(cfg_p, sent, got)
    assert (b.n_checked, b.n_errors) == (16, 2)
    b = end_to_end_ber(_MS, np.array([0, 1, 1, 0]), np.array([0, 1, 0, 0]))
    assert (b.n_checked, b.n_errors) == (4, 1)
