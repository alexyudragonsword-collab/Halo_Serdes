"""Phase 3 of the clock profile: the receiver's own sampling clock, and the live bridge.

The two kernels now take a per-symbol sampling-clock offset and add it to the
loop phase before every sample; ``RxConfig.clock`` describes that clock with
the same ``ClockConfig`` the transmit side uses, and the statistical engine
puts both clocks through the same loop model, since a CDR tracks their
difference. ``io/pll_bridge.py`` builds a profile straight from a pllsim
model for the desktop loop that does not want a file in between.

Pinned here:

* an ideal receiver clock (the default) changes nothing -- both kernels
  reproduce their pre-change output hash to the byte on both JIT paths;
* a receiver profile is tracked like a transmit profile (same loop, same
  residual to within the model's accuracy), and the two add in power where
  the loop is linear: on the Mueller-Muller loop the untracked residual of
  independent transmit and receive clocks is the sum of the two alone;
* on the bang-bang loop the detector gain falls with its total input, so a
  second clock also narrows the loop and the residues are superadditive --
  the model carries that and still matches; recorded as a test so nobody
  chases it as a bug, together with the warning for a detector drowned in
  broadband jitter;
* white receiver RJ smears the statistical bathtub in power sum with the
  transmit RJ, and the kernels sample where it says;
* the bridge reproduces the shipped profiles bit for bit from the sibling
  checkout when one is present, raises an ImportError that names the
  install when pllsim is absent, and refuses a grid that stops short of
  f0/2 rather than dropping clock noise silently.
"""

from __future__ import annotations

import builtins
import dataclasses
import hashlib
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

from halo_serdes.analysis.cdr_tracking import cdr_tracking_error_s
from halo_serdes.cdr.rx_clock import is_ideal, rx_clock_offsets_s
from halo_serdes.channel import ChannelModel
from halo_serdes.config import ClockConfig, LinkConfig, RxConfig, TxConfig, dump_config, load_config
from halo_serdes.config.schema import CdrConfig, ChannelConfig, CtleConfig, DfeConfig, SimConfig
from halo_serdes.engine import run_time_link
from halo_serdes.engine.statistical import run_statistical
from halo_serdes.io import pll_bridge
from halo_serdes.tx.clock import ClockProfile

REPO = Path(__file__).resolve().parents[1]
PROFILES = REPO / "data" / "clock_profiles"
FB = 16e9
UI = 1.0 / FB

#: sha256 over (phase_track, y_slicer, dfe_taps) of a 6000-symbol run of each
#: preset, recorded on the tree *before* the kernels took a receiver-clock
#: argument (JIT and no-JIT agreed). The default RxConfig.clock must land here.
GOLDEN = {
    "NRZ 16G mixed-signal": "480e3f4f2f2c97b837052e0cef406ee7db64f8cff44b545389d6e57f06f664f4",
    "PAM4 224G ADC (112 GBd stress)": "cae5c78ba982950a83d9468f75258cd07d3f539a269540b55f018bfcb5e7914a",
}


def _slope_profile(f0: float, l1_dbc: float, f1: float, corner: float) -> ClockProfile:
    f = np.geomspace(100.0, f0 / 2.0, 400)
    l_dbc = l1_dbc - 20.0 * np.log10(f / f1)
    l_dbc = np.where(f < corner, l1_dbc - 20.0 * np.log10(corner / f1), l_dbc)
    return ClockProfile(f0_hz=f0, f_hz=f, l_dbc_hz=l_dbc)


def _clean_link(kp_shift: int = 6, n_sym: int = 127 * 800, **clocks) -> LinkConfig:
    return LinkConfig(
        modulation="nrz", symbol_rate=FB, osr=32,
        channel=ChannelConfig(kind="analytic", length_m=0.02),
        tx=TxConfig(swing=1.0, clock=clocks.get("tx", ClockConfig())),
        rx=RxConfig(arch="mixed_signal", noise_rms=0.0, ctle=CtleConfig(enable=False),
                    dfe=DfeConfig(n_taps=1), clock=clocks.get("rx", ClockConfig()),
                    cdr=CdrConfig(kind="bang_bang", kp_shift=kp_shift, ki_shift=12)),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs7"))


# ------------------------------------------------------------- the config

def test_rx_clock_defaults_to_ideal_and_round_trips(tmp_path):
    assert RxConfig().clock == ClockConfig()
    assert is_ideal(RxConfig().clock)
    cfg = dataclasses.replace(LinkConfig(), rx=RxConfig(clock=ClockConfig(rj_ui=0.003)))
    p = tmp_path / "c.yaml"
    dump_config(cfg, p)
    assert load_config(p) == cfg
    with pytest.raises(ValueError, match="clock.file"):
        RxConfig(clock=ClockConfig(kind="profile"))


def test_bridge_fields_cover_the_receiver_clock():
    from halo_serdes_app.config_bridge import FIELD_BY_PATH, apply_overrides, build_config

    assert FIELD_BY_PATH["rx.clock.kind"]["options"] == ["white", "profile"]
    assert FIELD_BY_PATH["rx.clock.file"]["kind"] == "opt_enum"
    opts = FIELD_BY_PATH["rx.clock.file"]["options"]
    assert opts == FIELD_BY_PATH["tx.clock.file"]["options"] and opts
    cfg = build_config({"rx.clock.kind": "profile", "rx.clock.file": opts[0]})
    assert Path(cfg.rx.clock.file).is_absolute() and Path(cfg.rx.clock.file).is_file()
    a = apply_overrides(LinkConfig(), {"rx.clock.kind": "profile", "rx.clock.file": "x.yaml"})
    assert a.rx.clock.kind == "profile" and a.tx.clock == ClockConfig()


def test_an_ideal_receiver_clock_draws_no_random_numbers():
    cfg = _clean_link()
    rng = np.random.default_rng(1)
    before = rng.bit_generator.state
    assert not rx_clock_offsets_s(1000, cfg, rng).any()
    assert rng.bit_generator.state == before
    cfg2 = _clean_link(rx=ClockConfig(rj_ui=0.01))
    off = rx_clock_offsets_s(100_000, cfg2, np.random.default_rng(1))
    assert np.std(off) == pytest.approx(0.01 * UI, rel=0.02)


# -------------------------------------------------------- the kernels

@pytest.mark.parametrize("name", list(GOLDEN))
def test_ideal_receiver_clock_is_bit_identical_to_the_pre_change_kernels(name):
    from halo_serdes_app.config_bridge import load_preset

    base = load_preset(name)
    cfg = dataclasses.replace(base, sim=dataclasses.replace(base.sim, n_symbols=6000))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = run_time_link(cfg, channel=ChannelModel.from_config(cfg))
    h = hashlib.sha256()
    for arr in (res.extras["phase_track"], res.y_slicer, res.dfe_taps):
        h.update(np.ascontiguousarray(np.asarray(arr, dtype=np.float64)).tobytes())
    assert h.hexdigest() == GOLDEN[name]


def test_kernels_sample_where_the_receiver_clock_says():
    """A constant receiver-clock offset moves every reported sampling
    position by that amount and nothing else: the loop locks to the same
    place relative to the data, the decisions are the same."""
    from halo_serdes.cdr.kernels import ms_rx
    from halo_serdes.tx.jitter import jittered_zoh

    osr, n = 16, 6000
    rng = np.random.default_rng(0)
    sym = rng.integers(0, 2, size=n)
    levels = np.array([-0.5, 0.5])
    y = jittered_zoh(levels[sym], osr, np.zeros(n + 1), 1 / 32e9)
    y = np.concatenate([np.zeros(4 * osr), y, np.zeros(4 * osr)])
    ref = np.full(n - 500, -1, dtype=np.int64)

    def run(off):
        return ms_rx(y, osr, 4 * osr + osr / 2.0, n - 500, levels, np.zeros(0), 0.0, 100,
                     osr / 64, osr / 4096, 0.0, 1.0, ref, 0, 0, 0, np.zeros(2), off)

    dec0, _, ph0, *_ = run(np.zeros(n - 500))
    dec1, _, ph1, *_ = run(np.full(n - 500, 3.0))
    assert np.array_equal(dec0[1000:], dec1[1000:])
    # the sampler fired 3 samples late; the loop pulled its own phase back by 3
    assert np.allclose(ph1[1000:] - ph0[1000:], 0.0, atol=0.6)


# -------------------------------------------- through the loop, both engines

def test_a_receiver_profile_is_tracked_like_a_transmit_profile(tmp_path):
    """Same 5 MHz-corner 1/f^2 clock on the transmit side and on the receive
    side: the loop cannot tell them apart (it tracks the difference), so the
    residual is the same within the measurement, and the model says so."""
    path = str(_slope_profile(FB, -62.0, 1e6, 5e6).save(tmp_path / "p.yaml"))
    out = {}
    for side in ("tx", "rx"):
        cfg = _clean_link(**{side: ClockConfig(kind="profile", file=path)})
        cm = ChannelModel.from_config(cfg)
        meas = float(np.std(cdr_tracking_error_s(cfg, run_time_link(cfg, channel=cm)))) / UI
        sol = run_statistical(cfg, channel=cm).extras["clock_loop"]
        assert sol is not None
        out[side] = (meas, sol.sigma_ui)
    assert out["rx"][0] == pytest.approx(out["tx"][0], rel=0.2)
    assert out["rx"][1] == pytest.approx(out["tx"][1], rel=1e-6)   # the model is symmetric
    assert out["rx"][0] / out["rx"][1] == pytest.approx(1.0, abs=0.3)


def test_untracked_power_of_independent_clocks_adds_on_the_linear_loop(tmp_path):
    """Mueller-Muller (ADC) loop, detector gain independent of its input:
    the residual power above the ideal-clock floor with both clocks is the
    sum of the two alone. Broadband profiles (50/100 MHz corners against a
    0.7 MHz loop) so a single realisation has thousands of bins and converges;
    measured 0.98-1.09 over two seeds and two gains. The model adds them by
    construction (one error response, two integrals)."""
    from halo_serdes_app.config_bridge import load_preset

    n_sym = 200_000
    base = load_preset("PAM4 224G ADC (112 GBd stress)")
    base = dataclasses.replace(
        base, sim=dataclasses.replace(base.sim, n_symbols=n_sym),
        rx=dataclasses.replace(base.rx, cdr=dataclasses.replace(base.rx.cdr, kp_shift=4)))
    f_lo, f0 = base.symbol_rate / n_sym, base.symbol_rate
    ptx = str(_slope_profile(f0, -100.0, 1e6, 50e6).scaled_to_rms(200e-15, f_lo).save(tmp_path / "tx.yaml"))
    prx = str(_slope_profile(f0, -100.0, 1e6, 100e6).scaled_to_rms(150e-15, f_lo).save(tmp_path / "rx.yaml"))
    cm = ChannelModel.from_config(base)
    var_meas, var_model = {}, {}
    for name, (tx, rx) in {
        "ideal": (ClockConfig(), ClockConfig()),
        "tx": (ClockConfig(kind="profile", file=ptx), ClockConfig()),
        "rx": (ClockConfig(), ClockConfig(kind="profile", file=prx)),
        "both": (ClockConfig(kind="profile", file=ptx), ClockConfig(kind="profile", file=prx)),
    }.items():
        cfg = dataclasses.replace(base, tx=dataclasses.replace(base.tx, clock=tx),
                                  rx=dataclasses.replace(base.rx, clock=rx))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = run_time_link(cfg, channel=cm)
            st = run_statistical(cfg, channel=cm, ffe_taps=res.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
        var_meas[name] = float(np.var(cdr_tracking_error_s(cfg, res, skip=n_sym // 2)))
        sol = st.extras["clock_loop"]
        var_model[name] = (sol.sigma_untracked_ui * cfg.ui) ** 2 if sol is not None else 0.0
    both = var_meas["both"] - var_meas["ideal"]
    alone = (var_meas["tx"] - var_meas["ideal"]) + (var_meas["rx"] - var_meas["ideal"])
    assert both == pytest.approx(alone, rel=0.15), (var_meas, both / alone)
    assert var_model["both"] == pytest.approx(var_model["tx"] + var_model["rx"], rel=1e-6)


def test_bang_bang_loop_with_two_clocks_matches_the_model_but_is_superadditive(tmp_path):
    """On the bang-bang loop the detector gain falls with its total input,
    so adding a second clock also narrows the loop: the residual with both
    clocks exceeds the sum of the two alone (measured 1.24x on the clean
    16 GBd link) while the model, which carries that dependence, still
    lands on the measurement. Recorded so the non-additivity is read as the
    detector, not as a bug in the receiver clock."""
    ptx = str(_slope_profile(FB, -55.0, 1e6, 100e6).save(tmp_path / "tx.yaml"))
    prx = str(_slope_profile(FB, -58.0, 1e6, 150e6).save(tmp_path / "rx.yaml"))
    cm = ChannelModel.from_config(_clean_link(8))
    var = {}
    for name, clocks in {"ideal": {}, "tx": {"tx": ClockConfig(kind="profile", file=ptx)},
                         "rx": {"rx": ClockConfig(kind="profile", file=prx)},
                         "both": {"tx": ClockConfig(kind="profile", file=ptx),
                                  "rx": ClockConfig(kind="profile", file=prx)}}.items():
        cfg = _clean_link(8, **clocks)
        meas = float(np.std(cdr_tracking_error_s(cfg, run_time_link(cfg, channel=cm)))) / UI
        var[name] = meas ** 2
        if name == "both":
            sol = run_statistical(cfg, channel=cm).extras["clock_loop"]
            assert meas / sol.sigma_ui == pytest.approx(1.0, abs=0.1), (meas, sol)
    assert var["both"] - var["ideal"] > 1.1 * (var["tx"] + var["rx"] - 2 * var["ideal"])


def test_statistical_engine_warns_when_the_detector_is_noise_limited(tmp_path):
    """Broadband clock jitter the loop cannot track randomises the bang-bang
    comparison itself; from ~0.1 UI the kernel loses lock (measured: locked
    at 0.08 UI, slipping at 0.14), which the slew check -- about wander inside
    the bandwidth -- does not see. A second warning does."""
    cfg = _clean_link(8, tx=ClockConfig(kind="profile",
                                        file=str(_slope_profile(FB, -50.0, 1e6, 100e6).save(tmp_path / "p.yaml"))))
    with pytest.warns(UserWarning, match="noise-limited"):
        run_statistical(cfg)


def test_white_receiver_rj_adds_in_power_in_the_statistical_smear():
    cfg = _clean_link(tx=ClockConfig(rj_ui=0.006), rx=ClockConfig(rj_ui=0.008))
    st = run_statistical(cfg)
    assert st.extras["jitter_sigma_ui"] == pytest.approx(0.01)
    assert st.extras["clock_loop"] is None
    # and the kernel sampled with it: the residual carries the 0.008 UI on top of the hunting
    res = run_time_link(_clean_link(rx=ClockConfig(rj_ui=0.008)))
    ideal = run_time_link(_clean_link())
    v_rx = np.var(cdr_tracking_error_s(_clean_link(rx=ClockConfig(rj_ui=0.008)), res)) / UI ** 2
    v_0 = np.var(cdr_tracking_error_s(_clean_link(), ideal)) / UI ** 2
    assert v_rx > v_0 + 0.5 * 0.008 ** 2


# ------------------------------------------------------------- the bridge

class _FakeAnalysis:
    """What the bridge reads off a pllsim AnalysisResult, and nothing else."""

    def __init__(self, f0: float, f: np.ndarray, s_phi: np.ndarray, spurs: dict):
        self.f, self.f0, self.pn_breakdown, self.spurs_analytic = f, f0, {"total": s_phi}, spurs


def test_bridge_converts_an_analysis_result_the_way_the_file_format_does():
    f0 = 8e9
    f = pll_bridge.profile_grid(f0)
    s_phi = 2.0 * 10 ** (-100.0 / 10.0) * np.ones_like(f)          # L = -100 dBc/Hz flat
    ar = _FakeAnalysis(f0, f, s_phi, {"frac_spur@9696000Hz": -70.0, "ref_spur": -60.0,
                                     "frac_offset_hz": 9696000.0, "dead_spur@1Hz": float("nan")})
    prof = pll_bridge.profile_from_analysis(ar, source="test", fref_hz=100e6)
    assert np.allclose(prof.l_dbc_hz, -100.0) and prof.f_hz[-1] == pytest.approx(f0 / 2)
    assert prof.spurs == ((9696000.0, -70.0), (100e6, -60.0))
    assert prof.rms_jitter_s(1e3, 1e8) == pytest.approx(
        np.sqrt(2e-10 * (1e8 - 1e3)) / (2 * np.pi * f0), rel=1e-3)
    with pytest.raises(ValueError, match="fref_hz"):
        pll_bridge.profile_from_analysis(ar, source="test")
    short = _FakeAnalysis(f0, f[f < 1e9], s_phi[f < 1e9], {})
    with pytest.raises(ValueError, match="f0/2"):
        pll_bridge.profile_from_analysis(short)


def test_bridge_names_the_install_when_pllsim_is_absent(monkeypatch):
    real = builtins.__import__

    def blocked(name, *a, **k):
        if name == "pllsim" or name.startswith("pllsim."):
            raise ImportError("blocked for the test")
        return real(name, *a, **k)

    for m in [m for m in sys.modules if m == "pllsim" or m.startswith("pllsim.")]:
        monkeypatch.delitem(sys.modules, m)
    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ImportError, match=r"halo-serdes\[pll\]"):
        pll_bridge.profile_from_preset("cppll_19p2m_4p8g")


def test_bridge_reproduces_the_shipped_profiles_from_a_sibling_checkout():
    """With pll_simulator checked out beside this repo, the live bridge and the
    shipped files (its ex22 export) agree to the last digit, and the jitter
    over pllsim's own integration band equals ``ar.jitter_fs``."""
    sibling = REPO.parent.parent / "alexyudragonsword-collab" / "pll_simulator" / "src"
    if not (sibling / "pllsim").is_dir():
        pytest.skip("no pll_simulator sibling checkout")
    sys.path.insert(0, str(sibling))
    try:
        pllsim = pytest.importorskip("pllsim")
        from pllsim import presets
    finally:
        sys.path.remove(str(sibling))
    name = "bench_wu19_spll_frac_52m_6p253g"
    prof = pll_bridge.profile_from_preset(name)
    shipped = ClockProfile.load(PROFILES / f"{name}.yaml")
    assert prof.f0_hz == shipped.f0_hz
    l_on_grid = np.interp(np.log10(prof.f_hz), np.log10(shipped.f_hz), shipped.l_dbc_hz)
    assert np.max(np.abs(l_on_grid - prof.l_dbc_hz)) < 1e-6
    assert prof.spurs == pytest.approx(shipped.spurs)
    pll = presets.ALL_PRESETS[name]()
    ar = pll.analyze(f=pll_bridge.profile_grid(pll.cfg.fout))
    f1, f2 = ar.int_band
    assert prof.rms_jitter_s(f1, f2) * 1e15 == pytest.approx(ar.jitter_fs, rel=1e-6)
    assert "live bridge" in prof.source and pllsim is not None


def test_phone_path_never_imports_the_bridge():
    """The bridge lives in io/ and nothing on the compute path imports it;
    importing it is itself free of pllsim (the import sits inside the one
    function that needs it)."""
    import importlib

    mod = importlib.import_module("halo_serdes.io.pll_bridge")
    assert "pllsim" not in sys.modules or mod is not None
    src = Path(mod.__file__).read_text()
    assert "import pllsim" in src and not src.lstrip().startswith("import pllsim")
    for m in ("halo_serdes.engine.statistical", "halo_serdes.engine.timedomain",
              "halo_serdes.cdr.rx_clock", "halo_serdes.tx.clock"):
        assert "pll_bridge" not in Path(importlib.import_module(m).__file__).read_text()
