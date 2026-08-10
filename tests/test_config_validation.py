"""Config validation: invalid fields must fail at construction.

The config is the single source of truth for every module and the GUI builds
its form from it, so a bad field has to be rejected here with a precise
message. Before this, ``osr=-4`` was accepted and produced a *negative* dt —
silently reversed time rather than an error.
"""

import pytest

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, DfeConfig, FfeConfig, QFormat,
    SimConfig, TxConfig,
)


@pytest.mark.parametrize("factory,frag", [
    (lambda: LinkConfig(osr=0), "osr"),
    (lambda: LinkConfig(osr=-4), "osr"),
    (lambda: LinkConfig(symbol_rate=0.0), "symbol_rate"),
    (lambda: LinkConfig(symbol_rate=-1e9), "symbol_rate"),
    (lambda: LinkConfig(modulation="pam5"), "modulation"),
    (lambda: SimConfig(n_symbols=0), "n_symbols"),
    (lambda: SimConfig(engine="turbo"), "engine"),
    (lambda: TxConfig(swing=-1.0), "swing"),
    (lambda: TxConfig(rlm=1.5), "rlm"),
    (lambda: TxConfig(rlm=0.0), "rlm"),
    (lambda: TxConfig(rj_ui=-0.01), "rj_ui"),
    (lambda: TxConfig(fir_taps=(0.1, 1.0), fir_n_pre=2), "fir_n_pre"),
    (lambda: AdcConfig(n_bits=0), "n_bits"),
    (lambda: AdcConfig(n_lanes=0), "n_lanes"),
    (lambda: AdcConfig(fullscale=0.0), "fullscale"),
    (lambda: FfeConfig(n_pre=-1), "n_pre"),
    (lambda: FfeConfig(adapt="lsm"), "adapt"),
    (lambda: DfeConfig(n_taps=-1), "n_taps"),
    (lambda: DfeConfig(adapt="sign"), "adapt"),
    (lambda: DfeConfig(n_taps=2, tap_limits=(0.5,)), "tap_limits"),
    (lambda: DfeConfig(tap1_mode="speculative"), "tap1_mode"),
    (lambda: CdrConfig(kind="pll"), "cdr.kind"),
    (lambda: CdrConfig(loop_latency_symbols=-2), "loop_latency_symbols"),
    (lambda: ChannelConfig(kind="spice"), "channel.kind"),
    (lambda: ChannelConfig(n_freq=1), "n_freq"),
    (lambda: ChannelConfig(length_m=-0.1), "length_m"),
    (lambda: QFormat(0, 4), "wl"),
    (lambda: QFormat(8, -1), "fl"),
])
def test_invalid_config_is_rejected(factory, frag):
    with pytest.raises(ValueError) as exc:
        factory()
    assert frag in str(exc.value)          # message names the offending field


def test_valid_configs_still_construct():
    """Validation must not reject legitimate configurations."""
    c = LinkConfig()                        # library defaults (a GUI preset)
    assert c.ui > 0 and c.dt > 0
    c2 = LinkConfig(modulation="pam4", symbol_rate=106.25e9, osr=16)
    assert c2.dt > 0 and c2.bits_per_symbol == 2
    # a 1-tap FIR is a passthrough: its n_pre is unused and unconstrained
    TxConfig(fir_taps=(1.0,), fir_n_pre=1)
    # optional fields stay optional
    AdcConfig(enob=None)
    DfeConfig(sum_bw=None, tap_limits=None)
    CdrConfig(clamp=None)
    ChannelConfig(f_max=None)


def test_derived_quantities_are_physical():
    c = LinkConfig(symbol_rate=53.125e9, osr=32)
    assert c.dt == pytest.approx(c.ui / 32)
    assert c.f_nyquist == pytest.approx(c.symbol_rate / 2)
    assert c.dt > 0                          # the regression this guards
