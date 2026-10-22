"""The bang-bang loop hunts on any clock: its own wander in the statistical engine.

Until ROADMAP P2 #27 a white clock got no loop model, so the statistical
engine read the mixed-signal receiver at a still sampling instant while the
time engine's loop hunted 0.02-0.06 UI: 0.1-0.5x on the NRZ 16G preset with
transmit RJ. Now the loop model runs on white clocks too, a PAM4 detector's
transitions are taken by class, and the smear holds the DFE where the
receiver adapted it. The time engine is the referee."""

import dataclasses

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel
from halo_serdes.config.schema import ClockConfig
from halo_serdes.engine import run_time_link
import halo_serdes.engine.statistical as st


def _preset(name, rj=0.0, noise=None, n_symbols=100_000, seed=1):
    from halo_serdes_app.config_bridge import load_preset

    cfg = load_preset(name)
    return dataclasses.replace(
        cfg, tx=dataclasses.replace(cfg.tx, clock=ClockConfig(rj_ui=rj)),
        rx=cfg.rx if noise is None else dataclasses.replace(cfg.rx, noise_rms=noise),
        sim=dataclasses.replace(cfg.sim, n_symbols=n_symbols, seed=seed))


def _transitions(cfg, channel, monkeypatch):
    """The detector's classes on the pulse the engine hands its loop model."""
    seen = {}
    orig = st._sampling_jitter_ui

    def grab(*a, **kw):
        seen["a"] = a
        return orig(*a, **kw)

    monkeypatch.setattr(st, "_sampling_jitter_ui", grab)
    stat = st.run_statistical(cfg, channel=channel)
    _, pulse, levels_norm, swing, noise = seen["a"][:5]
    return stat, st._bb_transitions(pulse, cfg.osr, noise, swing, levels_norm)


def test_nrz_is_one_class_and_pam4_carries_timing_on_half_its_sign_changes(monkeypatch):
    """NRZ: every transition crosses zero at the lock point, one class of
    probability 1/2 -- the model as it was. PAM4: half of the sign changes
    (+-1 <-> -+1/3) cross 0.14-0.15 UI off it, a third of a UI's worth of
    their edge noise away, so they bring noise and next to no gain; the
    symmetric ones (+-1, +-1/3) carry the timing, the inner pair with 3x the
    edge noise."""
    nrz = _preset("NRZ 16G mixed-signal")
    _, cl = _transitions(nrz, ChannelModel.from_config(nrz), monkeypatch)
    assert len(cl) == 1 and cl[0][0] == 0.5 and cl[0][1] == 0.0
    pam4 = _preset("PAM4 32G mixed-signal")
    _, cl = _transitions(pam4, ChannelModel.from_config(pam4), monkeypatch)
    assert sum(p for p, _, _ in cl) == pytest.approx(0.5)
    at_lock = sorted(e for p, o, e in cl if o == 0.0)
    off = [o for p, o, e in cl if o != 0.0]
    assert len(at_lock) == 2 and at_lock[1] / at_lock[0] == pytest.approx(3.0, rel=0.05)
    assert len(off) == 2 and min(abs(o) for o in off) > 0.1
    assert sorted(np.sign(off)) == [-1.0, 1.0]           # balanced either side of the lock


@pytest.mark.parametrize("name, lo, hi", [
    ("NRZ 16G mixed-signal", 0.9, 1.15),
    ("NRZ 28G analytic (COM/xtalk)", 0.9, 1.15),
    ("PAM4 32G mixed-signal", 0.9, 1.15),
])
def test_the_model_hunts_as_the_kernel_does(name, lo, hi):
    """White clocks, the presets' own loops: the model's hunting against the
    time engine's sampling phase over the second half of 100k symbols. NRZ
    1.02-1.04x; PAM4 1.01x, its off-lock sign changes' data-set output taken
    through the pattern's lines (1.23x counted white, ROADMAP P2 #30; taking
    every sign change as a transition at the lock read 0.65-0.72x)."""
    cfg = _preset(name)
    cm = ChannelModel.from_config(cfg)
    sol = st.run_statistical(cfg, channel=cm).extras["clock_loop"]
    assert sol is not None and sol.sigma_untracked_ui == 0.0
    t = run_time_link(cfg, channel=cm)
    ph = np.asarray(t.extras["phase_track"], dtype=float)
    wander = float(((ph - np.arange(ph.size) * cfg.osr) / cfg.osr)[ph.size // 2:].std())
    assert lo < sol.sigma_self_ui / wander < hi, (sol.sigma_self_ui, wander)


@pytest.mark.parametrize("name, rj, noise", [
    ("NRZ 16G mixed-signal", 0.06, 0.018),
    ("PAM4 32G mixed-signal", 0.06, 0.004),
    ("NRZ 28G analytic (COM/xtalk)", 0.06, 0.03),
])
def test_two_engines_agree_where_the_loop_hunts(name, rj, noise):
    """Invariant 3 on steep bathtubs, where the hunting matters: two seeds of
    200k symbols, 0.06 UI of transmit RJ. Measured 0.78 / 1.01 / 0.98x
    (0.35 / 0.48 / 0.88x with the loop left out); over 0-0.09 UI of RJ and
    two noise levels, 0.45-1.08x on the cases with 20 errors or more (0.78 /
    1.03 / 1.02x and 0.50-1.12x before the curve was taken between samples,
    ROADMAP P2 #28). The rest on NRZ 16G was the sampler's own phase
    distribution against a Gaussian of the same sigma: exponential, late-heavy
    tails from the saturating detector, which the read is smeared with since
    2026-10-21 (0.84 -> 0.91x at 18 mV, 0.64 -> 0.78x at 15 mV / 0.04 UI, on 800k
    symbols -- too few errors in 400k to sit here; ROADMAP P2 #32)."""
    cfg = _preset(name, rj=rj, noise=noise, n_symbols=200_000)
    cm = ChannelModel.from_config(cfg)
    n_err = n_chk = 0
    for seed in (1, 2):
        t = run_time_link(dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, seed=seed)),
                          channel=cm)
        n_err += t.ber.n_errors
        n_chk += t.ber.n_checked
    assert n_err > 100, n_err
    stat = st.run_statistical(cfg, channel=cm)
    ratio = stat.ber / (n_err / n_chk)
    assert 0.5 < ratio < 2.0, (ratio, stat.ber, n_err)


def test_the_smear_holds_the_dfe_where_it_adapted(monkeypatch):
    """The loop wanders far faster than the sign-sign LMS adapts, so under
    the smear the DFE stays at the taps of the phase read and the postcursors
    it misses elsewhere stay ISI; the bathtub re-adapts at every phase. On the
    NRZ 16G preset (four taps) the curve the smear reads lies 3-27x above the
    re-adapted bathtub 4-16 samples early, and the read, taken the same way
    from both, is higher with the taps held -- by 5 % here: the smear's
    weight sits on the late, steep side, where holding the taps changes
    little."""
    cfg = _preset("NRZ 16G mixed-signal", rj=0.06, noise=0.012, n_symbols=200_000)
    seen = []
    orig = st._smeared_with
    monkeypatch.setattr(st, "_smeared_with", lambda c, p, ce, u, d: (
        seen.append((np.array(c), np.array(p), ce, np.array(u), np.array(d))),
        orig(c, p, ce, u, d))[1])
    s = st.run_statistical(cfg)
    held, x, lock, u, dens = seen[0]        # the density smear since P2 #32 (a Gaussian before)
    # the bathtub unsmeared: the DFE re-adapted at every phase
    monkeypatch.setattr(st, "_sampling_jitter_ui", lambda *a, **k: (0.0, None))
    still = st.run_statistical(cfg)
    readapted = np.interp(x, np.asarray(still.phi_ui) * cfg.osr, still.ber_phi)
    early = x - lock <= -4.0
    assert np.all(held[early] > 3.0 * readapted[early]), (held[early] / readapted[early]).min()
    assert s.ber > 1.02 * orig(readapted, x, lock, u, dens), (s.ber, orig(readapted, x, lock, u, dens))
