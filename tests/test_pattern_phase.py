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
    """NRZ 16G at 12 / 15 / 18 mV + 0.03 / 0.04 / 0.06 UI: binned by pattern
    class and phase the kernel's joint read was 2.01 / 1.40 / 1.15x its
    independent one, and its free-running BER 2.27 / 1.44 / 1.03x this engine
    before the factor; the model gives 2.15 / 1.49 / 1.17x."""
    f = []
    for noise, rj in ((0.012, 0.03), (0.015, 0.04), (0.018, 0.06)):
        cfg = _preset(noise, rj)
        s = st.run_statistical(cfg, channel=ChannelModel.from_config(cfg))
        f.append(s.extras["pattern_phase"].factor)
    assert 1.8 < f[0] < 2.6 and 1.3 < f[1] < 1.7 and 1.05 < f[2] < 1.3, f
    assert f[0] > f[1] > f[2], f


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
    assert abs(float(pp.shift.mean())) < 1e-6


def test_the_read_is_the_independent_one_times_the_factor(monkeypatch):
    cfg = _preset(0.015, 0.04)
    cm = ChannelModel.from_config(cfg)
    with_f = st.run_statistical(cfg, channel=cm)
    orig = st._pattern_phase_factor
    monkeypatch.setattr(st, "_pattern_phase_factor",
                        lambda *a, **k: dataclasses.replace(orig(*a, **k), factor=1.0))
    without = st.run_statistical(cfg, channel=cm)
    factor = with_f.extras["pattern_phase"].factor
    assert factor > 1.3
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
    assert a[10] > 0.0          # kp [samples per vote]
    a[10] = 0.0
    still = orig(*a, **cap["k"])
    assert still.factor == pytest.approx(1.0, abs=1e-12)
    assert np.all(still.shift == 0.0)
    assert still.read_joint == pytest.approx(still.read_indep, rel=1e-12)


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
    15 mV + 0.04 UI, where the free-running kernel read 1.44x this engine
    without the factor (five seeds of 1.6M symbols at 12 mV: 2.27x). Two
    seeds of 1M symbols here (about 100 errors); the engine reads 1.04x
    their pooled BER with the factor (0.70x without)."""
    cfg = _preset(0.015, 0.04, n_symbols=1_000_000)
    cm = ChannelModel.from_config(cfg)
    n_err = n_chk = 0
    for seed in (3, 4):
        t = run_time_link(dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, seed=seed)), channel=cm)
        n_err += t.ber.n_errors
        n_chk += t.ber.n_checked
    assert n_err > 60, n_err
    s = st.run_statistical(cfg, channel=cm)
    ratio = s.ber / (n_err / n_chk)
    assert 0.7 < ratio < 1.5, (ratio, s.ber, n_err)
    assert 1.3 < s.extras["pattern_phase"].factor < 1.7
