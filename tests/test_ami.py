"""io/ami.py — IBIS-AMI adapter seam + behavioral COM (task ②)."""

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CtleConfig, DfeConfig, FfeConfig, RxConfig, TxConfig,
)
from halo_serdes.engine import run_time_link
from halo_serdes.io import (
    AmiModel, IbisAmiModel, NativeCom, NativeFirAmi, load_ami_model,
)


def test_native_fir_init_equals_getwave_on_impulse():
    """An LTI FIR: the Init-flow impulse transform must equal GetWave applied
    to that impulse — the invariant a well-formed AMI model satisfies."""
    osr = 16
    dt, ui = 1e-12 / osr, 1e-12
    m = NativeFirAmi([-0.1, 0.8, -0.15], n_pre=1, sample_spaced=True)
    imp = np.zeros(64)
    imp[20] = 1.0
    hi = m.init(imp, dt, ui)
    wg, clk = m.get_wave(imp, dt, ui)
    assert np.allclose(hi, wg)
    assert clk is None
    assert isinstance(m, AmiModel) and m.has_getwave


def test_ui_spaced_taps_expand_to_grid():
    osr = 8
    dt, ui = 1e-12 / osr, 1e-12
    m = NativeFirAmi([1.0, -0.25], n_pre=0, sample_spaced=False)
    imp = np.zeros(40)
    imp[10] = 1.0
    out = m.init(imp, dt, ui)
    # a UI-spaced post tap lands one UI (osr samples) after the main
    assert out[10] == pytest.approx(1.0)
    assert out[10 + osr] == pytest.approx(-0.25)


def test_ibisami_without_backend_raises_clear_error():
    with pytest.raises(ImportError, match="pyibisami"):
        IbisAmiModel("nonexistent.ami", "nonexistent.so")


def test_factory_dispatch():
    assert isinstance(load_ami_model(taps=[1.0]), NativeFirAmi)
    with pytest.raises(ImportError):
        load_ami_model(ami_file="a.ami", dll_file="b.so")


def _cfg(length_m=0.30, peak_db=6.0, fir=(-0.1, 0.85, -0.05), noise=0.005):
    return LinkConfig(
        modulation="nrz", symbol_rate=32e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=fir, fir_n_pre=1, rj_ui=0.005),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=peak_db),
                    ffe=FfeConfig(n_pre=4, n_post=10), dfe=DfeConfig(n_taps=1),
                    noise_rms=noise))


def test_native_com_reasonable_and_monotone():
    """Behavioral COM must fall on a worse channel and be a finite dB number."""
    com = NativeCom(target_der=1e-4, n_dfe=1)
    short = com.compute(ChannelModel.from_config(_cfg(0.20)), _cfg(0.20))
    long = com.compute(ChannelModel.from_config(_cfg(0.45)), _cfg(0.45))
    assert np.isfinite(short.com_db) and np.isfinite(long.com_db)
    assert short.com_db > long.com_db  # more loss -> lower margin
    # aggregate noise is the RSS of the reported components
    rss = np.hypot(np.hypot(short.fom_isi, short.fom_xtalk),
                   np.hypot(short.fom_noise, short.fom_jitter))
    assert short.a_noise == pytest.approx(short.detail["q_target"] * rss, rel=1e-6)


def test_native_com_crosstalk_lowers_margin():
    from halo_serdes.core.waveform import Waveform

    cfg = _cfg(0.30)
    cm = ChannelModel.from_config(cfg)
    com = NativeCom()
    base = com.compute(cm, cfg)
    agg = Waveform(np.r_[np.zeros(50), 0.05, np.zeros(50)], cfg.dt)
    with_xt = com.compute(cm, cfg, xtalk_pulses=[agg])
    assert with_xt.fom_xtalk > 0
    assert with_xt.com_db < base.com_db


def test_engine_accepts_ami_rx_getwave_slot():
    """A native Rx GetWave FIR plugged into the engine runs and, as a small
    post-cursor canceller, does not blow up BER on an easy link."""
    cfg = _cfg(0.20, noise=0.003)
    cm = ChannelModel.from_config(cfg)
    rx_eq = NativeFirAmi([1.0, -0.05], n_pre=0, sample_spaced=False)
    res = run_time_link(cfg, channel=cm, rx_ami=rx_eq)
    assert res.n_symbols > 0
    assert np.isfinite(res.slicer_snr_db)


def test_engine_ami_init_only_folds_into_channel():
    """An Init-only Tx model (has_getwave False) transforms the impulse; the
    run must still complete and differ from the no-AMI baseline."""
    cfg = _cfg(0.20, fir=(1.0,), noise=0.003)  # no Tx FIR in cfg
    cm = ChannelModel.from_config(cfg)

    class InitOnly(NativeFirAmi):
        has_getwave = False

    tx_eq = InitOnly([-0.12, 1.0, -0.08], n_pre=1, sample_spaced=False)
    base = run_time_link(cfg, channel=cm)
    withtx = run_time_link(cfg, channel=cm, tx_ami=tx_eq)
    assert withtx.n_symbols > 0
    assert not np.isclose(base.slicer_snr_db, withtx.slicer_snr_db)
