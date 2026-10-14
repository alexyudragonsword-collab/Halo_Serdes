"""Mueller-Muller loop: how far its pull back reaches, against what moves the loop.

The linear loop model reads the detector's slope at the lock point and
assumes it holds at any error. On the fingerprint's 53 Gb/s link the timing
function turns over 0.026 UI past the lock point; on random symbols the
time engine's loop got over it and ran away, and the model read 0.035-0.117
UI without a word (ROADMAP P2 #26). The statistical engine now reports the
turnover's distance and warns when it is under 8x the loop's own wander;
the time engine is the referee."""

import dataclasses
import warnings
from pathlib import Path

import numpy as np
import pytest

from halo_serdes.cdr.linear import mm_hold_range_ui
from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import ChannelConfig, ClockConfig, RxConfig, SimConfig, TxConfig
from halo_serdes.engine import run_time_link
from halo_serdes.engine.cascade import initial_ffe_taps
from halo_serdes.engine.statistical import run_statistical

PROFILE = Path(__file__).resolve().parents[1] / "data" / "clock_profiles" / \
    "bench_markulic16_sspll_40m_10p24g.yaml"


def _stat(cfg: LinkConfig, cm: ChannelModel):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        st = run_statistical(cfg, channel=cm, ffe_taps=initial_ffe_taps(cfg, cm),
                             ffe_pre=cfg.rx.ffe.n_pre)
    return st.extras["clock_loop"], [w for w in caught if "pull back" in str(w.message)]


def _excursion_ui(cfg: LinkConfig, cm: ChannelModel) -> tuple[float, float]:
    """Largest distance the time engine's sampling phase went from its start [UI], and BER."""
    t = run_time_link(cfg, channel=cm)
    ph = np.asarray(t.extras["phase_track"], dtype=float)
    e = (ph - np.arange(ph.size) * cfg.osr - ph[0]) / cfg.osr
    return float(np.max(np.abs(e))), t.ber.n_errors / t.ber.n_checked


def test_a_triangle_pulls_back_until_a_neighbour_flips_a_sign():
    """A pulse with ISI on its two neighbours only: shifted by tau UI the
    sample is (1 - tau) a_n + tau a_(n+1), and the detector's mean output
    is tau for as long as the neighbour's sign is that symbol's own. NRZ
    keeps it to half a UI (0.41 on a 16-sample grid, the parabola through
    the last samples); PAM4's inner level loses its sign to a full-swing
    neighbour from tau = 1/4."""
    osr = 16
    tri = np.concatenate([np.zeros(10 * osr), 1 - np.abs(np.arange(-osr, osr + 1)) / osr,
                          np.zeros(10 * osr)])
    pk = int(np.argmax(tri))
    nrz = mm_hold_range_ui(tri, osr, pk, np.array([-1.0, 1.0]), 0.02)
    pam4 = mm_hold_range_ui(tri, osr, pk, np.array([-3.0, -1.0, 1.0, 3.0]) / 3.0, 0.02)
    assert 0.35 < nrz <= 0.5
    assert 0.15 < pam4 < 0.25


def _synthetic(pattern: str) -> LinkConfig:
    baud = 26.5625e9
    return LinkConfig(
        modulation="pam4", symbol_rate=baud, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.2),
        tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.72, -0.2), fir_n_pre=1, bw=40e9,
                    clock=ClockConfig(kind="profile", file=str(PROFILE), f0_hz=baud)),
        rx=RxConfig(arch="adc_dsp", noise_rms=2e-3),
        sim=SimConfig(n_symbols=60_000, seed=1, pattern=pattern))


@pytest.mark.parametrize("pattern,runs_away", [("prbs31q", True), ("prbs7q", False)])
def test_the_warning_follows_what_moves_the_loop(pattern, runs_away):
    """The fingerprint's 53 Gb/s link, turnover 0.026 UI out. Random symbols
    (PRBS31Q) put 0.059 UI of self-noise on the loop, 0.4x: warned, and the
    time engine runs ~10 UI away within 60k symbols on every seed tried.
    PRBS7Q's data noise is lines far above the loop: 0.0022 UI, 12x, no
    warning, held to 0.012 UI. Measured over 4 seeds x 200k symbols, PRBS7Q-15Q
    and kp_shift 4-10 on this profile clock: lost at 2.8-6.9x, never from 8.6x."""
    cfg = _synthetic(pattern)
    cm = ChannelModel.from_config(cfg)
    sol, warned = _stat(cfg, cm)
    assert sol.hold_range_ui == pytest.approx(0.026, abs=0.003)
    assert bool(warned) == runs_away
    ratio = sol.hold_range_ui / sol.sigma_wander_ui
    assert (ratio < 1.0) if runs_away else (ratio > 8.0)
    excursion, ber = _excursion_ui(cfg, cm)
    if runs_away:
        assert excursion > 1.0 and ber > 1e-2
    else:
        assert excursion < 0.05 and ber == 0.0


def test_a_preset_holds_far_inside_its_pull_back():
    """The 224G stress preset turns over 0.114 UI out, 21x its own wander on
    random symbols: no warning, and the time loop stays within 0.02 UI. The
    clock jitter above the loop's bandwidth does not
    count -- the loop does not follow it, so it cannot carry it over; with it,
    example 31's 200 fs clocks read 1.6-6.7x and warned on every loop setting
    while their time engine held."""
    from halo_serdes_app.config_bridge import load_preset

    base = load_preset("PAM4 224G ADC (112 GBd stress)")
    cfg = dataclasses.replace(
        base, tx=dataclasses.replace(base.tx, clock=ClockConfig(
            kind="profile", file=str(PROFILE), f0_hz=base.symbol_rate)),
        sim=dataclasses.replace(base.sim, pattern="prbs31q", n_symbols=100_000, seed=1))
    cm = ChannelModel.from_config(cfg)
    sol, warned = _stat(cfg, cm)
    assert sol.hold_range_ui == pytest.approx(0.114, abs=0.006)
    assert sol.hold_range_ui > 15 * sol.sigma_wander_ui
    assert not warned
    excursion, _ = _excursion_ui(cfg, cm)
    assert excursion < 0.02
