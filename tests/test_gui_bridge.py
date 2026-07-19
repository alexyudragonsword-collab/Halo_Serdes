"""GUI config bridge: form<->LinkConfig<->YAML round-trips and envelope."""

import pytest

pytest.importorskip("yaml")

from halo_serdes.config import LinkConfig
from halo_serdes_gui import config_bridge as cb


@pytest.mark.parametrize("name", cb.preset_names())
def test_preset_roundtrip_values_and_yaml(name):
    cfg = cb.load_preset(name)
    # form values round-trip
    cfg2 = cb.build_config(cb.config_to_values(cfg))
    assert cfg2.modulation == cfg.modulation
    assert abs(cfg2.symbol_rate - cfg.symbol_rate) < 1.0
    assert cfg2.osr == cfg.osr
    assert cfg2.rx.arch == cfg.rx.arch
    assert cfg2.rx.dfe.n_taps == cfg.rx.dfe.n_taps
    assert cfg2.rx.cdr.kind == cfg.rx.cdr.kind
    assert cfg2.tx.fir_taps == cfg.tx.fir_taps
    # YAML round-trip
    cfg3 = cb.yaml_to_config(cb.config_to_yaml(cfg))
    assert cfg3.sim.pattern == cfg.sim.pattern
    assert cfg3.rx.ffe.n_post == cfg.rx.ffe.n_post


def test_scale_applied_symbol_rate_and_ghz_fields():
    cfg = LinkConfig(modulation="nrz", symbol_rate=28e9)
    vals = cb.config_to_values(cfg)
    assert vals["symbol_rate"] == pytest.approx(28.0)  # shown in GBd
    assert cb.build_config(vals).symbol_rate == pytest.approx(28e9)


def test_optional_and_tuple_coercion():
    # empty optional -> None; tuple text -> tuple of floats
    f_optf = cb.FIELD_BY_PATH["channel.f_max"]
    assert cb.coerce_in(f_optf, "") is None
    assert cb.coerce_in(f_optf, 10) == pytest.approx(10e9)  # GHz scale
    f_tuple = cb.FIELD_BY_PATH["tx.fir_taps"]
    assert cb.coerce_in(f_tuple, "-0.1, 0.85, -0.05") == (-0.1, 0.85, -0.05)
    assert cb.coerce_in(f_tuple, "") == (1.0,)


def test_envelope_status():
    assert cb.envelope_status(LinkConfig(modulation="nrz", symbol_rate=16e9))[0] == "ok"
    lvl, msg = cb.envelope_status(LinkConfig(modulation="nrz", symbol_rate=32e9))
    assert lvl == "crit" and msg
    # adc arch never triggers the mixed-signal envelope
    from halo_serdes.config.schema import RxConfig
    adc = LinkConfig(modulation="pam4", symbol_rate=112e9,
                     rx=RxConfig(arch="adc_dsp"))
    assert cb.envelope_status(adc) == ("ok", "")


def test_partial_values_fill_defaults():
    cfg = cb.build_config({"modulation": "pam4", "rx.arch": "adc_dsp"})
    assert cfg.modulation == "pam4" and cfg.rx.arch == "adc_dsp"
    # untouched fields keep dataclass defaults
    assert cfg.osr == LinkConfig().osr
