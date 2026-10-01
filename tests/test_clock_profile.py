"""A PLL phase-noise profile becomes per-edge timing: the contract, pinned.

``tx.clock.kind = "profile"`` is the first coloured clock this simulator has
had. Everything before it was spec-sheet jitter -- white RJ, one SJ tone, DCD
-- and the acceptance of this feature is a set of closed-form statements about
what the synthesis must reproduce, not a smoke run:

* a flat profile is white phase noise and must land on the equivalent
  ``rj_ui`` to a few percent;
* a 1/f^2 profile is a random walk: the variance of edge-to-edge differences
  grows linearly with the lag;
* a spur at ``dbc`` is a sideband ``dbc`` below the carrier -- read back off the
  synthesised phase spectrum, so the convention is measured, not asserted;
* nothing is added above f0/2, the clock's own Nyquist;
* ``kind="white"`` must remain byte-identical to the engine before this
  existed, which hinges on *when* the profile draws its random numbers.

Two measurements that look like bugs are documented here as tests rather than
left for the next person to rediscover: a single realisation of pure 1/f^2
noise does not integrate to the profile's integral (its expectation is the FFT
bin sum, which is pi^2/6 above the continuous integral, and its scatter is
tens of percent), and ``calc_jitter`` files coloured low-frequency jitter under
Pj, so its Rj is not the number to compare a profile against. The total TIE is.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from halo_serdes.config import ClockConfig, LinkConfig, TxConfig, dump_config, load_config
from halo_serdes.config.schema import ChannelConfig, DfeConfig, RxConfig, SimConfig
from halo_serdes.tx.clock import TWOPI, ClockProfile
from halo_serdes.tx.jitter import edge_jitter_seq
from halo_serdes.vendor.pllsim.jitter import integrate_pn, sphi_from_ldbc

REPO = Path(__file__).resolve().parents[1]
PROFILES = sorted((REPO / "data" / "clock_profiles").glob("*.yaml"))

FB = 16e9                      # a baud rate the mixed-signal engine runs fast at
UI = 1.0 / FB


# ----------------------------------------------------------------- helpers

def _flat_profile(f0: float, rj_ui: float) -> ClockProfile:
    """White PM whose integral over [0, f0/2] is the given rj_ui at f0 = 1/UI."""
    sig_phi = rj_ui * TWOPI                     # sigma_t / UI = sigma_phi / 2pi when f0 = 1/UI
    s_phi = sig_phi ** 2 / (f0 / 2.0)           # one-sided, flat up to Nyquist
    l0 = 10.0 * math.log10(s_phi / 2.0)
    return ClockProfile(f0_hz=f0, f_hz=np.array([100.0, f0 / 2.0]),
                        l_dbc_hz=np.array([l0, l0]))


def _slope_profile(f0: float, l1_dbc: float, f1: float,
                   corner: float | None = None) -> ClockProfile:
    """-20 dB/decade from (f1, l1). With ``corner`` the curve is held flat
    below it -- a loop-filtered PLL rather than a free-running oscillator."""
    f = np.geomspace(100.0, f0 / 2.0, 400)
    l_dbc = l1_dbc - 20.0 * np.log10(f / f1)
    if corner is not None:
        l_dbc = np.where(f < corner, l1_dbc - 20.0 * np.log10(corner / f1), l_dbc)
    return ClockProfile(f0_hz=f0, f_hz=f, l_dbc_hz=l_dbc)


def _write(tmp_path: Path, prof: ClockProfile, name: str = "p.yaml") -> str:
    import yaml

    path = tmp_path / name
    with path.open("w") as fh:
        yaml.safe_dump({"f0_hz": float(prof.f0_hz), "f_hz": prof.f_hz.tolist(),
                        "l_dbc_hz": prof.l_dbc_hz.tolist(),
                        "spurs": [{"f_hz": a, "dbc": b} for a, b in prof.spurs],
                        "source": "test"}, fh)
    return str(path)


def _nrz_link(path: str, n_sym: int, seed: int = 3, osr: int = 32) -> LinkConfig:
    """A clean, short NRZ link whose Tx stage TIE is the clock, nothing else."""
    return LinkConfig(
        modulation="nrz", symbol_rate=FB, osr=osr,
        channel=ChannelConfig(kind="analytic", length_m=0.02),
        tx=TxConfig(swing=1.0, clock=ClockConfig(kind="profile", file=path)),
        rx=RxConfig(arch="mixed_signal", noise_rms=0.0, dfe=DfeConfig(n_taps=1)),
        sim=SimConfig(n_symbols=n_sym, seed=seed, pattern="prbs7"))


# ------------------------------------------------------------- the config

def test_white_is_the_default_and_the_old_fields_live_on_the_clock():
    c = TxConfig()
    assert c.clock == ClockConfig(kind="white")
    assert (c.clock.rj_ui, c.clock.sj_ui, c.clock.sj_freq, c.clock.dcd_ui) == (0, 0, 0, 0)


def test_a_profile_clock_without_a_file_fails_in_load_config(tmp_path):
    """The error has to name the field, from the config layer -- not surface
    as a FileNotFoundError out of the engine three layers down."""
    p = tmp_path / "bad.yaml"
    p.write_text("tx:\n  clock:\n    kind: profile\n")
    with pytest.raises(ValueError, match="tx.clock.file"):
        load_config(p)


def test_old_flat_jitter_keys_still_load_to_the_same_config(tmp_path):
    """Every YAML written before ClockConfig carries ``tx.rj_ui``; it must
    load to exactly what the new spelling loads to (invariant #1 makes this a
    correctness requirement, not a convenience)."""
    old = tmp_path / "old.yaml"
    old.write_text("tx:\n  swing: 0.8\n  rj_ui: 0.01\n  sj_ui: 0.02\n  sj_freq: 5.0e6\n  dcd_ui: 0.003\n")
    new = tmp_path / "new.yaml"
    new.write_text("tx:\n  swing: 0.8\n  clock:\n    rj_ui: 0.01\n    sj_ui: 0.02\n"
                   "    sj_freq: 5.0e6\n    dcd_ui: 0.003\n")
    assert load_config(old) == load_config(new)
    assert load_config(old).tx.clock.sj_freq == 5.0e6


def test_the_new_location_wins_when_a_file_states_both(tmp_path):
    p = tmp_path / "both.yaml"
    p.write_text("tx:\n  rj_ui: 0.01\n  clock:\n    rj_ui: 0.02\n")
    assert load_config(p).tx.clock.rj_ui == 0.02


def test_dump_then_load_round_trips_a_profile_clock(tmp_path):
    cfg = LinkConfig(tx=TxConfig(clock=ClockConfig(
        kind="profile", file="data/clock_profiles/x.yaml", f0_hz=5e9, sj_ui=0.01, sj_freq=1e6)))
    out = tmp_path / "dump.yaml"
    dump_config(cfg, out)
    assert load_config(out) == cfg


def test_relative_profile_paths_resolve_like_channel_files():
    """The app layer, not the engine, knows where data lives -- the same
    arrangement ``channel.file`` has, so a preset can say
    ``data/clock_profiles/...`` and work from any cwd, a frozen bundle, or a
    phone's extracted assets."""
    from halo_serdes_app import config_bridge as cb

    rel = "data/clock_profiles/" + PROFILES[0].name
    cfg = cb.apply_overrides(LinkConfig(), {"tx.clock.kind": "profile", "tx.clock.file": rel})
    resolved = cb._resolve_channel(cfg)
    assert Path(resolved.tx.clock.file).is_absolute()
    assert Path(resolved.tx.clock.file).is_file()
    # the form field list carries the new paths, and only the new paths
    assert "tx.clock.kind" in cb.FIELD_BY_PATH and "tx.rj_ui" not in cb.FIELD_BY_PATH
    assert cb.FIELD_BY_PATH["tx.clock.kind"]["options"] == ["white", "profile"]


def test_kind_and_file_can_be_overridden_in_either_order():
    """Both the GUI form and the Android app set fields through
    apply_overrides in field order, kind before file. Sequential application
    raised on kind="profile" before file arrived; it must not."""
    from halo_serdes.config import apply_overrides

    a = apply_overrides(LinkConfig(), {"tx.clock.kind": "profile", "tx.clock.file": "x.yaml"})
    b = apply_overrides(LinkConfig(), {"tx.clock.file": "x.yaml", "tx.clock.kind": "profile"})
    assert a == b and a.tx.clock.kind == "profile"
    with pytest.raises(KeyError, match="unknown config field"):
        apply_overrides(LinkConfig(), {"tx.clock.nope": 1})
    with pytest.raises(ValueError, match="tx.clock.file"):
        apply_overrides(LinkConfig(), {"tx.clock.kind": "profile"})


# ------------------------------------------------------------- the file

@pytest.mark.parametrize("path", PROFILES, ids=lambda p: p.stem)
def test_every_bundled_profile_loads_and_is_what_the_format_says(path):
    prof = ClockProfile.load(path)
    assert prof.f0_hz > 0
    assert prof.f_hz[0] == pytest.approx(100.0)
    assert prof.f_hz[-1] == pytest.approx(prof.f0_hz / 2.0, rel=1e-9)
    assert np.all(np.diff(prof.f_hz) > 0)
    assert prof.f_hz.size == prof.l_dbc_hz.size
    assert "pllsim" in prof.source and "commit" in prof.source
    for f_s, dbc in prof.spurs:
        assert 0 < f_s < prof.f0_hz and dbc < 0


def test_there_are_seven_bundled_profiles():
    """The seven JSSC anchors of pll_simulator's ex14, as its ex22 writes them."""
    assert len(PROFILES) == 7


@pytest.mark.parametrize("text,frag", [
    ("f0_hz: 1e9\nf_hz: [100, 200]\nl_dbc_hz: [-100]\n", "l_dbc_hz has 1 points"),
    ("f0_hz: 1e9\nf_hz: [200, 100]\nl_dbc_hz: [-100, -100]\n", "ascending"),
    ("f0_hz: 0\nf_hz: [100, 200]\nl_dbc_hz: [-100, -100]\n", "f0_hz"),
    ("f0_hz: 1e9\nf_hz: [100, 200]\nl_dbc_hz: [-100, -100]\nbogus: 1\n", "unknown clock-profile key"),
    ("f0_hz: 1e9\nl_dbc_hz: [-100, -100]\n", "missing"),
    ("f0_hz: 1e9\nf_hz: [100, 200]\nl_dbc_hz: [-100, -100]\nspurs: [{f_hz: 1e6}]\n", "each spur"),
])
def test_a_malformed_profile_is_refused_with_the_reason(tmp_path, text, frag):
    p = tmp_path / "p.yaml"
    p.write_text(text)
    with pytest.raises(ValueError, match=frag):
        ClockProfile.load(p)


def test_a_missing_profile_names_the_path():
    with pytest.raises(FileNotFoundError, match="no/such/file.yaml"):
        ClockProfile.load("no/such/file.yaml")


# -------------------------------------------------- random-number discipline

def test_white_kind_draws_exactly_what_it_always_did():
    """Byte-identity of kind="white" rests on the profile branch not touching
    the generator. Same seed, same call, same numbers as a direct draw."""
    cfg = LinkConfig(symbol_rate=FB, tx=TxConfig(clock=ClockConfig(rj_ui=0.01)))
    a = edge_jitter_seq(1000, cfg, np.random.default_rng(7))
    b = np.random.default_rng(7).normal(scale=0.01 * UI, size=1001)
    assert np.array_equal(a, b)


def test_a_profile_clock_consumes_the_generator_before_the_white_terms(tmp_path):
    path = _write(tmp_path, _flat_profile(FB, 0.005))
    cfg = LinkConfig(symbol_rate=FB, tx=TxConfig(clock=ClockConfig(
        kind="profile", file=path, rj_ui=0.01)))
    rng = np.random.default_rng(7)
    jit = edge_jitter_seq(1000, cfg, rng)
    # not the white-only sequence, and the generator has moved further
    assert not np.array_equal(jit, np.random.default_rng(7).normal(scale=0.01 * UI, size=1001))
    assert rng.bit_generator.state != np.random.default_rng(7).bit_generator.state


# ---------------------------------------------------------- synthesis

def test_a_flat_profile_is_white_pm_at_the_equivalent_rj():
    """The acceptance bar: sigma within 3% of the rj_ui the profile encodes,
    over 1e5 edges (the estimator's own scatter is ~0.2%)."""
    rj_ui = 0.01
    prof = _flat_profile(FB, rj_ui)
    t = prof.edge_offsets_s(100_000, UI, np.random.default_rng(1))
    assert t.std() / UI == pytest.approx(rj_ui, rel=0.03)
    assert abs(t.mean()) < 0.05 * t.std()          # DC bin zeroed: no offset


def test_one_over_f2_is_a_random_walk():
    """Var[phi(k+m) - phi(k)] grows linearly in m -- the signature of white FM."""
    prof = _slope_profile(FB, l1_dbc=-80.0, f1=1e6)
    t = prof.edge_offsets_s(100_000, UI, np.random.default_rng(2))
    lags = np.array([1, 2, 4, 8, 16, 32, 64])
    var = np.array([np.var(t[m:] - t[:-m]) for m in lags])
    slope, intercept = np.polyfit(lags, var, 1)
    fit = slope * lags + intercept
    r2 = 1.0 - np.sum((var - fit) ** 2) / np.sum((var - var.mean()) ** 2)
    assert slope > 0 and r2 > 0.99, (var / var[0], r2)


def test_pure_one_over_f2_realises_the_bin_sum_which_is_pi2_over_6_above_the_integral():
    """Why "within 10% of integrate_pn" is not a statement one can make about
    a single pure-1/f^2 run, written down so nobody relaxes a tolerance to
    make it pass: the synthesiser's expectation is the FFT bin sum
    sum_k S(k df) df; for 1/f^2 that is sum 1/k^2 = pi^2/6 times the integral
    from df. Over 100 fixed seeds the ensemble lands on the bin sum; any one
    seed scatters by tens of percent because the two lowest bins hold most of
    the power and each is one chi^2(2) draw."""
    n = 20_000
    prof = _slope_profile(FB, l1_dbc=-70.0, f1=1e6)
    fk = np.arange(1, n // 2 + 1) * FB / n
    bin_sum = float(np.sum(prof.s_phi(fk)) * FB / n)
    integral = integrate_pn(prof.f_hz, sphi_from_ldbc(prof.l_dbc_hz), FB / n, FB / 2)
    assert bin_sum / integral == pytest.approx(math.pi ** 2 / 6, rel=0.02)
    phase_var = [np.var(prof.edge_offsets_s(n, UI, np.random.default_rng(s)) * TWOPI * FB)
                 for s in range(100)]
    assert np.mean(phase_var) == pytest.approx(bin_sum, rel=0.15)
    assert np.std(phase_var) / np.mean(phase_var) > 0.3     # the scatter is real


def test_a_loop_shaped_profile_matches_integrate_pn_in_one_run():
    """What a real PLL looks like -- flat inside the loop bandwidth -- has
    neither problem of the pure case: one run's sigma is within 10% of the
    profile's integral over [1/(N UI), f0/2]."""
    n = 100_000
    prof = _slope_profile(FB, l1_dbc=-70.0, f1=1e6, corner=5e6)
    t = prof.edge_offsets_s(n, UI, np.random.default_rng(4))
    ref = math.sqrt(integrate_pn(prof.f_hz, sphi_from_ldbc(prof.l_dbc_hz), FB / n, FB / 2)) / (TWOPI * FB)
    assert t.std() == pytest.approx(ref, rel=0.10)


def test_a_spur_is_a_sideband_dbc_below_the_carrier():
    """-60 dBc at 100 MHz, bin-centred so the FFT reads the amplitude
    exactly: 20 log10(A/2) must come back as -60 within 0.1 dB. This pins the
    producer's definition (pll_simulator: 20*log10(dphi/2)); the user-facing
    "sqrt(2 * 10^(dbc/10))" is the same tone's RMS phase."""
    n = 16_000                                  # df = 1 MHz -> 100 MHz is bin 100
    silent = ClockProfile(f0_hz=FB, f_hz=np.array([100.0, FB / 2]),
                          l_dbc_hz=np.array([-300.0, -300.0]), spurs=((100e6, -60.0),))
    t = silent.edge_offsets_s(n, UI, np.random.default_rng(5))
    phi = t * TWOPI * FB
    spec = np.abs(np.fft.rfft(phi)) * 2.0 / n
    k = int(np.argmax(spec))
    assert np.fft.rfftfreq(n, UI)[k] == pytest.approx(100e6)
    a_peak = spec[k]
    assert 20.0 * math.log10(a_peak / 2.0) == pytest.approx(-60.0, abs=0.1)
    assert a_peak / math.sqrt(2.0) == pytest.approx(math.sqrt(2.0 * 10 ** (-60 / 10)), rel=0.02)
    assert np.ptp(t) == pytest.approx(2.0 * a_peak / (TWOPI * FB), rel=0.02)


def test_nothing_is_added_above_the_clocks_own_nyquist():
    """A 4 GHz PLL driving a 16 GBd link: the profile ends at 2 GHz, and the
    synthesised phase spectrum must be empty above it rather than hold the
    floor out to 8 GHz."""
    f0 = 4e9
    prof = ClockProfile(f0_hz=f0, f_hz=np.array([100.0, f0 / 2]), l_dbc_hz=np.array([-120.0, -120.0]))
    n = 65_536
    t = prof.edge_offsets_s(n, UI, np.random.default_rng(6))
    f = np.fft.rfftfreq(n, UI)
    p = np.abs(np.fft.rfft(t)) ** 2
    above = p[f > f0 / 2 * 1.01].sum()
    below = p[(f > 0) & (f <= f0 / 2)].sum()
    assert above < 1e-9 * below


def test_f0_scales_the_seconds_and_nothing_else():
    """Half the carrier, same dBc/Hz: twice the seconds, identical phase.
    This is the whole reason the file carries f0 instead of leaving the
    reader to use 1/UI."""
    f = np.geomspace(100.0, 1e9, 200)
    l_dbc = -100.0 - 20.0 * np.log10(f / 1e6)
    full = ClockProfile(f0_hz=2e9, f_hz=f, l_dbc_hz=l_dbc)
    half = ClockProfile(f0_hz=1e9, f_hz=f, l_dbc_hz=l_dbc)
    t_full = full.edge_offsets_s(4096, UI, np.random.default_rng(8))
    t_half = half.edge_offsets_s(4096, UI, np.random.default_rng(8))
    assert np.allclose(t_half, 2.0 * t_full)


# ------------------------------------------------------ through the engine

def test_tx_stage_tie_matches_the_profile_integral(tmp_path):
    """calc_jitter on the Tx waveform of a real run, loop-shaped profile: the
    total TIE sigma is within 10% of integrate_pn over [1/(N UI), f0/2]."""
    from halo_serdes.channel import ChannelModel
    from halo_serdes.engine import run_time_link

    prof = _slope_profile(FB, l1_dbc=-70.0, f1=1e6, corner=5e6)
    path = _write(tmp_path, prof)
    n_sym = 127 * 400
    cfg = _nrz_link(path, n_sym)
    res = run_time_link(cfg, channel=ChannelModel.from_config(cfg), collect_jitter=True)
    jr = res.extras["jitter_budget"]["tx"]
    ref = math.sqrt(integrate_pn(prof.f_hz, sphi_from_ldbc(prof.l_dbc_hz),
                                 FB / (n_sym + 1), FB / 2)) / (TWOPI * FB)
    assert np.std(jr.tie) == pytest.approx(ref, rel=0.10)


def test_calc_jitter_files_coloured_jitter_under_pj_not_rj(tmp_path):
    """Known limit of the acceptance instrument, pinned so phase 2 does not
    plot "Rj" against the profile and chase a ghost: the spectral split flags
    the lowest bins of a 1/f^2 spectrum as periodic, and Rj collapses to a
    few percent of the true sigma. analysis/jitter.py is left as it is -- it
    is the judge here, and the judge is not edited to fit the case."""
    from halo_serdes.channel import ChannelModel
    from halo_serdes.engine import run_time_link

    prof = _slope_profile(FB, l1_dbc=-70.0, f1=1e6, corner=5e6)
    path = _write(tmp_path, prof)
    cfg = _nrz_link(path, 127 * 80)
    jr = run_time_link(cfg, channel=ChannelModel.from_config(cfg),
                       collect_jitter=True).extras["jitter_budget"]["tx"]
    assert jr.rj < 0.5 * np.std(jr.tie)
    assert jr.pj > np.std(jr.tie)


def test_a_spur_comes_back_as_a_pj_line_at_its_offset(tmp_path):
    """-20 dBc at 100 MHz through the engine: the TIE spectrum peaks at
    100 MHz and calc_jitter's Pj (pk-pk) is within 1 dB of 2A.

    -20 rather than -60 because the instrument, not the synthesis, sets the
    floor: calc_jitter finds crossings by linear interpolation across the
    area-conserving edge sample, and for a tone a hundredth of a sample wide
    sitting on grid-aligned edges that reads back at half amplitude (-6 dB,
    measured). At -20 dBc the tone is ~1 sample at osr=32 and the detector
    is linear to better than a dB. The convention itself is pinned at -60 dBc
    on the synthesised phase above, where no detector is involved."""
    from halo_serdes.analysis.jitter import find_crossings, tie_from_crossings
    from halo_serdes.channel import ChannelModel
    from halo_serdes.engine import make_pattern, run_time_link
    from halo_serdes.tx.builder import symbols_to_voltages
    from halo_serdes.tx.jitter import build_jittered_tx

    dbc = -20.0
    silent = ClockProfile(f0_hz=FB, f_hz=np.array([100.0, FB / 2]),
                          l_dbc_hz=np.array([-300.0, -300.0]), spurs=((100e6, dbc),))
    path = _write(tmp_path, silent)
    n_sym = 127 * 160
    cfg = _nrz_link(path, n_sym)
    res = run_time_link(cfg, channel=ChannelModel.from_config(cfg), collect_jitter=True)
    a_t = 2.0 * 10 ** (dbc / 20.0) / (TWOPI * FB)
    jr = res.extras["jitter_budget"]["tx"]
    assert 20.0 * math.log10(jr.pj / (2.0 * a_t)) == pytest.approx(0.0, abs=1.0)

    # where the line is: the Tx waveform the engine would build, TIE placed
    # at its UI slot, FFT at the edge rate
    v = symbols_to_voltages(make_pattern(cfg), cfg)
    wave, _ = build_jittered_tx(v, cfg, np.random.default_rng(cfg.sim.seed))
    xings = find_crossings(wave.y, cfg.dt, 0.0)
    tie, slots = tie_from_crossings(xings, UI)
    grid = np.zeros(n_sym + 1)
    grid[np.clip(slots, 0, n_sym)] = tie
    spec = np.abs(np.fft.rfft(grid - grid.mean()))
    spec[0] = 0.0
    f_peak = np.fft.rfftfreq(grid.size, UI)[int(np.argmax(spec))]
    assert f_peak == pytest.approx(100e6, abs=FB / grid.size * 1.5)


@pytest.mark.parametrize("path", PROFILES, ids=lambda p: p.stem)
def test_every_bundled_profile_runs_the_pam4_112g_link(path):
    """Load, resolve, synthesise, run: a short ADC-based 112 GBd link on each
    shipped clock, BER finite. The BER itself is not asserted -- 6000 symbols
    is a smoke length -- the point is that every file goes end to end."""
    from halo_serdes.channel import ChannelModel
    from halo_serdes.engine import run_time_link
    from halo_serdes_app.config_bridge import load_preset

    base = load_preset("PAM4 224G ADC (112 GBd stress)")
    cfg = dataclasses.replace(
        base,
        tx=dataclasses.replace(base.tx, clock=ClockConfig(kind="profile", file=str(path))),
        sim=dataclasses.replace(base.sim, n_symbols=6000))
    res = run_time_link(cfg, channel=ChannelModel.from_config(base))
    assert math.isfinite(res.ber.ber) and 0.0 <= res.ber.ber <= 1.0
    assert res.n_symbols > 0

