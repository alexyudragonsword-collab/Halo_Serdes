"""tx.clock.rj_ui: the transmitter's white jitter moves each edge on its own.

Through the channel that is noise following the transitions -- not a shift
of the sampling instant, which is what the statistical engine smeared the
phase axis with until ROADMAP P2 #25 (0.01-0.10x the time engine's BER at
0.03-0.09 UI). The statistical engine bins it by the sampled symbol's
neighbours and takes that symbol's own two edges exactly; the time engine
is the referee."""

import dataclasses

import numpy as np
import pytest

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import ChannelConfig, ClockConfig, RxConfig, TxConfig
from halo_serdes.engine import run_time_link
from halo_serdes.engine.statistical import tx_edge_jitter
from halo_serdes.engine.statistical import run_statistical
from halo_serdes.tx.pipeline import TxPipeline


def _edge(rj: float, taps=(1.0,), n_pre=0):
    cfg = LinkConfig(modulation="nrz", symbol_rate=16e9, osr=32,
                     channel=ChannelConfig(kind="analytic", length_m=0.15),
                     tx=TxConfig(swing=1.0, fir_taps=taps, fir_n_pre=n_pre, clock=ClockConfig(rj_ui=rj)),
                     rx=RxConfig(arch="mixed_signal"))
    from halo_serdes.channel import ChannelModel

    h = ChannelModel.from_config(cfg).response_set(cfg.dt).h.y
    return cfg, tx_edge_jitter(cfg, TxPipeline.from_config(cfg), h, cfg.osr)


def test_the_bins_fix_the_sampled_symbols_own_edges():
    """No Tx FFE: a bin (level, next, previous) fixes both steps of the
    sampled symbol -- start a_n - a_(n-1), end a_(n+1) - a_n -- and a bin
    without a transition has no own-edge noise at all."""
    cfg, ej = _edge(0.05)
    lv = np.array([-0.5, 0.5])
    for k, nxt, prv in np.ndindex(2, 2, 2):
        assert ej.step[k, nxt, prv] == pytest.approx([lv[k] - lv[prv], lv[nxt] - lv[k]])
    ker, lo = ej.kernel((1, 1, 1), dv=1e-4)
    assert ker.size == 1 and lo == 0
    # more transitions around the symbol, more noise
    assert ej.sigma[1, 0, 0] > ej.sigma[1, 1, 0] > ej.sigma[1, 1, 1]


def test_small_jitter_is_the_linear_power():
    """At a small displacement an exact own-edge kernel is the linearised
    edge: its power plus the rest's is the all-edge linear power. Power, not
    variance: on the sample grid an edge drawn later moves the sample after
    its boundary and an earlier one the sample before, so the slope differs
    on the two sides and the kernel picks up a mean -- as the time engine's
    area-conserving edges do. Checked on bins where one own edge carries
    the step, so the two kernels' means do not cross."""
    _, ej = _edge(0.005)
    dv = 1e-6
    for combo in [(1, 1, 0), (1, 0, 1), (0, 1, 0), (0, 0, 1)]:
        assert np.count_nonzero(ej.step[combo]) == 1
        ker, lo = ej.kernel(combo, dv)
        x = (np.arange(ker.size) + lo) * dv
        power = float(np.sum(ker * x ** 2))
        assert power + ej.rest[combo] ** 2 == pytest.approx(ej.sigma[combo] ** 2, rel=0.03)


@pytest.mark.parametrize("preset,rj,n_sym", [
    ("PAM4 224G ADC (112 GBd stress)", 0.06, 100_000),
    ("PAM4 32G mixed-signal", 0.09, 100_000),
])
def test_two_engines_agree_on_transmit_jitter(preset, rj, n_sym):
    """Invariant 3 with heavy Tx RJ. Measured stat / time over two seeds of
    200k symbols: 0.69-1.39x on the two 224G ADC presets at 0.03-0.09 UI,
    0.47-0.67x on PAM4 32G mixed-signal at 0.06-0.09 UI (the rest there is
    the bang-bang loop's own wander, which a white clock's model leaves
    out: with it measured and added, 0.83-0.90x; ROADMAP P2 #27). The phase
    smear this replaces read 0.01-0.10x."""
    from halo_serdes.channel import ChannelModel
    from halo_serdes_app.config_bridge import load_preset

    base = load_preset(preset)
    cfg = dataclasses.replace(base, tx=dataclasses.replace(base.tx, clock=ClockConfig(rj_ui=rj)),
                              sim=dataclasses.replace(base.sim, n_symbols=n_sym, seed=1))
    cm = ChannelModel.from_config(cfg)
    t = run_time_link(cfg, channel=cm)
    kw = {}
    if cfg.rx.arch == "adc_dsp":
        kw = dict(ffe_taps=t.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    s = run_statistical(cfg, channel=cm, **kw)
    assert t.ber.n_errors > 100
    ratio = s.ber / (t.ber.n_errors / t.ber.n_checked)
    assert 0.4 < ratio < 2.0, (ratio, s.ber, t.ber.n_errors)
