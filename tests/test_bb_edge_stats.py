"""The bang-bang detector's gain and noise read off its edge samples (ROADMAP P2 #38).

Taken Gaussian, the edge's ISI -- a few large cursors -- read the gain 1.22 /
1.07x the kernel frozen at its lock (NRZ 16G / PAM4 32G); a white detector
noise counting the data's whole power had compensated in the hunting.
``BbEdgeStats`` takes both from the same edge samples over the pattern.
And DFE error propagation's four-state chain (ROADMAP P2 #37) is checked
here on the model side; the time engine's run lengths are in the docstrings."""

import dataclasses

import numpy as np
import pytest

from halo_serdes.cdr.linear import BbEdgeStats, bb_pd_gain
from halo_serdes.engine import stat_loop
import halo_serdes.engine.statistical as st


def _clean_pulse(osr=16):
    """Two cursors, no other ISI: a sample-per-UI ramp up and down."""
    t = np.arange(8 * osr)
    y = np.clip(1.0 - np.abs(t - 3 * osr) / osr, 0.0, None)
    return y, 3 * osr


def test_without_isi_the_edge_is_the_gaussian_model():
    """No ISI: every edge sits at zero with the same slope, so the gain is
    the Gaussian formula's and the noise is all white, one per transition."""
    osr = 16
    y, peak = _clean_pulse(osr)
    rng = np.random.default_rng(1)
    amps = rng.choice([-0.2, 0.2], size=1 << 14)
    sigma = 0.01
    e = BbEdgeStats.build(y, osr, float(peak), osr // 2, amps, sigma)
    assert np.allclose(e.u, 0.0, atol=1e-12)
    slope = float(np.abs(e.du).mean())              # V per UI
    assert np.allclose(np.abs(e.du), slope)
    assert e.gain(0.0) == pytest.approx(bb_pd_gain(sigma / slope, e.rho), rel=1e-9)
    # loop jitter folds in as more edge noise, exactly
    assert e.gain(0.02) == pytest.approx(bb_pd_gain(np.hypot(sigma / slope, 0.02), e.rho), rel=1e-9)
    nz = e.noise(0.0)
    assert np.size(nz.line_freq) == 0 and nz.white_var == pytest.approx(e.rho, rel=1e-6)


def test_a_repeating_pattern_puts_the_data_noise_on_lines():
    """Periodic: the data part has no zero-frequency density, only lines at
    multiples of the repetition rate; the white part is the AWGN's."""
    osr = 16
    y, peak = _clean_pulse(osr)
    y = y + 0.3 * np.roll(y, 2 * osr)               # a second-postcursor ISI term
    amps = np.random.default_rng(2).choice([-0.2, 0.2], size=127)
    e = BbEdgeStats.build(y, osr, float(peak), osr // 2, amps, 0.01, periodic=True)
    nz = e.noise(0.01)
    assert np.size(nz.line_freq) == 63
    assert nz.line_freq[0] == pytest.approx(1 / 127)
    assert 0.0 < nz.white_var < e.rho and float(np.sum(nz.line_power)) > 0.0


def _edge_of(cfg, monkeypatch):
    seen = {}
    orig = stat_loop._bb_edge_stats

    def grab(*a, **kw):
        seen["edge"] = orig(*a, **kw)
        return seen["edge"]

    monkeypatch.setattr(stat_loop, "_bb_edge_stats", grab)
    sol = st.run_statistical(cfg).extras["clock_loop"]
    return seen["edge"], sol


@pytest.mark.parametrize("name, kernel_slope", [
    ("NRZ 16G mixed-signal", 1.908),
    ("PAM4 32G mixed-signal", 6.842),
])
def test_the_gain_is_the_kernels_frozen_slope(name, kernel_slope, monkeypatch):
    """The kernel's mean detector output against a constant transmit delay,
    loop frozen (probe ``bbprobe``, 200k symbols, 2026-10-31): 1.908 / 6.842
    per UI at its lock. The edge samples give 1.98 / 6.76 at zero loop
    jitter; the Gaussian classes gave 2.33 / 7.32."""
    from halo_serdes_app.config_bridge import load_preset

    edge, sol = _edge_of(load_preset(name), monkeypatch)
    assert 0.95 < edge.gain(0.0) / kernel_slope < 1.08
    assert sol.k_pd == pytest.approx(edge.gain(sol.sigma_ui), rel=1e-5)


def test_a_chain_error_after_a_chain_error_follows_more_often():
    """The 224G preset frozen at its lock, 2.5 mV (probe ``p71``, two seeds
    of 500k, 833 errors): after a first error the next fails 0.72 of the
    time, after the first chain error 0.39, after a later one 0.44-0.51;
    2.32 errors per first one. Taking p2 for the whole chain read 2.06
    (-11 %); with p3 the chain reads 2.20 (-5 %)."""
    from halo_serdes.engine import run_time_link
    from halo_serdes_app.config_bridge import load_preset

    b = load_preset("PAM4 224G ADC (106 GBd)")
    rx = dataclasses.replace(b.rx, noise_rms=0.0025,
                             cdr=dataclasses.replace(b.rx.cdr, kp_shift=40, ki_shift=50),
                             ffe=dataclasses.replace(b.rx.ffe, adapt="none"),
                             dfe=dataclasses.replace(b.rx.dfe, adapt="none"))
    cfg = dataclasses.replace(b, rx=rx, sim=dataclasses.replace(b.sim, n_symbols=20_000))
    r = run_time_link(cfg)
    ep = st.run_statistical(cfg, ffe_taps=r.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre).extras["dfe_ep_factor"]
    assert 2.15 < ep < 2.45, ep
