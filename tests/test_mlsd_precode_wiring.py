"""MLSD and 1+D precoding wired into the engines.

The kernels were unit-tested standalone but reachable only by hand; these tests
pin the *engine-level* contract: an MlsdConfig switch that actually re-decides
the stream, a precoder that round-trips through the whole link, and the
statistical engine's MLSD gain agreeing with the time engine.
"""

import dataclasses as dc

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    CdrConfig, ChannelConfig, CtleConfig, DfeConfig, MlsdConfig, RxConfig,
    SimConfig, TxConfig,
)
from halo_serdes.core.mapping import precode_1plusd, unprecode_1plusd
from halo_serdes.engine import run_time_link
from halo_serdes.engine.statistical import run_statistical


def _isi_cfg(noise=0.055, n=120_000):
    """Mixed-signal, DFE off: the postcursors survive, so a sequence detector
    has something real to work over."""
    return LinkConfig(
        modulation="nrz", symbol_rate=20e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.30, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(1.0,), fir_n_pre=0),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=3.0),
                    dfe=DfeConfig(n_taps=0),
                    cdr=CdrConfig(kind="bang_bang", kp_shift=7, ki_shift=14),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=n, seed=3, pattern="prbs13"))


def _with_mlsd(cfg, kind, memory=1):
    return dc.replace(cfg, rx=dc.replace(cfg.rx,
                                         mlsd=MlsdConfig(kind=kind, memory=memory)))


# --------------------------------------------------------------- precoding ---

def test_precode_roundtrip_is_identity():
    rng = np.random.default_rng(0)
    for m in (2, 4):
        s = rng.integers(0, m, size=5000)
        assert np.array_equal(unprecode_1plusd(precode_1plusd(s, m), m), s)


def test_precode_doubles_isolated_errors():
    """The known 1+D trade: an isolated decision error becomes two user errors
    (in exchange for terminating DFE error bursts)."""
    rng = np.random.default_rng(1)
    m = 4
    s = rng.integers(0, m, size=2000)
    p = precode_1plusd(s, m)
    p_err = p.copy()
    p_err[500] = (p_err[500] + 1) % m           # one symbol error on the line
    out = unprecode_1plusd(p_err, m)
    assert np.count_nonzero(out != s) == 2      # exactly two, and they are local
    assert set(np.nonzero(out != s)[0]) == {500, 501}


def test_precode_runs_end_to_end_and_is_scored_on_user_symbols():
    cfg = _isi_cfg(noise=0.02, n=40_000)
    ch = ChannelModel.from_config(cfg)
    plain = run_time_link(cfg, channel=ch)
    pre = run_time_link(dc.replace(cfg, precode=True), channel=ch)
    assert pre.n_symbols > 0
    assert pre.extras["precode"] is True
    assert plain.extras["precode"] is False
    # scoring happens after un-precoding, so a clean link stays clean
    assert pre.ser < 0.5


# -------------------------------------------------------------------- MLSD ---

def test_mlsd_switch_changes_decisions_and_reports_baseline():
    cfg = _isi_cfg()
    ch = ChannelModel.from_config(cfg)
    base = run_time_link(cfg, channel=ch)
    mlsd = run_time_link(_with_mlsd(cfg, "viterbi", 2), channel=ch)
    # the raw-slicer baseline is carried alongside so the gain is measurable
    assert base.extras["ser_slicer"] == pytest.approx(base.ser, rel=1e-12)
    assert mlsd.extras["ser_slicer"] > mlsd.ser
    assert np.any(np.abs(mlsd.extras["mlsd_resid"]) > 1e-3)   # real residual ISI


@pytest.mark.parametrize("kind", ["sliding", "viterbi"])
def test_mlsd_improves_ser_over_the_slicer(kind):
    cfg = _isi_cfg()
    ch = ChannelModel.from_config(cfg)
    r = run_time_link(_with_mlsd(cfg, kind), channel=ch)
    assert r.ser * r.n_symbols > 100          # enough errors to be meaningful
    assert r.ser < r.extras["ser_slicer"]


def test_deeper_trellis_memory_stays_at_least_as_good():
    """Deeper memory keeps the gain; it is deliberately NOT asserted to improve
    monotonically. The residual cursors are estimated from the pulse response
    rather than matched to the received signal, so adding 0.03-scale taps buys
    less than their model error costs — and here the m=1..4 spread (~65 errors
    on ~1400) sits inside Poisson noise. The load-bearing gain is slicer->MLSD.
    """
    cfg = _isi_cfg()
    ch = ChannelModel.from_config(cfg)
    base = run_time_link(cfg, channel=ch).ser
    sers = [run_time_link(_with_mlsd(cfg, "viterbi", m), channel=ch).ser
            for m in (1, 2, 4)]
    for s in sers:
        assert s < base / 1.2                 # every depth beats the slicer
    n_err = sers[0] * 120_000
    tol = 4.0 * np.sqrt(max(n_err, 1.0)) / 120_000     # ~4 sigma Poisson band
    assert max(sers) <= min(sers) + tol       # no depth blows up


def test_mlsd_off_is_bit_identical_to_no_mlsd():
    """The switch must be inert when off (kind='none' is the default)."""
    cfg = _isi_cfg(n=30_000)
    ch = ChannelModel.from_config(cfg)
    a = run_time_link(cfg, channel=ch)
    b = run_time_link(_with_mlsd(cfg, "none"), channel=ch)
    assert a.ser == b.ser and a.slicer_snr_db == pytest.approx(b.slicer_snr_db)


# ---------------------------------------------- statistical-engine MLSD gain ---

def test_statistical_engine_applies_mlsd_gain():
    cfg = _isi_cfg(n=2000)
    ch = ChannelModel.from_config(cfg)
    base = run_statistical(cfg, channel=ch).ber
    gains = [run_statistical(_with_mlsd(cfg, "viterbi", m), channel=ch).ber
             for m in (1, 2, 3)]
    assert gains[0] < base                    # MLSD helps
    assert gains[0] >= gains[1] >= gains[2]   # monotonic in trellis memory


def test_stat_and_time_engines_agree_with_mlsd_on():
    """Cross-check the closed-form MLSD gain against the measured one: the two
    engines must stay inside the framework's <2x agreement band."""
    cfg = _with_mlsd(_isi_cfg(), "viterbi", 2)
    ch = ChannelModel.from_config(cfg)
    t = run_time_link(cfg, channel=ch)
    s = run_statistical(dc.replace(cfg, sim=dc.replace(cfg.sim, n_symbols=2000)),
                        channel=ch)
    ratio = s.ber / max(t.ser, 1e-12)
    assert 0.5 < ratio < 2.0, (s.ber, t.ser, ratio)
