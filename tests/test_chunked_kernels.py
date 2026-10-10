"""The receiver kernels run in chunks of ``sim.chunk_symbols`` with the same
result as one call (ROADMAP P1 #3).

The kernels carry their whole loop state across calls, so chunking must not
change a single bit: whole run vs chunks vs one symbol at a time, on both
receiver architectures, on the JIT and the pure-Python path alike (CI runs
this file under both). ``progress(done, total)`` is called between chunks,
and an exception from it abandons the run -- that is the cancel path the app
layer uses.
"""

import dataclasses

import numpy as np
import pytest

from halo_serdes.cdr.adc_kernel import AdcRxRun, adc_rx
from halo_serdes.cdr.kernels import MsRxRun, ms_rx
from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig, PrConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig,
    MlsdConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link


def _ms_cfg(n_sym=12_000):
    return LinkConfig(
        modulation="nrz", symbol_rate=28e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.25, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=4096),
        tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.85, -0.05), fir_n_pre=1,
                    clock=ClockConfig(rj_ui=0.004)),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                    dfe=DfeConfig(n_taps=2, adapt="sign_sign", mu=1e-3), noise_rms=0.005),
        sim=SimConfig(n_symbols=n_sym, seed=6, pattern="prbs13"))


def _adc_cfg(n_sym=12_000):
    """PR target with LMS-adapted a: the most state the ADC kernel carries."""
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.20, rdc=5.0, r_skin=2.0e-3,
                              loss_tangent=0.012, n_freq=4096),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=14, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=1), mlsd=MlsdConfig(kind="viterbi", memory=2),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"),
        pr=PrConfig(target=(1.0, 0.75), adapt="lms"))


def _chunked(cfg, chunk):
    return dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, chunk_symbols=chunk))


def _same(a, b):
    assert a.ber.n_errors == b.ber.n_errors and a.ber.n_checked == b.ber.n_checked
    assert a.slicer_snr_db == b.slicer_snr_db
    for x, y in ((a.y_slicer, b.y_slicer), (a.ffe_taps, b.ffe_taps), (a.dfe_taps, b.dfe_taps)):
        if x is None:
            assert y is None
        else:
            assert np.array_equal(x, y)
    for k in ("phase_track", "decisions", "w_dfe_hist", "pd_hist", "q_hist_head", "lane_ser"):
        if k in a.extras:
            assert np.array_equal(np.asarray(a.extras[k]), np.asarray(b.extras[k])), k
    assert a.extras.get("pr_alpha") == b.extras.get("pr_alpha")
    assert a.extras.get("pr_target") == b.extras.get("pr_target")


def _adc4_cfg(n_sym=12_000):
    """A four-cursor target with LMS-adapted a, b and c (three controlled
    cursors' worth of decision history in the kernel's state)."""
    cfg = _adc_cfg(n_sym)
    return dataclasses.replace(
        cfg, pr=PrConfig(target=(1.0, 0.5, 0.0, 0.0), adapt="lms"),
        rx=dataclasses.replace(cfg.rx, mlsd=MlsdConfig(kind="viterbi", memory=1)))


@pytest.mark.parametrize("make", [_ms_cfg, _adc_cfg, _adc4_cfg],
                         ids=["mixed_signal", "adc_dsp", "adc_dsp_pr4"])
def test_chunking_does_not_change_a_bit(make):
    cfg = make()
    cm = ChannelModel.from_config(cfg)
    whole = run_time_link(_chunked(cfg, cfg.sim.n_symbols), channel=cm)
    for chunk in (997, 1):
        _same(whole, run_time_link(_chunked(cfg, chunk), channel=cm))


@pytest.mark.parametrize("make", [_ms_cfg, _adc_cfg], ids=["mixed_signal", "adc_dsp"])
def test_progress_reports_each_chunk_and_can_cancel(make):
    cfg = _chunked(make(), 2_000)
    cm = ChannelModel.from_config(cfg)
    seen = []
    run_time_link(cfg, channel=cm, progress=lambda done, total: seen.append((done, total)))
    dones = [d for d, _ in seen]
    total = seen[0][1]
    assert dones == sorted(dones) and len(set(dones)) == len(dones)
    assert dones[-1] == total or dones[-1] < total     # a run may stop short of its request
    assert len(seen) == -(-dones[-1] // 2_000)

    class Stop(Exception):
        pass

    calls = []

    def cancel_after_two(done, total):
        calls.append(done)
        if len(calls) == 2:
            raise Stop

    with pytest.raises(Stop):
        run_time_link(cfg, channel=cm, progress=cancel_after_two)
    assert calls == dones[:2]


def test_kernel_runs_out_of_waveform_mid_chunk_the_same_way():
    """Both kernels stop when the sampling position leaves the waveform; a
    chunk boundary must not move where (or change what came before)."""
    rng = np.random.default_rng(0)
    osr, n = 16, 3_000
    y = np.repeat(rng.choice([-1.0, 1.0], n), osr) + 0.02 * rng.standard_normal(n * osr)
    levels = np.array([-1.0, 1.0])
    ref = np.full(n + 200, -1, dtype=np.int64)
    off = np.zeros(n + 200)
    ms_args = (y, osr, 4.0 * osr, n + 200, levels, np.array([0.1, 0.02]), 1e-3, 64,
               0.02, 1e-4, 0.0, 1.0, ref, 0, 100, 0, np.zeros(2), off)
    whole = ms_rx(*ms_args)
    run = MsRxRun(*ms_args)
    while not run.finished:
        run.advance(run.done + 333)
    assert run.stopped and run.done < n + 200
    for a, b in zip(whole, run.result()):
        assert np.array_equal(a, b)

    m = n + 200
    adc_args = [y, osr, 4.0 * osr, m, levels, 4, np.zeros(4), np.ones(4), np.zeros(4),
                2.0 / 255, 127, np.zeros(m), np.r_[np.zeros(3), 1.0, np.zeros(4)], 3, 1e-4,
                np.zeros(1), 1e-4, 0.02, 1e-4, 0.0, 0.0, 1, 1, ref, 0, 100, off,
                0.0, 0, np.zeros(1), 0.0, np.zeros(2), 0.0, 1]
    a_whole = adc_rx(*adc_args)
    adc_args[31] = np.zeros(2)
    run = AdcRxRun(*adc_args)
    while not run.finished:
        run.advance(run.done + 333)
    assert run.stopped
    for a, b in zip(a_whole, run.result()):
        assert np.array_equal(a, b)
