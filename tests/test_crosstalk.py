"""Time-domain FEXT/NEXT crosstalk injection (task ③)."""

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel, synthetic_aggressor
from halo_serdes.channel.crosstalk import XtalkAggressor, inject_crosstalk
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, ClockConfig, TxConfig,
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
        tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.85, -0.05), fir_n_pre=1, clock=ClockConfig(rj_ui=0.004)),
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


# ------------------------------------------- lane skew: one phase, two engines

def _preset_with_bank(name, noise, coupling_db, n_symbols):
    import dataclasses

    from halo_serdes.channel import aggressor_bank
    from halo_serdes_app import config_bridge as cb

    cfg = cb.load_preset(name)
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=noise),
                              sim=dataclasses.replace(cfg.sim, n_symbols=n_symbols))
    return cfg, aggressor_bank(2, 2, coupling_db, cfg.ui, cfg.dt, modulation=cfg.modulation,
                               base_seed=1)


def _both(cfg, bank, skew_ui):
    import dataclasses

    xt = [dataclasses.replace(a, skew_s=skew_ui * cfg.ui) for a in bank]
    t = run_time_link(cfg, xtalk=xt)
    kw = dict(ffe_taps=t.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre) if cfg.rx.arch == "adc_dsp" else {}
    s = run_statistical(cfg, xtalk_pulses=[a.pulse(cfg.osr) for a in xt], **kw)
    return t.ber.ber, s.ber


@pytest.mark.parametrize("name, noise, coupling_db, skews", [
    ("PAM4 224G ADC (106 GBd)", 0.0015, -46.0, (0.0, 0.25, 0.5, 0.75)),
    ("PAM4 32G mixed-signal", 0.006, -34.0, (0.0, 0.25, 0.5)),
])
def test_invariant3_with_crosstalk_at_every_lane_skew(name, noise, coupling_db, skews):
    """The statistical engine reads the aggressors where the time engine's
    victim samples them -- on its own sampling grid, after the FFE on an ADC
    receiver -- for the skew both are given. Read at each aggressor's own peak
    (and, on the ADC, before the FFE) it was 2.9-49x off as the skew moved."""
    cfg, bank = _preset_with_bank(name, noise, coupling_db, 60_000)
    bers = [_both(cfg, bank, s) for s in skews]
    for t, s in bers:
        assert 0.5 < s / t < 2.0, bers
    times = [t for t, _ in bers]
    assert max(times) > 2.5 * min(times), bers          # the skew matters ...
    stats = [s for _, s in bers]
    assert np.argmax(stats) == np.argmax(times), bers    # ... the same way in both


def test_skew_delays_the_coupling_both_engines_see():
    ui, osr = 1 / 28e9, 16
    a = synthetic_aggressor("fext", -20.0, ui, ui / osr, seed=3)
    assert a.launched_coupling is a.coupling                       # no skew: untouched
    b = XtalkAggressor(coupling=a.coupling, dt=a.dt, seed=3, skew_s=0.25 * ui)
    assert np.array_equal(b.launched_coupling[4:], a.coupling)
    assert not np.any(b.launched_coupling[:4])
    assert np.array_equal(b.pulse(osr).y[4:4 + 100], a.pulse(osr).y[:100])
    yb = b.time_contribution(20_000, osr, "nrz")
    ya = a.time_contribution(20_000, osr, "nrz")
    assert np.array_equal(yb[4:], ya[:-4])
    with pytest.raises(ValueError, match="skew_s"):
        XtalkAggressor(coupling=a.coupling, dt=a.dt, skew_s=-1e-12)


# ------------------------------------- the aggressor PDFs sit where the victim's does

def _flat_cfg(mod, sigma, pattern):
    from halo_serdes.config.schema import FfeConfig

    return LinkConfig(
        modulation=mod, symbol_rate=32e9, osr=8, channel=ChannelConfig(kind="analytic"),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(ctle=CtleConfig(enable=False), ffe=FfeConfig(n_pre=0, n_post=0),
                    dfe=DfeConfig(n_taps=0), noise_rms=sigma),
        sim=SimConfig(n_symbols=100_000, seed=5, pattern=pattern))


def _flat_channel(n=1025):
    f = np.linspace(0, 128e9, n)
    return ChannelModel(np.full(n, 0.5, dtype=complex), f, name="flat")


@pytest.mark.parametrize("mod, sigma, pattern", [
    ("nrz", 0.25 / 4.75, "prbs31"),
    ("pam4", (0.25 / 3.0) / 4.0, "prbs13q"),
])
def test_aggressors_with_no_coupling_leave_the_statistical_ser_alone(mod, sigma, pattern):
    """Each aggressor's ISI PDF is convolved onto the victim's on a 4096-bin
    grid; ``np.convolve(mode="same")`` put the centre of that even grid one
    bin late per aggressor (ROADMAP P2 #36). Eight aggressors coupling
    nothing then read 8 % (NRZ) to 22 % (PAM4) more SER, and the
    quasi-synchronous (drifting) path the same per lane."""
    import dataclasses

    from halo_serdes.config import CrosstalkConfig

    cfg = _flat_cfg(mod, sigma, pattern)
    ch = _flat_channel()
    ref = run_statistical(cfg, channel=ch).ser
    for n_agg in (1, 8):
        aggs = [synthetic_aggressor("fext" if k % 2 == 0 else "next", -120.0, cfg.ui, cfg.dt,
                                    seed=10 + k) for k in range(n_agg)]
        ser = run_statistical(cfg, channel=ch, xtalk_pulses=[a.pulse(cfg.osr) for a in aggs]).ser
        assert ser == pytest.approx(ref, rel=1e-4), (n_agg, ser, ref)
    drifting = dataclasses.replace(cfg, crosstalk=CrosstalkConfig(
        n_fext=4, n_next=4, coupling_db=-120.0, ppm=100.0))
    assert run_statistical(drifting, channel=ch).ser == pytest.approx(ref, rel=1e-4)


def test_com_with_no_coupling_is_the_com_without_aggressors():
    """The COM's A_ni PDF has the same convolution (8192 bins): eight
    aggressors coupling nothing cost it 0.25 dB before the centring."""
    from halo_serdes.analysis.com import compute_com
    from halo_serdes.channel import aggressor_bank
    from halo_serdes_app import config_bridge as cb

    cfg = cb.load_preset("PAM4 32G mixed-signal")
    ch = ChannelModel.from_config(cfg)
    clean = compute_com(ch, cfg).com_db
    bank = aggressor_bank(4, 4, -120.0, cfg.ui, cfg.dt, modulation=cfg.modulation, base_seed=1)
    nil = compute_com(ch, cfg, xtalk_pulses=[a.pulse(cfg.osr) for a in bank]).com_db
    assert nil == pytest.approx(clean, abs=1e-3), (nil, clean)


# ------------------------------------- crosstalk in the config; quasi-synchronous lanes

def _xt_cfg(name, noise, n_symbols, **xt):
    import dataclasses

    from halo_serdes.config import CrosstalkConfig
    from halo_serdes_app import config_bridge as cb

    cfg = cb.load_preset(name)
    return dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=noise),
                               sim=dataclasses.replace(cfg.sim, n_symbols=n_symbols),
                               crosstalk=CrosstalkConfig(**xt))


def test_config_builds_the_bank_and_the_listed_lanes():
    from halo_serdes.channel.crosstalk import aggressors_from_config
    from halo_serdes.config import AggressorConfig, CrosstalkConfig

    cfg = LinkConfig(modulation="pam4", tx=TxConfig(swing=0.8), crosstalk=CrosstalkConfig(
        n_fext=2, n_next=1, coupling_db=-30.0, seed=4, skew_ui=0.25, ppm=50.0,
        aggressors=(AggressorConfig(kind="next", coupling_db=-36.0, skew_ui=0.5),)))
    aggs = aggressors_from_config(cfg)
    assert [a.kind for a in aggs] == ["fext", "fext", "next", "next"]
    assert len({a.seed for a in aggs}) == 4                         # no two lanes share data
    assert all(a.modulation == "pam4" and a.swing == 0.8 for a in aggs)
    assert [a.ppm for a in aggs] == [50.0, 50.0, 50.0, 0.0]
    assert [a.skew_s / cfg.ui for a in aggs] == pytest.approx([0.25, 0.25, 0.25, 0.5])
    assert aggs[-1].coupling_peak_db(cfg.osr) == pytest.approx(-36.0, abs=0.2)
    assert aggressors_from_config(LinkConfig()) == []


def test_crosstalk_config_round_trips_through_yaml(tmp_path):
    pytest.importorskip("yaml")
    from halo_serdes.config import CrosstalkConfig, dump_config, load_config

    cfg = LinkConfig(crosstalk=CrosstalkConfig(n_next=3, ppm=-100.0, aggressors=(
        {"kind": "fext", "coupling_db": -28.0, "skew_ui": 0.125},)))
    dump_config(cfg, tmp_path / "x.yaml")
    assert load_config(tmp_path / "x.yaml") == cfg


@pytest.mark.parametrize("kw, match", [
    (dict(n_fext=-1), "n_fext"), (dict(skew_ui=-0.1), "skew_ui"), (dict(ppm=2e4), "ppm"),
    (dict(spread_db=-1.0), "spread_db"),
    (dict(aggressors=({"kind": "both"},)), "kind"),
    (dict(aggressors=({"victim_port": 2, "aggressor_port": 2},)), "ports"),
])
def test_crosstalk_config_is_validated(kw, match):
    from halo_serdes.config import CrosstalkConfig

    with pytest.raises(ValueError, match=match):
        CrosstalkConfig(**kw)


def test_engines_take_the_aggressors_from_the_config():
    """Config and explicit argument are the same aggressors, sample for
    sample; given both, the argument wins and says so."""
    import dataclasses

    from halo_serdes.channel.crosstalk import aggressors_from_config

    cfg = _xt_cfg("PAM4 32G mixed-signal", 0.006, 20_000, n_fext=1, n_next=1,
                  coupling_db=-34.0, seed=1, skew_ui=0.25)
    from_cfg = run_time_link(cfg)
    explicit = run_time_link(dataclasses.replace(cfg, crosstalk=type(cfg.crosstalk)()),
                             xtalk=aggressors_from_config(cfg))
    assert np.array_equal(from_cfg.extras["decisions"], explicit.extras["decisions"])
    assert from_cfg.ber.n_errors > 0
    with pytest.warns(UserWarning, match="replaces cfg.crosstalk"):
        run_time_link(cfg, xtalk=[])
    s_cfg = run_statistical(cfg)
    s_exp = run_statistical(dataclasses.replace(cfg, crosstalk=type(cfg.crosstalk)()),
                            xtalk_pulses=[a.pulse(cfg.osr) for a in aggressors_from_config(cfg)])
    assert s_cfg.ber == pytest.approx(s_exp.ber, rel=1e-12)


def test_symbol_index_walks_ppm_per_ui():
    from halo_serdes.channel.crosstalk import symbol_index

    n = np.arange(16 * 100_000)
    assert np.array_equal(symbol_index(n, 16, 0.0), n // 16)
    k = symbol_index(n, 16, 200.0)
    assert k[-1] == 100_000 + 19                       # 100k UI at +200 ppm: 20 more, less the last partial
    assert set(np.diff(k)) == {0, 1}
    assert symbol_index(n, 16, -200.0)[-1] == 100_000 - 21


def test_quasi_synchronous_hold_streams_like_the_whole_run():
    from halo_serdes.channel.crosstalk import symbol_index
    from halo_serdes.engine.stream import HoldSource

    v = np.random.default_rng(0).normal(size=5_000)
    src = HoldSource(v, 16, 150.0)
    got = np.concatenate([src.read(m) for m in (1, 997, 4000, 31, 20_000)])
    k = symbol_index(np.arange(got.size), 16, 150.0)
    assert np.array_equal(got, v[k])


def test_stream_engine_with_quasi_synchronous_config_crosstalk():
    from test_stream import _both_ways, _ms_cfg

    cfg = _ms_cfg(30_000)
    import dataclasses

    from halo_serdes.config import CrosstalkConfig

    cfg = dataclasses.replace(cfg, crosstalk=CrosstalkConfig(n_fext=1, n_next=1, coupling_db=-16.0,
                                                             spread_db=0.0, seed=11, ppm=200.0))
    _both_ways(cfg, 30)


def test_one_drifting_aggressor_is_the_mean_over_its_phases():
    """The statistical engine's quasi-synchronous aggressor is the synchronous
    one averaged over every phase: with one aggressor (and a PDF tail that is
    linear in it) its first-error rate is the mean of the skews'. The DFE's
    error propagation on top (``dfe_ep_factor``, ROADMAP P1 #21) is not
    linear in the PDF; taken from the averaged one it is 0.4 % off here. Nor
    is the bang-bang loop's hunting smeared in on top (ROADMAP P2 #27): the
    read averages phases each with its own factor, and dividing one out
    leaves 2e-4."""
    cfg = _xt_cfg("PAM4 32G mixed-signal", 0.006, 100_000, n_fext=1, coupling_db=-30.0,
                  spread_db=0.0, seed=1, ppm=100.0)
    drift = run_statistical(cfg)
    import dataclasses

    skews = [run_statistical(dataclasses.replace(cfg, crosstalk=dataclasses.replace(
        cfg.crosstalk, ppm=0.0, skew_ui=k / cfg.osr))) for k in range(cfg.osr)]

    def first(s):
        # the factor is the one at the nearest grid phase, the BER read
        # between two: 3e-5 apart on this link
        return s.ber / s.extras["dfe_ep_factor"]
    assert first(drift) == pytest.approx(np.mean([first(s) for s in skews]), rel=1e-3)
    assert drift.ber == pytest.approx(np.mean([s.ber for s in skews]), rel=0.02)
    bers = [s.ber for s in skews]
    assert max(bers) > 2 * min(bers)                    # the phase mattered


@pytest.mark.parametrize("name, noise, coupling_db", [
    ("PAM4 224G ADC (106 GBd)", 0.0015, -46.0),
    ("PAM4 32G mixed-signal", 0.006, -34.0),
])
def test_invariant3_with_quasi_synchronous_crosstalk(name, noise, coupling_db):
    """Aggressors 100 ppm off the victim's clock: the time engine draws their
    symbols at their own rate and sees every phase in turn; the statistical
    engine averages over the phase. 0.84-0.93x on these links at 300k
    symbols (cairn/多lane建模.md §7)."""
    cfg = _xt_cfg(name, noise, 120_000, n_fext=2, n_next=2, coupling_db=coupling_db,
                  seed=1, ppm=100.0)
    t = run_time_link(cfg)
    kw = dict(ffe_taps=t.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre) if cfg.rx.arch == "adc_dsp" else {}
    s = run_statistical(cfg, **kw)
    assert t.ber.n_errors > 300
    assert 0.5 < s.ber / t.ber.ber < 2.0, (t.ber.ber, s.ber)


def test_short_run_against_slow_drift_warns():
    cfg = _xt_cfg("PAM4 32G mixed-signal", 0.006, 5_000, n_fext=1, ppm=10.0)
    with pytest.warns(UserWarning, match="drifts"):
        run_statistical(cfg)


def test_static_link_and_retimed_cascade_refuse_config_crosstalk():
    import dataclasses

    from halo_serdes.config import CrosstalkConfig
    from halo_serdes.engine import run_static_link
    from halo_serdes.engine.cascade import segment_configs

    cfg = _xt_cfg("PAM4 32G mixed-signal", 0.006, 5_000, n_fext=1)
    with pytest.raises(NotImplementedError, match="crosstalk"):
        run_static_link(cfg)
    from halo_serdes_app import config_bridge as cb

    ret = cb.load_preset("PAM4 32G mixed-signal")
    from halo_serdes.config.schema import OpticalConfig, TopologyConfig
    ret = dataclasses.replace(ret, topology=TopologyConfig(optical=OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0),
                                                           retimer="both"),
                              crosstalk=CrosstalkConfig(n_next=1))
    with pytest.raises(ValueError, match="retimed"):
        segment_configs(ret)


def test_com_reads_the_config_aggressors():
    import dataclasses

    from halo_serdes.analysis.com import compute_com
    from halo_serdes.channel.crosstalk import aggressors_from_config

    cfg = _xt_cfg("NRZ 28G analytic (COM/xtalk)", 0.002, 5_000, n_fext=1, n_next=1, seed=2)
    cm = ChannelModel.from_config(cfg)
    a = compute_com(cm, cfg).com_db
    b = compute_com(cm, dataclasses.replace(cfg, crosstalk=type(cfg.crosstalk)()),
                    xtalk_pulses=[x.pulse(cfg.osr) for x in aggressors_from_config(cfg)]).com_db
    assert a == pytest.approx(b)
    assert a < compute_com(cm, dataclasses.replace(cfg, crosstalk=type(cfg.crosstalk)())).com_db
