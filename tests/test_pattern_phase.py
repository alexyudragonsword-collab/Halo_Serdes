"""The pattern-phase factor: a free-running bang-bang loop errs on the data
that drove it late, which a phase-independent bathtub does not hold
(ROADMAP P3 #13; cairn/DSP发端与PR.md §33)."""
import dataclasses

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel
from halo_serdes.engine import run_time_link
from halo_serdes.engine import statistical as st
from halo_serdes_app import config_bridge as cb


def _preset(noise, rj, name="NRZ 16G mixed-signal", **sim):
    cfg = cb.load_preset(name)
    cfg = dataclasses.replace(
        cfg, rx=dataclasses.replace(cfg.rx, noise_rms=noise),
        tx=dataclasses.replace(cfg.tx, clock=dataclasses.replace(cfg.tx.clock, rj_ui=rj)))
    return dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, **sim)) if sim else cfg


def _run_lengths(history):
    """Per history: the run of the sampled level ending at it and the run of
    the other level before that, within the window."""
    n_hist = history.shape[1]
    out = np.zeros((history.shape[0], 2), dtype=int)
    for k, row in enumerate(history):
        lb = 0
        while lb < n_hist and row[lb] > 0:
            lb += 1
        la = 0
        while lb + la < n_hist and row[lb + la] < 0:
            la += 1
        out[k] = lb, la
    return out


def test_the_factor_is_what_the_kernel_measured_and_fades_with_noise():
    """NRZ 16G at 12 / 15 / 18 mV + 0.03 / 0.04 / 0.06 UI, on independent
    random data (ROADMAP P3 #15): the free-running kernel errs 1.47 / 1.24 /
    1.00x this engine before the factor (20 / 5 / 3 seeds of 1.6M symbols,
    171 / 348 / 1824 errors); the chain with the loop's integral path gives
    1.35 / 1.18 / 1.05x (the engine then 0.92 / 0.95 / 1.05x the kernel).
    On PRBS31 the kernel errs 1.54 / 1.16 / 1.03x more than on random data."""
    f = []
    for noise, rj in ((0.012, 0.03), (0.015, 0.04), (0.018, 0.06)):
        cfg = _preset(noise, rj)
        s = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg))
        f.append(s.extras["pattern_phase"].factor)
    assert 1.2 < f[0] < 1.5 and 1.08 < f[1] < 1.3 and 1.0 < f[2] < 1.12, f
    assert f[0] > f[1] > f[2], f


def test_deep_the_factor_follows_the_long_run_classes_tails():
    """ROADMAP P3 #14-#16: at 8 mV + 0.019 UI on independent random data
    the kernel (80 seeds of 1.6M symbols, 32 errors) read 1.29x the engine
    before the factor; the chain with the integral path (second-order
    fluxes) gives 1.13, the engine 0.88x the kernel. Its class with the
    errors (a run of 2-3 after 6 or more of the other level, a transition
    next): P(phase > 8.5 samples) 4.2e-5 against the kernel's 6.4e-5."""
    cfg = _preset(0.008, 0.019)
    pp = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg)).extras["pattern_phase"]
    assert 0.95 < pp.factor < 1.35, pp.factor
    assert pp.converged and pp.steps < 600
    lbla = _run_lengths(pp.history)
    worst = ~pp.next_same & np.isin(lbla[:, 0], (1, 2)) & (lbla[:, 1] >= 6)
    du = float(pp.phase_grid[1] - pp.phase_grid[0])
    late = pp.phase_grid > 8.5
    tail = float(pp.cond_density[worst][:, late].sum(axis=1).mean() * du)
    everyone = float(pp.cond_density[:, late].sum(axis=1).mean() * du)
    assert 2e-5 < tail < 1.2e-4 and tail > 5 * everyone, (tail, everyone)


def test_below_one_where_the_stationary_density_runs_heavy():
    """NRZ 28G analytic at 10 mV: the kernel's phase (10 seeds of 1.6M
    symbols, independent data) has P(> 2 samples) 2e-5, the engine's
    stationary density 1.6e-4, the chain 1e-6 -- the factor carries that
    marginal, and reads 0.13 (until 2026-10-26 floored at 1)."""
    cfg = cb.load_preset("NRZ 28G analytic (COM/xtalk)")
    cfg = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=0.010))
    pp = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg)).extras["pattern_phase"]
    assert pp.factor < 0.3 and pp.converged, pp.factor
    assert pp.factor == pp.factor_raw


def test_the_shift_follows_the_run_before_the_short_run():
    """A run of 2-3 of the sampled level after La of the other, a transition
    next: the kernel's mean phase -0.51 (La 1), +0.23 (2), +0.87 to +1.20
    samples (3 to 7); the model -0.55 / +0.36 / +0.84 to +1.07. Over all
    histories the shift averages to zero (it is relative to the lock)."""
    cfg = _preset(0.012, 0.03)
    pp = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg)).extras["pattern_phase"]
    n_hist = pp.history.shape[1]
    lbla = _run_lengths(pp.history)
    opp = ~pp.next_same

    def mean_shift(la):
        m = opp & np.isin(lbla[:, 0], (2, 3)) & (lbla[:, 1] == la) & (lbla[:, 0] + la < n_hist)
        return float(pp.shift[m].mean())

    assert mean_shift(1) < -0.3, mean_shift(1)
    assert 0.1 < mean_shift(2) < 0.6, mean_shift(2)
    assert 0.6 < mean_shift(3) < mean_shift(5) < 1.3, (mean_shift(3), mean_shift(5))
    assert abs(float(pp.shift.mean())) < 0.2      # the chain's own marginal, not quite centred
    # the conditional densities are densities on the grid, their mixture the stationary one
    du = float(pp.phase_grid[1] - pp.phase_grid[0])
    assert np.allclose(pp.cond_density.sum(axis=1) * du, 1.0, atol=1e-4)
    assert pp.density.sum() * du == pytest.approx(1.0, rel=1e-6)


def test_the_read_is_the_independent_one_times_the_factor(monkeypatch):
    cfg = _preset(0.015, 0.04)
    cm = ChannelModel.from_config(cfg)
    with_f = st.run_statistical(cfg, channel=cm)
    orig = st._pattern_phase_factor
    monkeypatch.setattr(st, "_pattern_phase_factor",
                        lambda *a, **k: dataclasses.replace(orig(*a, **k), factor=1.0))
    without = st.run_statistical(cfg, channel=cm)
    factor = with_f.extras["pattern_phase"].factor
    assert factor > 1.08
    assert with_f.ber / without.ber == pytest.approx(factor, rel=1e-9)
    assert with_f.ser / without.ser == pytest.approx(factor, rel=1e-9)


def test_a_loop_that_does_not_move_has_no_correlation(monkeypatch):
    """kp 0: the votes move nothing, every class keeps the stationary density,
    the two reads are one."""
    cfg = _preset(0.012, 0.03)
    cap = {}
    orig = st._pattern_phase_factor

    def spy(*a, **k):
        cap["a"], cap["k"] = a, k
        return orig(*a, **k)

    monkeypatch.setattr(st, "_pattern_phase_factor", spy)
    st.run_statistical(cfg, channel=ChannelModel.from_config(cfg))
    a = list(cap["a"])
    assert a[9] > 0.0           # kp [samples per vote]
    a[9] = 0.0
    k = dict(cap["k"], ki=0.0)  # and no integral path
    still = orig(*a, **k)
    assert still.factor == pytest.approx(1.0, abs=1e-4)      # float32 chain
    assert np.ptp(still.shift) < 1e-4           # every class at the stationary density's mean
    assert still.read_joint == pytest.approx(still.read_indep, rel=1e-4)


def test_the_fused_step_is_the_numpy_step_bit_for_bit(monkeypatch):
    """The chain's step as one loop (cdr/pattern_chain.py) gives the numpy
    step's arrays exactly: its pure-Python copy on a short chain here, the
    jitted one on the engine's own when numba is there. Without numba the
    engine runs the numpy step (invariant 4)."""
    from halo_serdes.cdr import pattern_chain as pc

    cfg = _preset(0.012, 0.03)
    cap = {}
    orig = st._pattern_phase_factor

    def spy(*a, **k):
        cap["a"], cap["k"] = a, k
        return orig(*a, **k)

    monkeypatch.setattr(st, "_pattern_phase_factor", spy)
    st.run_statistical(cfg, channel=ChannelModel.from_config(cfg))
    a, k = cap["a"], cap["k"]
    runs = [(dict(k, n_hist=4, n_steps=4), pc.chain_step_py)]
    if pc.chain_step is not None:
        runs.append((dict(k), pc.chain_step))
    for kw, kern in runs:
        st._PATTERN_PHASE_CACHE.clear()
        ref = orig(*a, **kw, kernel="numpy")
        st._PATTERN_PHASE_CACHE.clear()
        got = orig(*a, **kw, kernel=kern)
        assert got.steps == ref.steps
        assert np.array_equal(got.cond_density, ref.cond_density)
        assert np.array_equal(got.density, ref.density)
        assert got.factor == ref.factor and got.factor_marginal == ref.factor_marginal
    st._PATTERN_PHASE_CACHE.clear()


def test_it_applies_to_nrz_with_a_dfe_under_a_bang_bang_loop_only():
    pam4 = cb.load_preset("PAM4 32G mixed-signal")
    assert st.run_statistical(pam4, channel=ChannelModel.from_config(pam4)).extras["pattern_phase"] is None
    adc = cb.load_preset("PAM4 224G ADC (106 GBd)")
    assert st.run_statistical(adc).extras["pattern_phase"] is None
    cfg = _preset(0.015, 0.04)
    no_dfe = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, dfe=dataclasses.replace(cfg.rx.dfe, n_taps=0)))
    assert st.run_statistical(no_dfe, channel=ChannelModel.from_config(no_dfe)).extras["pattern_phase"] is None
    assert st.run_statistical(cfg, channel=ChannelModel.from_config(cfg)).extras["pattern_phase"] is not None


@pytest.mark.needs_jit
def test_two_engines_agree_where_the_loop_errs_on_its_own_data():
    """Invariant 3 at the point the residual was registered from: NRZ 16G at
    15 mV + 0.04 UI, on independent random data -- what this engine models
    (on PRBS31 the free-running kernel errs 1.16x more here and up to 4x more
    at 8 mV, ROADMAP P3 #15). Two seeds of 1M symbols (about 90 errors); five
    seeds of 1.6M read the engine 0.95x with the factor (0.81x without)."""
    cfg = _preset(0.015, 0.04, n_symbols=1_000_000)
    cm = ChannelModel.from_config(cfg)
    n_err = n_chk = 0
    for seed in (3, 4):
        symbols = np.random.default_rng(1000 + seed).integers(0, 2, cfg.sim.n_symbols)
        t = run_time_link(dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, seed=seed)),
                          channel=cm, symbols=symbols)
        n_err += t.ber.n_errors
        n_chk += t.ber.n_checked
    assert n_err > 50, n_err
    s = st.run_statistical(cfg, channel=cm)
    ratio = s.ber / (n_err / n_chk)
    assert 0.7 < ratio < 1.5, (ratio, s.ber, n_err)
    assert 1.08 < s.extras["pattern_phase"].factor < 1.3


def test_crosstalk_is_noise_to_the_factor_too(monkeypatch):
    """The aggressors' variance goes into the factor's Gaussian rest, of the
    votes and of the decisions alike (example 44's NRZ 28G lanes read
    1.23-1.32x the time engine without it). NRZ 16G at 12 mV + 0.03 UI, 2 FEXT
    + 2 NEXT, on independent data: the factor 1.35 quiet, 1.25 / 1.32 at -27 /
    -33 dB (skew 0.5 UI), and the engine 1.05 / 1.07x the time engine with
    it, 0.84 / 0.81x without (two seeds of 400k symbols, 13257 / 260
    errors)."""
    from halo_serdes.config.schema import CrosstalkConfig
    cfg = _preset(0.012, 0.03)
    quiet = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg)).extras["pattern_phase"].factor
    assert quiet > 1.2, quiet
    cap = {}
    orig = st._pattern_phase_factor

    def spy(*a, **k):
        cap["xtalk_var"] = k.get("xtalk_var")
        return orig(*a, **k)

    monkeypatch.setattr(st, "_pattern_phase_factor", spy)
    loud = dataclasses.replace(cfg, crosstalk=CrosstalkConfig(n_fext=2, n_next=2, coupling_db=-27.0, seed=1, skew_ui=0.5))
    with_x = st.run_statistical(loud, channel=ChannelModel.from_config(loud)).extras["pattern_phase"].factor
    assert cap["xtalk_var"] is not None
    assert 1.1 < with_x < 1.45 and with_x < quiet, (with_x, quiet)
    # the variance the closure reports is of the couplings' size, not zero
    v = np.asarray(cap["xtalk_var"](np.arange(4.0)))
    assert np.all(v > (0.005) ** 2), v


def test_the_models_own_read_is_the_engines_before_the_factor():
    """The factor is a ratio of the model's reads, so the model's bathtub has
    to be the engine's: with the edge-jitter tables read in their bin order
    (this symbol, next, previous) and the error-propagation chain, its
    independent read is 0.92 / 0.99 / 0.98x the engine's read before the
    factor at 12 / 15 / 18 mV (0.3-0.6x with the bins swapped, which is what
    the 2-3.5x gap of cairn/DSP发端与PR.md §33 was)."""
    for noise, rj in ((0.015, 0.04), (0.018, 0.06)):
        cfg = _preset(noise, rj)
        s = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg))
        pp = s.extras["pattern_phase"]
        before = s.ber / pp.factor
        assert 0.8 < pp.read_indep / before < 1.2, (noise, pp.read_indep, before)
