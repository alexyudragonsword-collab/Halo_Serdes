"""Streaming time engine (``sim.stream``, ROADMAP P1 #2).

The waveform is produced block by block and the receiver kernels read a
window sliding along it. Three properties hold it together:

* the pieces that are the default engine's operators restricted to a range
  (the jittered hold) give the same values exactly;
* within streaming the chunk size changes nothing, bit for bit;
* against the default engine -- whose noise brick wall and Tx pole are
  whole-waveform FFTs that cannot stream -- the result agrees within its
  statistics: same link instance, same white draws, other filters.
"""

import dataclasses
import tracemalloc

import numpy as np
import pytest

from halo_serdes.cdr.kernels import ms_rx, _ms_rx_py
from halo_serdes.channel import ChannelModel
from halo_serdes.engine import run_time_link
from halo_serdes.engine.stream import Fir, Lead, NoiseSource, ZohSource, noise_fir
from halo_serdes.tx.jitter import jittered_zoh
from test_chunked_kernels import _adc_cfg, _chunked, _ms_cfg, _same

needs_jit = pytest.mark.skipif(ms_rx is _ms_rx_py,
                               reason="millions of symbols through the pure-Python kernel")


def _streamed(cfg, **sim):
    return dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, stream=True, **sim))


def _with_tx_pole(cfg):
    return dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, bw=40e9))


def _read_in(src, sizes, total):
    out, n = [], 0
    i = 0
    while n < total:
        m = min(sizes[i % len(sizes)], total - n)
        out.append(src.read(m))
        n += m
        i += 1
    return np.concatenate(out)


@pytest.mark.parametrize("kind", ["rj", "ramp", "wild"])
def test_zoh_window_is_the_jittered_hold(kind):
    """Same fill, same order, restricted to a range: the same samples."""
    rng = np.random.default_rng(1)
    osr, n, ui = 16, 3_000, 1.0
    v = rng.choice([-1.0, -0.3, 0.3, 1.0], n)
    jit = {"rj": 0.05 * rng.standard_normal(n + 1),
           # a frequency offset: several UI of accumulated shift
           "ramp": np.linspace(0.0, 7.3, n + 1) + 0.02 * rng.standard_normal(n + 1),
           # edges that cross each other
           "wild": 1.4 * rng.standard_normal(n + 1)}[kind] * ui
    want = jittered_zoh(v, osr, jit, ui)
    got = _read_in(ZohSource(v, jit, osr, ui), [997, 64, 5_000, 1], want.size)
    assert np.array_equal(got, want)
    # and past the end it is silence, as the default engine's zero padding
    src = ZohSource(v, jit, osr, ui)
    src.read(want.size)
    assert not np.any(src.read(100))


def test_fir_stage_does_not_depend_on_how_it_is_read():
    rng = np.random.default_rng(2)
    h = rng.standard_normal(700)
    total = 50_000

    def run(sizes):
        return _read_in(Fir(NoiseSource(np.random.default_rng(3)), h, 4096), sizes, total)

    a = run([total])
    assert np.array_equal(a, run([1, 997, 4096, 13]))
    x = np.random.default_rng(3).normal(size=total)
    assert np.allclose(a, np.convolve(x, h)[:total], atol=1e-11)
    # a lead drops the response's non-causal samples
    b = _read_in(Lead(Fir(NoiseSource(np.random.default_rng(3)), h, 4096), 50), [333], total - 50)
    assert np.allclose(b, np.convolve(x, h)[50:total], atol=1e-11)


def test_noise_fir_keeps_the_variance_and_the_band():
    osr = 16
    h = noise_fir(osr)
    assert abs(np.sum(h * h) - 1.0) < 1e-12
    H = np.abs(np.fft.rfft(h, 1 << 16))
    f = np.fft.rfftfreq(1 << 16)            # cycles / sample; baud = 1 / osr
    assert np.all(np.abs(H[f < 0.9 / osr] / H[0] - 1.0) < 1e-3)
    assert np.all(H[f > 1.1 / osr] / H[0] < 1e-3)


@pytest.mark.parametrize("make", [_ms_cfg, _adc_cfg], ids=["mixed_signal", "adc_dsp"])
def test_streamed_chunking_does_not_change_a_bit(make):
    cfg = _streamed(_with_tx_pole(make()))
    cm = ChannelModel.from_config(cfg)
    whole = run_time_link(_chunked(cfg, cfg.sim.n_symbols), channel=cm)
    for chunk in (997, 1):
        _same(whole, run_time_link(_chunked(cfg, chunk), channel=cm))


@pytest.mark.parametrize("make", [_ms_cfg, _adc_cfg], ids=["mixed_signal", "adc_dsp"])
@pytest.mark.parametrize("pole", [False, True], ids=["no_pole", "tx_pole"])
def test_streamed_agrees_with_the_default_engine(make, pole):
    """Same link instance and white draws; only the noise band-limit and the
    Tx pole are other (equivalent) filters."""
    cfg = make(40_000)
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=3 * cfg.rx.noise_rms))
    if pole:
        cfg = _with_tx_pole(cfg)
    cm = ChannelModel.from_config(cfg)
    full = run_time_link(cfg, channel=cm)
    st = run_time_link(_streamed(cfg), channel=cm)
    assert st.ber.n_checked == full.ber.n_checked
    assert abs(st.slicer_snr_db - full.slicer_snr_db) < 0.1, (st.slicer_snr_db, full.slicer_snr_db)
    e1, e2 = full.ber.n_errors, st.ber.n_errors
    assert abs(e1 - e2) <= 3 * np.sqrt(e1 + e2) + 3, (e1, e2)


def test_streamed_eye_is_the_head_of_the_waveform():
    cfg = _ms_cfg(8_000)
    cm = ChannelModel.from_config(cfg)
    full = run_time_link(cfg, channel=cm, collect_eye=True)
    st = run_time_link(_streamed(cfg), channel=cm, collect_eye=True)
    assert st.eye_data.shape == full.eye_data.shape
    # the same traces up to the two noise filters
    assert np.std(st.eye_data - full.eye_data) < 0.2 * cfg.rx.noise_rms


@pytest.mark.parametrize("what", ["jitter", "xtalk"])
def test_what_needs_the_whole_waveform_says_so(what):
    cfg = _streamed(_ms_cfg(4_000))
    with pytest.raises(ValueError, match="sim.stream does not support"):
        if what == "jitter":
            run_time_link(cfg, collect_jitter=True)
        else:
            run_time_link(cfg, xtalk=[object()])     # refused before it is looked at


@needs_jit
def test_long_streamed_run_memory_stays_flat():
    """The ROADMAP acceptance smoke test: 5x10^6 symbols at OSR 16. The
    default engine would hold several waveform arrays of 8 x 16 bytes per
    symbol each; streamed, what is left is the per-symbol bookkeeping."""
    n = 5_000_000
    cfg = _ms_cfg(n)
    cfg = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, pattern="prbs31", stream=True))
    cm = ChannelModel.from_config(cfg)
    run_time_link(_streamed(_ms_cfg(20_000)), channel=cm)       # compile outside the trace
    tracemalloc.start()
    r = run_time_link(cfg, channel=cm)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert r.ber.n_checked > 0.99 * n - 10_000
    assert r.ber.n_errors == 0
    one_waveform = 8 * cfg.osr * n
    assert peak < 1.5 * one_waveform, f"peak {peak / 1e6:.0f} MB"
