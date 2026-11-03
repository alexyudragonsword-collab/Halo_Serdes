"""The ADC receiver's non-LTI blocks in the statistical engine (2026-11-03).

Time-interleave lane mismatch enters as white noise over the draws
(``stat_noise.ti_mismatch_sigma``); clipping is not modelled and warns.
The survey behind both: cairn/统计引擎非线性项.md.
"""

from __future__ import annotations

import dataclasses
import warnings

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel
from halo_serdes.engine import run_time_link
from halo_serdes.engine.stat_noise import ti_mismatch_sigma
from halo_serdes.engine.statistical import run_statistical
from halo_serdes_app import config_bridge as cb


def _adc(cfg, **kw):
    cal = kw.pop("cal", None)
    adc = dataclasses.replace(cfg.rx.adc, **kw)
    if cal is not None:
        adc = dataclasses.replace(adc, cal=dataclasses.replace(adc.cal, **cal))
    return dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, adc=adc))


@pytest.fixture(scope="module")
def base():
    c = cb.load_preset("PAM4 224G ADC (106 GBd)")
    return dataclasses.replace(c, rx=dataclasses.replace(c.rx, noise_rms=0.0025))


def test_lane_mismatch_variance_follows_the_calibration(base):
    o, g, s, x2, xd2 = 2e-3, 0.03, 0.02, 4e-3, 9e-3
    off = _adc(base, offset_sigma=o, gain_sigma=g, skew_sigma_ui=s)
    full = np.sqrt(o ** 2 + g ** 2 * x2 + s ** 2 * xd2)
    assert ti_mismatch_sigma(off, x2, xd2) == pytest.approx(full)
    skew_only = s * np.sqrt(xd2)
    # ideal calibration zeroes offset and gain, never skew
    assert ti_mismatch_sigma(_adc(off, calibrated=True), x2, xd2) == pytest.approx(skew_only)
    # the loops' residuals are left out; a background loop with mu_skew trims skew
    assert ti_mismatch_sigma(_adc(off, cal={"mode": "foreground"}), x2, xd2) == \
        pytest.approx(skew_only)
    assert ti_mismatch_sigma(_adc(off, cal={"mode": "background"}), x2, xd2) == \
        pytest.approx(skew_only)
    assert ti_mismatch_sigma(_adc(off, cal={"mode": "background", "mu_skew": 0.01}),
                             x2, xd2) == 0.0
    assert ti_mismatch_sigma(base, x2, xd2) == 0.0


def _both(cfg, seeds=(1, 2), n=100_000):
    cm = ChannelModel.from_config(cfg)
    ne = nc = 0
    taps = None
    for sd in seeds:
        t = run_time_link(dataclasses.replace(
            cfg, sim=dataclasses.replace(cfg.sim, seed=sd, n_symbols=n)), channel=cm)
        ne, nc = ne + t.ber.n_errors, nc + t.ber.n_checked
        taps = t.ffe_taps if taps is None else taps
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = run_statistical(cfg, channel=cm, ffe_taps=taps, ffe_pre=cfg.rx.ffe.n_pre).ber
    return s, ne / nc, ne, taps, cm


def test_lane_offset_mismatch_is_inside_the_cross_check(base):
    """2 mV of lane offset on 2.5 mV of noise: 0.37x of the time engine when the
    statistical engine left it out, 1.26x over six draws with it."""
    cfg = _adc(base, offset_sigma=0.002)
    stat, time, n_err, taps, cm = _both(cfg)
    assert n_err > 50                          # enough errors to compare
    assert 0.5 < stat / time < 2.0, (stat, time)
    without = run_statistical(base, channel=cm, ffe_taps=taps, ffe_pre=base.rx.ffe.n_pre).ber
    assert stat > 2.0 * without                # the model is what carries it


def test_a_clipping_adc_warns_and_a_roomy_one_does_not(base):
    cm = ChannelModel.from_config(base)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        run_statistical(base, channel=cm)
    assert not [x for x in w if "fullscale" in str(x.message)]
    with pytest.warns(UserWarning, match="rx.adc.fullscale=0.3 V"):
        run_statistical(_adc(base, fullscale=0.3), channel=cm)
