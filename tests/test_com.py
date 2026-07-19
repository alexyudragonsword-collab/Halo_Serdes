"""IEEE 802.3 COM (analysis/com.py) — the faithful PDF-based method."""

import numpy as np

from halo_serdes.analysis.com import ComParams, compute_com
from halo_serdes.channel import ChannelModel, synthetic_aggressor
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.io import Com93a, ComResult


def _cfg(length=0.20, modulation="pam4", rate=53.125e9, n_dfe=1, rj=0.0, dcd=0.0):
    return LinkConfig(
        modulation=modulation, symbol_rate=rate, osr=32,
        channel=ChannelConfig(kind="analytic", length_m=length, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.1, 1.0, -0.15), fir_n_pre=1,
                    rj_ui=rj, dcd_ui=dcd),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                    dfe=DfeConfig(n_taps=n_dfe)),
        sim=SimConfig(n_symbols=1000, seed=1, pattern="prbs13q"))


def _com(cfg, **kw):
    ch = ChannelModel.from_config(cfg)
    return compute_com(ch, cfg, **kw)


def test_com_decreases_with_loss():
    coms = [_com(_cfg(length=L)).com_db for L in (0.10, 0.20, 0.35)]
    assert coms[0] > coms[1] > coms[2]      # more loss -> lower COM


def test_com_drops_with_crosstalk():
    cfg = _cfg(length=0.18)
    ch = ChannelModel.from_config(cfg)
    clean = compute_com(ch, cfg).com_db
    aggs = [synthetic_aggressor("fext", -28, cfg.ui, cfg.dt,
                                modulation="pam4", seed=3).pulse(cfg.osr),
            synthetic_aggressor("next", -30, cfg.ui, cfg.dt,
                                modulation="pam4", seed=4).pulse(cfg.osr)]
    withxt = compute_com(ch, cfg, xtalk_pulses=aggs).com_db
    assert withxt < clean
    r = compute_com(ch, cfg, xtalk_pulses=aggs)
    assert r.fom_xtalk > 0 and r.detail["n_aggressors"] == 2


def test_com_improves_with_more_dfe():
    cfg = _cfg(length=0.35)
    ch = ChannelModel.from_config(cfg)
    prev = -1e9
    for nb in (0, 1, 2, 4):
        com = compute_com(ch, cfg, params=ComParams(n_dfe=nb)).com_db
        assert com >= prev - 1e-9           # non-decreasing in DFE taps
        prev = com


def test_com_selects_a_ctle_setting_and_phase():
    r = _com(_cfg(length=0.25))
    assert r.detail["ctle_peak_db"] in {0.0, 2.0, 4.0, 6.0, 8.0, 10.0}
    assert -16 <= r.detail["sample_phase"] <= 16
    # A_s, A_ni positive; COM consistent with 20log10(A_s/A_ni)
    assert r.a_signal > 0 and r.a_noise > 0
    assert abs(r.com_db - 20 * np.log10(r.a_signal / r.a_noise)) < 1e-6


def test_com_jitter_lowers_margin():
    base = _com(_cfg(length=0.20)).com_db
    jit = _com(_cfg(length=0.20, rj=0.01, dcd=0.02))
    assert jit.fom_jitter > 0
    assert jit.com_db < base                # jitter eats margin


def test_com_deterministic_and_nrz():
    cfg = _cfg(length=0.30, modulation="nrz", rate=32e9)
    a = _com(cfg).com_db
    b = _com(cfg).com_db
    assert a == b                           # no RNG in the analytic COM
    assert np.isfinite(a)


def test_com93a_adapter_matches_engine():
    cfg = _cfg(length=0.22)
    ch = ChannelModel.from_config(cfg)
    res = Com93a().compute(ch, cfg)
    assert isinstance(res, ComResult)
    assert res.detail["method"] == "802.3-93A/178A"
    assert abs(res.com_db - compute_com(ch, cfg).com_db) < 1e-9
