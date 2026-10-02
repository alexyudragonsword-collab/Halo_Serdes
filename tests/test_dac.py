"""Transmit DAC: closed forms for quantisation and INL, clipping that is not
silent, the statistical engine's equivalent noise, and the places the DAC
reaches (backchannel training, optical TDECQ, profile clocks)."""

import dataclasses

import numpy as np
import pytest

from halo_serdes.analysis.tx_metrics import tx_report
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, ClockConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine.static_link import make_pattern
from halo_serdes.tx.dac import TxDac
from halo_serdes.tx.pipeline import TxPipeline


# ------------------------------------------------------------- closed forms

@pytest.mark.parametrize("n_bits", [5, 6, 7, 8])
def test_ideal_dac_sqnr_is_6p02n_plus_1p76(n_bits):
    assert abs(TxDac(n_bits, 1.0).sqnr_of_sine() - (6.02 * n_bits + 1.76)) < 0.2


def test_ideal_dac_is_the_mid_rise_quantiser():
    d = TxDac(6, 1.2)
    assert np.all(d.inl == 0.0)
    x = np.random.default_rng(0).uniform(-0.6, 0.6, 200_000)
    err = d(x) - x
    assert np.max(np.abs(err)) <= d.lsb / 2 + 1e-12
    assert abs(np.mean(err ** 2) / (d.lsb ** 2 / 12) - 1) < 0.02


@pytest.mark.parametrize("n_bits,thermo", [(6, 0), (7, 3), (7, 7), (8, 4)])
def test_inl_rms_matches_the_unit_cell_closed_form(n_bits, thermo):
    """E[INL^2] = sigma^2 (M - 1) / 6 over the codes, any segmentation."""
    sigma = 0.05
    rms = [TxDac(n_bits, 1.0, thermo_msbs=thermo, unit_sigma=sigma,
                 rng=np.random.default_rng(s)).inl_rms for s in range(60)]
    measured = float(np.sqrt(np.mean(np.square(rms))))
    expected = TxDac.inl_rms_expected(n_bits, sigma)
    assert abs(measured / expected - 1) < 0.2, (measured, expected)


def test_inl_variance_follows_the_bridge_profile():
    """Var(INL_c) = sigma^2 c (1 - c / M): zero at both ends, largest mid-code."""
    n, sigma = 6, 0.05
    inl = np.array([TxDac(n, 1.0, thermo_msbs=2, unit_sigma=sigma,
                          rng=np.random.default_rng(s)).inl for s in range(400)])
    m = 2 ** n - 1
    c = np.arange(m + 1)
    expected = sigma ** 2 * c * (1 - c / m)
    assert np.all(inl[:, 0] == 0) and np.allclose(inl[:, -1], 0, atol=1e-12)
    mid = slice(8, m - 8)
    assert np.all(np.abs(inl.var(axis=0)[mid] / expected[mid] - 1) < 0.25)


def test_full_thermometer_dac_is_monotonic_where_binary_is_not():
    n, sigma = 6, 0.3
    thermo = TxDac(n, 1.0, thermo_msbs=n, unit_sigma=sigma, rng=np.random.default_rng(1))
    assert np.all(np.diff(thermo.table) > 0)
    nonmono = sum(np.any(np.diff(TxDac(n, 1.0, unit_sigma=sigma, rng=np.random.default_rng(s)).table) < 0)
                  for s in range(20))
    assert nonmono > 0


# ------------------------------------------------------------- in the link

def _link(**tx):
    base = dict(swing=1.0, fir_taps=(-0.1, 0.7, -0.2), fir_n_pre=1)
    base.update(tx)
    return LinkConfig(modulation="pam4", symbol_rate=53.125e9, osr=16,
                      channel=ChannelConfig(kind="analytic", length_m=0.2),
                      tx=TxConfig(**base),
                      rx=RxConfig(arch="adc_dsp"),
                      sim=SimConfig(n_symbols=20_000, seed=4, pattern="prbs13q"))


def test_default_full_scale_is_the_ffe_peak_and_never_clips():
    cfg = _link(dac_bits=6)
    pipe = TxPipeline.from_config(cfg)
    assert pipe.dac_model.fullscale == pytest.approx(1.0)      # swing * sum|taps|
    pipe.symbol_stage(make_pattern(cfg))
    assert pipe.stats["dac_clipped"] == 0


def test_clipping_is_counted_and_costs_sndr():
    fine = tx_report(_link(dac_bits=7))
    clipped = tx_report(_link(dac_bits=7, dac_fs=0.6))
    assert fine.dac_clipped == 0
    assert clipped.dac_clipped > 0.05 * clipped.line_symbols.size
    assert clipped.sndr_db < fine.sndr_db - 10, (clipped.sndr_db, fine.sndr_db)


def test_sndr_tracks_the_bit_count():
    s = [tx_report(_link(dac_bits=n)).sndr_db for n in (5, 6, 7, 8)]
    assert np.all(np.diff(s) > 4.5), s
    assert np.isinf(tx_report(_link()).sndr_db)


def test_mismatch_draws_never_move_the_links_random_numbers():
    """The DAC's cells come from a stream of their own: with a profile clock,
    the edges a DAC link draws are byte-identical to those without one."""
    from halo_serdes.analysis.cdr_tracking import tx_edge_offsets_s

    clk = ClockConfig(kind="profile", file="data/clock_profiles/bench_markulic16_sspll_40m_10p24g.yaml",
                      f0_hz=53.125e9, rj_ui=0.003)
    plain, dac = _link(clock=clk), _link(clock=clk, dac_bits=6, dac_unit_sigma=0.05, dac_thermo_msbs=3)
    assert np.array_equal(tx_edge_offsets_s(plain), tx_edge_offsets_s(dac))
    from halo_serdes.engine import run_time_link

    res = run_time_link(dac)
    assert np.isfinite(res.slicer_snr_db) and res.ber.n_checked > 10_000


def test_backchannel_training_stays_inside_the_dac_range():
    from halo_serdes.engine import train_tx_fir

    cfg = LinkConfig(modulation="nrz", symbol_rate=26.5625e9, osr=16,
                     channel=ChannelConfig(kind="analytic", length_m=0.3),
                     tx=TxConfig(swing=1.0, dac_bits=6, dac_fs=0.8),
                     rx=RxConfig(ctle=CtleConfig(enable=False), dfe=DfeConfig(n_taps=0)),
                     sim=SimConfig(n_symbols=4000, seed=1, pattern="prbs13"))
    res = train_tx_fir(cfg, n_pre=1, n_post=2)
    assert cfg.tx.swing * np.abs(res.taps).sum() <= cfg.tx.dac_fs + 1e-12
    trained = dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, fir_taps=tuple(res.taps), fir_n_pre=1))
    pipe = TxPipeline.from_config(trained)
    pipe.symbol_stage(make_pattern(trained))
    assert pipe.stats["dac_clipped"] == 0
    # without a DAC the old constraint holds unchanged
    free = train_tx_fir(dataclasses.replace(cfg, tx=TxConfig(swing=1.0)), n_pre=1, n_post=2)
    assert np.abs(free.taps).sum() == pytest.approx(1.0)


def test_statistical_dac_noise_is_the_measured_error_at_the_slicer():
    """sigma_q carried through every cursor of the DAC-to-slicer response is
    the RMS of the waveform difference the DAC makes at the slicer instant."""
    from halo_serdes.channel import ChannelModel
    from halo_serdes.channel.response import pulse_from_impulse
    from halo_serdes.core.sampler import baud_samples
    from halo_serdes.core.waveform import Waveform
    from halo_serdes.engine.lti import fft_filter
    from halo_serdes.engine.statistical import dac_noise_at_slicer

    for n_bits in (5, 7):
        cfg = _link(dac_bits=n_bits)
        h = ChannelModel.from_config(cfg).response_set(cfg.dt).h.y
        sym = make_pattern(cfg)
        waves = []
        for c in (cfg, _link()):
            pipe = TxPipeline.from_config(c)
            waves.append(fft_filter(pipe.waveform(pipe.symbol_stage(sym)).y, h))
        pulse = pulse_from_impulse(Waveform(h, cfg.dt), cfg.osr).y
        phase = int(np.argmax(np.abs(pulse))) % cfg.osr
        diff = baud_samples(waves[0] - waves[1], cfg.osr, phase)[100:-100]
        model = dac_noise_at_slicer(TxPipeline.from_config(cfg).dac_sigma_q, h, cfg.dt, cfg.osr)
        assert abs(np.std(diff) / model - 1) < 0.15, (n_bits, np.std(diff), model)


def test_dac_bits_show_up_in_tdecq():
    """Optical topology: the DAC sits in front of the E/O, so fewer bits read
    as a worse TDECQ on the same transmitter."""
    from halo_serdes.analysis.tdecq import tdecq
    from halo_serdes.config.schema import OpticalConfig, TopologyConfig
    from halo_serdes.engine.optical_stage import transmitter_power

    def measure(bits):
        seg = ChannelConfig(kind="analytic", length_m=0.05, n_freq=4096)
        cfg = LinkConfig(modulation="pam4", symbol_rate=53.125e9, osr=16,
                         tx=TxConfig(swing=1.0, fir_taps=(-0.1, 0.75, -0.15), fir_n_pre=1, dac_bits=bits),
                         channel=ChannelConfig(kind="analytic"),
                         sim=SimConfig(n_symbols=16382, seed=3, pattern="prbs13q"),
                         topology=TopologyConfig(seg_a=seg, seg_b=seg, optical=OpticalConfig(
                             kind="vcsel_mmf", f_r_hz=26e9, damping_hz=30e9, er_db=4.0, oma_dbm=1.0,
                             rin_db_hz=-150.0, length_m=50.0, modal_bw_mhz_km=4700.0)))
        p, line = transmitter_power(cfg, include_seg_a=False, through_fibre=True)
        return tdecq(p, cfg.dt, cfg.symbol_rate, line, n_taps=5, pre_options=(1, 2)).tdecq_db

    ideal, b6, b4 = measure(None), measure(6), measure(4)
    assert b4 > b6 > ideal, (ideal, b6, b4)
