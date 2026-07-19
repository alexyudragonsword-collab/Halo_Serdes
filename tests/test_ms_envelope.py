"""Product mixed-signal envelope: 16G NRZ as the mode's default and limit."""

import warnings

import numpy as np
import pytest

from halo_serdes.config import LinkConfig, load_config
from halo_serdes.config.schema import MS_PRODUCT_MAX_BAUD, RxConfig, SimConfig
from halo_serdes.engine import run_time_link


def test_default_linkconfig_is_16g_nrz():
    cfg = LinkConfig()
    assert cfg.modulation == "nrz"
    assert cfg.symbol_rate == 16e9 == MS_PRODUCT_MAX_BAUD
    assert cfg.rx.arch == "mixed_signal"
    assert cfg.ui == pytest.approx(62.5e-12)


def test_product_mixed_signal_factory():
    rx = RxConfig.product_mixed_signal()
    assert rx.arch == "mixed_signal"
    assert rx.dfe.n_taps == 4
    assert rx.dfe.tap1_mode == "unrolled"
    assert rx.cdr.kind == "bang_bang"
    assert rx.ctle.enable


def test_canonical_yaml_runs_clean(tmp_path):
    """The shipped canonical config must run error-free (fast subset)."""
    import pathlib

    root = pathlib.Path(__file__).parent.parent
    cfg = load_config(root / "configs" / "nrz_16g_ms.yaml", overrides={
        "channel.file": str(root / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p"),
        "sim.n_symbols": 40_000,
    })
    assert cfg.symbol_rate == 16e9
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = run_time_link(cfg)
    # no envelope warning at exactly 16G
    assert not [w for w in caught if "product envelope" in str(w.message)]
    assert res.ber.n_errors == 0
    assert res.slicer_snr_db > 12


def _analytic_channel():
    from halo_serdes.config.schema import ChannelConfig

    return ChannelConfig(kind="analytic", length_m=0.1, rdc=2.0,
                         r_skin=1.0e-3, loss_tangent=0.008)


def test_warns_above_envelope():
    cfg = LinkConfig(symbol_rate=32e9,
                     channel=_analytic_channel(),
                     rx=RxConfig.product_mixed_signal(),
                     sim=SimConfig(n_symbols=5_000))
    with pytest.warns(UserWarning, match="exceeds the product envelope"):
        run_time_link(cfg)


def test_no_warning_for_adc_above_16g():
    from halo_serdes.config.schema import AdcConfig, CdrConfig, DfeConfig, FfeConfig

    cfg = LinkConfig(
        modulation="pam4", symbol_rate=56e9, osr=16,
        channel=_analytic_channel(),
        rx=RxConfig(arch="adc_dsp",
                    adc=AdcConfig(n_bits=10, n_lanes=8),
                    ffe=FfeConfig(n_pre=2, n_post=4),
                    dfe=DfeConfig(n_taps=1),
                    cdr=CdrConfig(kind="mueller_muller")),
        sim=SimConfig(n_symbols=10_000, pattern="prbs13q"))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_time_link(cfg)
    assert not [w for w in caught if "product envelope" in str(w.message)]
