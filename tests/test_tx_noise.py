"""tx.noise_rms: the transmitter's own white noise, per UI at the DAC output.

It sits ahead of the driver pole and (optical) the modulator's band limit,
so the chain shapes it as it shapes the signal; the statistical engine
carries it there -- its variance and, for a sequence detector working a
partial-response target, its colour."""

import dataclasses

import numpy as np
import pytest

from halo_serdes.config import LinkConfig, PrConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig, MlsdConfig,
    OpticalConfig, RxConfig, SimConfig, TopologyConfig, TxConfig,
)
from halo_serdes.engine import run_time_link
from halo_serdes.engine.statistical import run_statistical
from halo_serdes.tx.pipeline import TxPipeline


def test_noise_is_white_per_ui_and_drawn_from_its_own_stream():
    cfg = LinkConfig(modulation="pam4", tx=TxConfig(fir_taps=(-0.1, 0.8, -0.1), fir_n_pre=1,
                                                    noise_rms=0.01, clock=ClockConfig(rj_ui=0.01)),
                     sim=SimConfig(seed=4))
    quiet = dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, noise_rms=0.0))
    sym = np.random.default_rng(1).integers(0, 4, 50_000)
    noisy, clean = TxPipeline.from_config(cfg), TxPipeline.from_config(quiet)
    v = noisy.symbol_stage(sym)
    d = v - clean.symbol_stage(sym)
    assert abs(np.std(d) / 0.01 - 1) < 0.02
    assert abs(np.corrcoef(d[1:], d[:-1])[0, 1]) < 0.02
    # the same draw again from a fresh pipeline (seeded from sim.seed), none
    # without the noise or with noise=False
    assert np.array_equal(TxPipeline.from_config(cfg).symbol_stage(sym), v)
    assert np.array_equal(TxPipeline.from_config(cfg).symbol_stage(sym, noise=False),
                          clean.symbol_stage(sym))
    # the link's generator is not touched: the edges drawn from it after the
    # Tx are the edges drawn without the noise
    e1 = noisy.edge_offsets(v, np.random.default_rng(9))
    e2 = clean.edge_offsets(clean.symbol_stage(sym), np.random.default_rng(9))
    assert np.array_equal(e1, e2)
    assert noisy.tx_sigma == pytest.approx(0.01)


BAUD = 53.125e9


def _optical(f_r, tx_noise, target=(1.0,), at="rx", precode=False, n_sym=200_000):
    """Example 40's VCSEL link with the noise in the transmitter: RIN and TIA
    noise pushed below it."""
    return LinkConfig(
        modulation="pam4", symbol_rate=BAUD, osr=16, precode=precode,
        tx=TxConfig(swing=1.0, noise_rms=tx_noise),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=3.0),
                    adc=AdcConfig(n_bits=10, n_lanes=16, enob=None, fullscale=0.3),
                    ffe=FfeConfig(n_pre=4, n_post=12, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0), mlsd=MlsdConfig(kind="viterbi", memory=2),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=1e-5),
        sim=SimConfig(n_symbols=n_sym, seed=7, pattern="prbs13q"),
        topology=TopologyConfig(optical=OpticalConfig(
            kind="vcsel_mmf", f_r_hz=f_r, damping_hz=30e9, er_db=4.0, oma_dbm=1.0,
            rin_db_hz=-165.0, length_m=100.0, modal_bw_mhz_km=4700.0, responsivity_a_w=0.7,
            tia_bw_hz=40e9, tia_noise_pa_sqrthz=1.0, tz_ohm=2000.0)),
        pr=PrConfig(target=target, at=at))


@pytest.mark.parametrize("kw,sigma", [
    ({}, 0.045),
    (dict(target=(1.0, 0.5)), 0.042),
    (dict(target=(1.0, 1.0), precode=True), 0.036),
    (dict(target=(1.0, 0.5), at="tx"), 0.036),
])
def test_statistical_engine_follows_transmitter_noise_through_the_chain(kw, sigma):
    """Invariant 3 with the noise in the transmitter, ahead of a 12 GHz VCSEL:
    at the slicer it has the colour of the whole chain after the DAC, which
    a receive PR target's union bound has to see (with the receiver's white
    noise only it read 0.32-0.48x for receive PR, 2.9x for transmit a = 0.5)."""
    cfg = _optical(12e9, sigma, **kw)
    res = run_time_link(cfg)
    assert res.ber.n_errors > 60, res.ber.n_errors
    st = run_statistical(cfg, ffe_taps=res.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = st.ber / res.ber.ber
    assert 0.5 < ratio < 2.0, (kw, res.ber.ber, st.ber)


def test_tx_sndr_counts_the_transmitter_noise():
    """The SNDR's reference is noiseless: white noise of sigma on unit-power
    PAM4 levels (swing 1 V, mean level power 5/36 V^2) reads 10 log10(P / sigma^2)."""
    from halo_serdes.analysis.tx_metrics import tx_report

    sigma = 0.02
    cfg = LinkConfig(modulation="pam4", tx=TxConfig(noise_rms=sigma), sim=SimConfig(n_symbols=40_000))
    expect = 10 * np.log10((5 / 36) / sigma ** 2)
    assert abs(tx_report(cfg).sndr_db - expect) < 0.5, (tx_report(cfg).sndr_db, expect)
