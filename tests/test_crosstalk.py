"""Time-domain FEXT/NEXT crosstalk injection (task ③)."""

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel, synthetic_aggressor
from halo_serdes.channel.crosstalk import XtalkAggressor, inject_crosstalk
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link
from halo_serdes.engine.statistical import run_statistical


def test_synthetic_aggressor_hits_target_coupling():
    ui, osr = 1 / 28e9, 16
    dt = ui / osr
    for kind in ("fext", "next"):
        for db in (-30.0, -22.0, -16.0):
            agg = synthetic_aggressor(kind, db, ui, dt)
            assert agg.coupling_peak_db(osr) == pytest.approx(db, abs=0.2)


def test_time_contribution_length_and_independence():
    ui, osr = 1 / 28e9, 16
    dt = ui / osr
    a = synthetic_aggressor("fext", -20.0, ui, dt, seed=1)
    b = synthetic_aggressor("fext", -20.0, ui, dt, seed=2)
    ya = a.time_contribution(50_000, osr, "nrz")
    yb = b.time_contribution(50_000, osr, "nrz")
    assert ya.size == 50_000 and yb.size == 50_000
    assert np.any(ya != 0.0)
    # different seeds -> independent (uncorrelated) aggressor data
    corr = np.corrcoef(ya, yb)[0, 1]
    assert abs(corr) < 0.1


def test_inject_crosstalk_is_additive():
    rng = np.random.default_rng(0)
    rx = rng.normal(size=8000)
    ui, osr = 1 / 28e9, 16
    agg = synthetic_aggressor("next", -20.0, ui, ui / osr, seed=3)
    out = inject_crosstalk(rx, [agg], osr, "nrz")
    assert not np.shares_memory(out, rx)  # original untouched
    assert np.allclose(out - rx, agg.time_contribution(rx.size, osr, "nrz"))
    # empty aggressor list is a no-op passthrough
    assert inject_crosstalk(rx, None, osr, "nrz") is rx


def _cfg(noise=0.004, n=60_000):
    return LinkConfig(
        modulation="nrz", symbol_rate=28e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.30, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.85, -0.05), fir_n_pre=1,
                    rj_ui=0.004),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                    dfe=DfeConfig(n_taps=2), noise_rms=noise),
        sim=SimConfig(n_symbols=n, seed=5, pattern="prbs13"))


def test_engine_crosstalk_degrades_snr_monotonically():
    cfg = _cfg()
    cm = ChannelModel.from_config(cfg)
    fext = synthetic_aggressor("fext", -22.0, cfg.ui, cfg.dt, seed=11)
    nxt = synthetic_aggressor("next", -26.0, cfg.ui, cfg.dt, seed=22)
    base = run_time_link(cfg, channel=cm)
    one = run_time_link(cfg, channel=cm, xtalk=[fext])
    two = run_time_link(cfg, channel=cm, xtalk=[fext, nxt])
    assert base.slicer_snr_db > one.slicer_snr_db > two.slicer_snr_db


def test_same_aggressor_feeds_both_engines():
    """The unified interface: one XtalkAggressor drives the time engine (xtalk)
    and the statistical engine (xtalk_pulses via .pulse). Both must degrade."""
    cfg = _cfg(noise=0.006, n=2000)
    cm = ChannelModel.from_config(cfg)
    agg = synthetic_aggressor("fext", -18.0, cfg.ui, cfg.dt, seed=7)

    s_base = run_statistical(cfg, channel=cm)
    s_xt = run_statistical(cfg, channel=cm, xtalk_pulses=[agg.pulse(cfg.osr)])
    assert s_xt.ber >= s_base.ber

    t_base = run_time_link(cfg, channel=cm)
    t_xt = run_time_link(cfg, channel=cm, xtalk=[agg])
    assert t_xt.slicer_snr_db <= t_base.slicer_snr_db


def test_aggressor_bank_counts_and_independence():
    from halo_serdes.channel import aggressor_bank
    ui, osr = 1 / 53.125e9, 32
    bank = aggressor_bank(3, 2, -28.0, ui, ui / osr, modulation="pam4",
                          base_seed=1)
    assert len(bank) == 5
    assert sum(a.kind == "fext" for a in bank) == 3
    assert sum(a.kind == "next" for a in bank) == 2
    # each lane has an independent data seed
    assert len({a.seed for a in bank}) == 5
    # couplings scatter around the target within the ±spread band
    peaks = [a.coupling_peak_db(osr) for a in bank]
    assert all(-28.0 - 2.5 < p < -28.0 + 2.5 for p in peaks)


def test_icn_grows_with_aggressor_count():
    from halo_serdes.channel import aggressor_bank, icn_rms
    ui, osr = 1 / 53.125e9, 32
    small = aggressor_bank(1, 1, -30.0, ui, ui / osr, modulation="pam4",
                           spread_db=0.0, base_seed=2)
    big = aggressor_bank(4, 4, -30.0, ui, ui / osr, modulation="pam4",
                         spread_db=0.0, base_seed=2)
    icn_s = icn_rms(small, osr, modulation="pam4")
    icn_b = icn_rms(big, osr, modulation="pam4")
    assert icn_s > 0 and icn_b > icn_s
    # RSS power sum: 4x the aggressors (2 -> 8) -> ~2x the ICN voltage
    assert icn_b == pytest.approx(icn_s * np.sqrt(4.0), rel=0.05)
    assert icn_rms([], osr) == 0.0


def test_icn_is_order_independent_with_mixed_swings():
    """Regression: each aggressor must contribute its OWN swing to the power
    sum. Scaling the whole RSS by one lane's swing made the result depend on
    list order."""
    from halo_serdes.channel import icn_rms
    ui, osr = 1 / 53.125e9, 32
    a = synthetic_aggressor("fext", -30.0, ui, ui / osr, swing=1.0, seed=1)
    b = synthetic_aggressor("fext", -30.0, ui, ui / osr, swing=2.0, seed=2)
    ia, ib = icn_rms([a], osr), icn_rms([b], osr)
    # a doubled swing doubles that lane's contribution
    assert ib == pytest.approx(2.0 * ia, rel=1e-9)
    ab, ba = icn_rms([a, b], osr), icn_rms([b, a], osr)
    assert ab == pytest.approx(ba, rel=1e-12)          # order-independent
    assert ab == pytest.approx(np.hypot(ia, ib), rel=1e-9)   # true RSS


def test_bank_feeds_com_and_lowers_margin():
    """A multi-lane bank flows straight into the COM engine as σ_XT."""
    from halo_serdes.analysis.com import compute_com
    from halo_serdes.channel import aggressor_bank
    cfg = _cfg(noise=0.003, n=1000)
    cm = ChannelModel.from_config(cfg)
    clean = compute_com(cm, cfg).com_db
    bank = aggressor_bank(4, 4, -26.0, cfg.ui, cfg.dt, base_seed=3)
    pulses = [a.pulse(cfg.osr) for a in bank]
    loud = compute_com(cm, cfg, xtalk_pulses=pulses)
    assert loud.com_db < clean
    assert loud.detail["n_aggressors"] == 8 and loud.fom_xtalk > 0


def test_import_xtalk_from_synthetic_network():
    """import_xtalk pulls a coupling impulse out of a multi-port network."""
    rf = pytest.importorskip("skrf")
    # 2-port toy: through ~flat, coupling (S21) a single-pole lowpass
    f = np.linspace(1e6, 40e9, 400)
    freq = rf.Frequency.from_f(f / 1e9, unit="GHz")
    s = np.zeros((f.size, 2, 2), dtype=complex)
    s[:, 0, 0] = 0.05
    s[:, 1, 1] = 0.05
    coupling = 0.1 / (1 + 1j * f / 10e9)
    s[:, 1, 0] = coupling
    s[:, 0, 1] = coupling
    ntwk = rf.Network(frequency=freq, s=s)

    from halo_serdes.channel import import_xtalk

    agg = import_xtalk(ntwk, victim_out=1, aggressor_in=0, dt=1 / (28e9 * 16),
                       f_max=40e9, kind="fext")
    assert isinstance(agg, XtalkAggressor)
    assert agg.coupling.size > 0 and np.any(agg.coupling != 0.0)
