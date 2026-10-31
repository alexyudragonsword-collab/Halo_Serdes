"""Product mixed-signal envelope: three tiers calibrated by examples/12.

    default anchor 16 GBd | comfort NRZ 24 / PAM4 28 / PAM6 30 / PAM8 24 Gb/s |
    absolute limit NRZ 28 / PAM4 32 / PAM6 32.5 / PAM8 33 Gb/s | hard ceiling 30 GBd

(Recalibrated 2026-10-30 with the impulse trimmed by its ISI tail, ROADMAP
P1 #42: NRZ's limit was 30, PAM4's 36 with comfort 32. The PAM4 32G
mixed-signal preset, 16 GBd, now sits at its limit and is warned marginal.)
"""

import pathlib
import warnings

import pytest

from halo_serdes.config import LinkConfig, load_config
from halo_serdes.config.schema import (
    MS_COMFORT_DATA_RATE,
    MS_DEFAULT_BAUD,
    MS_HARD_MAX_BAUD,
    MS_LIMIT_DATA_RATE,
    ChannelConfig,
    RxConfig,
    SimConfig,
)
from halo_serdes.engine import run_time_link

ROOT = pathlib.Path(__file__).parent.parent


def _analytic_channel():
    return ChannelConfig(kind="analytic", length_m=0.1, rdc=2.0,
                         r_skin=1.0e-3, loss_tangent=0.008)


def _ms_cfg(modulation, symbol_rate, n_symbols=5_000):
    return LinkConfig(
        modulation=modulation, symbol_rate=symbol_rate,
        channel=_analytic_channel(),
        rx=RxConfig.product_mixed_signal(),
        sim=SimConfig(n_symbols=n_symbols,
                      pattern="prbs13q" if modulation == "pam4" else "prbs31"))


def _envelope_warnings(cfg):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert run_time_link(cfg) is not None   # a warning, never a refusal
    return [str(w.message) for w in caught
            if "envelope" in str(w.message) or "marginal zone" in str(w.message)]


def test_constants_are_consistent():
    assert MS_DEFAULT_BAUD == 16e9
    assert MS_COMFORT_DATA_RATE == {"nrz": 24e9, "pam4": 28e9, "pam6": 30e9, "pam8": 24e9}
    assert MS_LIMIT_DATA_RATE == {"nrz": 28e9, "pam4": 32e9, "pam6": 32.5e9, "pam8": 33e9}
    assert MS_HARD_MAX_BAUD == 30e9
    # comfort < limit per modulation
    for m in ("nrz", "pam4", "pam6", "pam8"):
        assert MS_COMFORT_DATA_RATE[m] < MS_LIMIT_DATA_RATE[m]
    # the limits sit at the same closing loss once the level penalty is paid:
    # Whisper's last open eye at -29.0 / -18.7 / -16.1 / -14.5 dB (examples/12,
    # recalibrated 2026-10-30 with the impulse trimmed by its ISI tail)


def test_default_linkconfig_is_16g_nrz():
    cfg = LinkConfig()
    assert cfg.modulation == "nrz"
    assert cfg.symbol_rate == MS_DEFAULT_BAUD
    assert cfg.rx.arch == "mixed_signal"
    assert cfg.ui == pytest.approx(62.5e-12)


def test_product_mixed_signal_factory():
    rx = RxConfig.product_mixed_signal()
    assert rx.arch == "mixed_signal"
    assert rx.dfe.n_taps == 4
    assert rx.dfe.tap1_mode == "unrolled"
    assert rx.cdr.kind == "bang_bang"
    assert rx.ctle.enable


# ---- tier boundaries ------------------------------------------------------


def test_comfort_zone_is_silent():
    # NRZ 24 Gb/s and PAM4 28 Gb/s sit exactly on the comfort boundary
    assert _envelope_warnings(_ms_cfg("nrz", 24e9)) == []
    assert _envelope_warnings(_ms_cfg("pam4", 14e9)) == []


def test_marginal_zone_warns_marginal():
    msgs = _envelope_warnings(_ms_cfg("nrz", 26e9))
    assert any("marginal zone" in m for m in msgs)
    assert not any("exceeds" in m for m in msgs)
    msgs = _envelope_warnings(_ms_cfg("pam4", 15e9))  # 30 Gb/s
    assert any("marginal zone" in m for m in msgs)
    # the PAM4 32G preset's 16 GBd is the limit itself: marginal, not beyond
    msgs = _envelope_warnings(_ms_cfg("pam4", 16e9))
    assert any("marginal zone" in m for m in msgs) and not any("exceeds" in m for m in msgs)


def test_beyond_limit_warns_exceeds():
    msgs = _envelope_warnings(_ms_cfg("nrz", 30e9))   # > 28
    assert any("exceeds the mixed-signal envelope" in m for m in msgs)
    msgs = _envelope_warnings(_ms_cfg("pam4", 17e9))  # 34 Gb/s > 32
    assert any("exceeds the mixed-signal envelope" in m for m in msgs)


@pytest.mark.parametrize("mod, comfort_baud, marginal_baud, beyond_baud", [
    ("pam6", 12e9, 13e9, 14e9),     # 30 / 32.5 / 35 Gb/s
    ("pam8", 8e9, 10e9, 12e9),      # 24 / 30 / 36 Gb/s
])
def test_pam6_pam8_tiers(mod, comfort_baud, marginal_baud, beyond_baud):
    """PAM6 / PAM8 calibrated on 2026-10-17 (ROADMAP P2 #34) the way NRZ and
    PAM4 were: Whisper's last open post-DFE eye at 13 GBd PAM6 (12.4 mV,
    -16.1 dB) and 11 GBd PAM8 (1.5 mV, -14.5 dB), comfort where the eye is
    still >= ~25 % of the level spacing (12 GBd PAM6 0.28, 8 GBd PAM8 0.42;
    9 GBd PAM8 is 0.24). No "未核实" warning any more."""
    def run(baud):
        return _envelope_warnings(LinkConfig(
            modulation=mod, symbol_rate=baud, channel=_analytic_channel(),
            rx=RxConfig.product_mixed_signal(), sim=SimConfig(n_symbols=5_000, pattern="prbs31")))
    assert run(comfort_baud) == []
    msgs = run(marginal_baud)
    assert any("marginal zone" in m for m in msgs) and not any("exceeds" in m for m in msgs), msgs
    msgs = run(beyond_baud)
    assert any("exceeds the mixed-signal envelope" in m for m in msgs), msgs
    assert not any("未核实" in m for m in msgs)


def test_adc_arch_never_warns():
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
    assert _envelope_warnings(cfg) == []


# ---- canonical configs stay clean ----------------------------------------


def test_canonical_yaml_runs_clean():
    cfg = load_config(ROOT / "configs" / "nrz_16g_ms.yaml", overrides={
        "channel.file": str(ROOT / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p"),
        "sim.n_symbols": 40_000,
    })
    assert cfg.symbol_rate == 16e9
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = run_time_link(cfg)
    assert not [w for w in caught if "envelope" in str(w.message)]
    assert res.ber.n_errors == 0
    assert res.slicer_snr_db > 12


def test_canonical_pam4_yaml_runs_clean():
    """Error-free on its own channel -- and, since the 2026-10-30
    recalibration, at the PAM4 limit, so the run says "marginal"."""
    cfg = load_config(ROOT / "configs" / "pam4_32g_ms.yaml",
                      overrides={"sim.n_symbols": 40_000})
    assert cfg.data_rate == 32e9
    with pytest.warns(UserWarning, match="marginal zone"):
        res = run_time_link(cfg)
    assert res.ber.n_errors == 0
    assert res.slicer_snr_db > 14
