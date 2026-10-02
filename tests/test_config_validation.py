"""Config validation: invalid fields must fail at construction.

The config is the single source of truth for every module and the GUI builds
its form from it, so a bad field has to be rejected here with a precise
message. Before this, ``osr=-4`` was accepted and produced a *negative* dt —
silently reversed time rather than an error.
"""

import pytest

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, DfeConfig, FfeConfig, OpticalConfig, QFormat,
    SimConfig, ClockConfig, TopologyConfig, TxConfig,
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
    (lambda: TxConfig(clock=ClockConfig(rj_ui=-0.01)), "rj_ui"),
    # ClockConfig: every field has an illegal value, and the profile kind
    # without a file must fail *here* -- at construction, so load_config names
    # the field -- not as a FileNotFoundError out of the engine.
    (lambda: ClockConfig(kind="pll"), "clock.kind"),
    (lambda: ClockConfig(kind="profile"), "clock.file"),
    (lambda: ClockConfig(kind="profile", file=""), "clock.file"),
    (lambda: ClockConfig(f0_hz=0.0), "f0_hz"),
    (lambda: ClockConfig(f0_hz=-1e9), "f0_hz"),
    (lambda: ClockConfig(sj_ui=-0.1), "sj_ui"),
    (lambda: ClockConfig(sj_freq=-1.0), "sj_freq"),
    (lambda: ClockConfig(dcd_ui=-0.01), "dcd_ui"),
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
    # OpticalConfig: each kind requires its own fibre parameter, and the
    # power scale must be well defined (ER > 0 dB, RIN below 0 dB/Hz)
    (lambda: OpticalConfig(kind="dfb_smf"), "optical.kind"),
    (lambda: OpticalConfig(kind="vcsel_mmf"), "modal_bw_mhz_km"),
    (lambda: OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=0.0), "modal_bw_mhz_km"),
    (lambda: OpticalConfig(kind="eml_smf"), "dispersion_ps_nm_km"),
    (lambda: OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, er_db=0.0), "er_db"),
    (lambda: OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, rin_db_hz=0.0), "rin_db_hz"),
    (lambda: OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, f_r_hz=0.0), "f_r_hz"),
    (lambda: OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, length_m=-1.0), "length_m"),
    (lambda: OpticalConfig(kind="eml_smf", dispersion_ps_nm_km=-1.5, tia_bw_hz=0.0), "tia_bw_hz"),
    (lambda: OpticalConfig(kind="eml_smf", dispersion_ps_nm_km=-1.5,
                           tia_noise_pa_sqrthz=-1.0), "tia_noise_pa_sqrthz"),
    (lambda: OpticalConfig(kind="eml_smf", dispersion_ps_nm_km=-1.5,
                           responsivity_a_w=0.0), "responsivity_a_w"),
    (lambda: OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, li_compression=1.0),
     "li_compression"),
    (lambda: OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, li_compression=-0.1),
     "li_compression"),
    # the two segments are multiplied point by point: one grid
    (lambda: TopologyConfig(seg_a=ChannelConfig(kind="analytic", n_freq=1024),
                            seg_b=ChannelConfig(kind="analytic", n_freq=2048)), "seg_b.n_freq"),
    (lambda: TopologyConfig(seg_a=ChannelConfig(kind="analytic", f_max=50e9),
                            seg_b=ChannelConfig(kind="analytic")), "seg_b.f_max"),
])
def test_invalid_config_is_rejected(factory, frag):
    with pytest.raises(ValueError) as exc:
        factory()
    assert frag in str(exc.value)          # message names the offending field


def test_valid_configs_still_construct():
    """Validation must not reject legitimate configurations."""
    c = LinkConfig()                        # library defaults (a GUI preset)
    assert c.ui > 0 and c.dt > 0
    # a profile clock is legal with a file; validation does not open the file
    # (that is the engine's job, with a path the app layer has resolved)
    ck = ClockConfig(kind="profile", file="data/clock_profiles/x.yaml", f0_hz=10e9)
    assert ck.kind == "profile" and ck.f0_hz == 10e9
    assert TxConfig().clock == ClockConfig()      # white, all zero: the old default
    c2 = LinkConfig(modulation="pam4", symbol_rate=106.25e9, osr=16)
    assert c2.dt > 0 and c2.bits_per_symbol == 2
    # a 1-tap FIR is a passthrough: its n_pre is unused and unconstrained
    TxConfig(fir_taps=(1.0,), fir_n_pre=1)
    # optional fields stay optional
    AdcConfig(enob=None)
    DfeConfig(sum_bw=None, tap_limits=None)
    CdrConfig(clamp=None)
    ChannelConfig(f_max=None)
    # an "off" optical config needs no fibre parameters at all
    OpticalConfig(kind="none")
    OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0)
    OpticalConfig(kind="eml_smf", dispersion_ps_nm_km=0.0)   # zero dispersion is a value


def test_optical_power_scale_is_fixed_by_oma_and_er():
    """``oma_dbm`` and ``er_db`` determine P_high / P_low uniquely."""
    o = OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0, oma_dbm=0.0, er_db=3.0)
    assert o.oma_w == pytest.approx(1e-3)
    assert o.p_high_w - o.p_low_w == pytest.approx(o.oma_w)
    assert o.p_high_w / o.p_low_w == pytest.approx(10 ** 0.3)


def test_topology_none_optics_normalise_to_no_topology():
    """A topology whose optics are "none" is the electrical link, and the
    engines see exactly one representation of that: ``topology is None``."""
    assert LinkConfig().topology is None
    assert LinkConfig(topology=TopologyConfig()).topology is None
    on = LinkConfig(topology=TopologyConfig(
        optical=OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0)))
    assert on.topology is not None and on.topology.optical.kind == "vcsel_mmf"
    assert on.topology.seg_a.kind == "analytic"    # segments default to a buildable trace


def test_derived_quantities_are_physical():
    c = LinkConfig(symbol_rate=53.125e9, osr=32)
    assert c.dt == pytest.approx(c.ui / 32)
    assert c.f_nyquist == pytest.approx(c.symbol_rate / 2)
    assert c.dt > 0                          # the regression this guards
