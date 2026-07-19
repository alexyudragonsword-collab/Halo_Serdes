"""Config schema/loader tests: round-trip, overrides, strict keys."""

import dataclasses

import pytest

from halo_serdes.config import LinkConfig, dump_config, load_config
from halo_serdes.config.loader import apply_overrides


def _write(tmp_path, text):
    p = tmp_path / "cfg.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_minimal_config(tmp_path):
    p = _write(tmp_path, "modulation: nrz\nsymbol_rate: 32.0e9\n")
    cfg = load_config(p)
    assert cfg.modulation == "nrz"
    assert cfg.symbol_rate == 32e9
    assert cfg.osr == 32
    assert cfg.ui == pytest.approx(31.25e-12)
    assert cfg.rx.arch == "mixed_signal"


def test_nested_and_derived(tmp_path):
    p = _write(tmp_path, """
modulation: pam4
symbol_rate: 106.25e9
osr: 16
rx:
  arch: adc_dsp
  adc:
    n_bits: 7
    n_lanes: 32
  ffe:
    n_pre: 4
    n_post: 10
""")
    cfg = load_config(p)
    assert cfg.rx.adc.n_bits == 7
    assert cfg.rx.adc.n_lanes == 32
    assert cfg.bits_per_symbol == 2
    assert cfg.dt == pytest.approx(cfg.ui / 16)
    assert cfg.f_nyquist == pytest.approx(53.125e9)


def test_unknown_key_rejected(tmp_path):
    p = _write(tmp_path, "modulation: nrz\nsymbol_rate: 32e9\nbogus_key: 1\n")
    with pytest.raises(KeyError, match="bogus_key"):
        load_config(p)


def test_roundtrip(tmp_path):
    p = _write(tmp_path, """
modulation: pam4
symbol_rate: 112e9
tx:
  fir_taps: [-0.05, 1.0]
  fir_n_pre: 1
""")
    cfg = load_config(p)
    out = tmp_path / "dump.yaml"
    dump_config(cfg, out)
    cfg2 = load_config(out)
    assert cfg == cfg2


def test_overrides():
    cfg = LinkConfig(modulation="nrz", symbol_rate=32e9)
    cfg2 = apply_overrides(cfg, {"rx.arch": "adc_dsp", "sim.n_symbols": 1000})
    assert cfg2.rx.arch == "adc_dsp"
    assert cfg2.sim.n_symbols == 1000
    # original untouched (frozen dataclasses)
    assert cfg.rx.arch == "mixed_signal"
    with pytest.raises(KeyError):
        apply_overrides(cfg, {"rx.nonexistent": 1})


def test_frozen():
    cfg = LinkConfig(modulation="nrz", symbol_rate=32e9)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.symbol_rate = 1.0
