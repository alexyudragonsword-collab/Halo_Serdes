"""GUI study sweeps: reach, crosstalk, COM, FEC projection, fixed-point."""

import dataclasses

import numpy as np
import pytest

pytest.importorskip("dash")

from halo_serdes_gui import config_bridge as cb  # noqa: E402
from halo_serdes_gui import runner, studies  # noqa: E402


def _adc_rec():
    cfg = cb.load_preset("PAM4 224G ADC (106 GBd)")
    cfg = dataclasses.replace(cfg, sim=dataclasses.replace(
        cfg.sim, n_symbols=12000, engine="time"))
    return runner.run_link(cfg)


def test_fec_projection_monotone():
    p = studies.fec_projection()
    # deeper pre-FEC BER -> lower post-FEC BER (monotone), concat below KP4
    assert p["kp4"][0] > p["kp4"][-1]
    assert np.all(p["concat"] <= p["kp4"] + 1e-30)


def test_reach_and_com_need_analytic_channel():
    rec = _adc_rec()  # analytic channel
    r = studies.reach_study(rec)
    assert "loss" in r and r["loss"].size == 8
    c = studies.com_study(rec)
    assert "com_db" in c and np.all(np.diff(c["loss"]) > 0)
    # COM falls as loss grows
    assert c["com_db"][0] > c["com_db"][-1]


def test_crosstalk_sweep_shape():
    rec = _adc_rec()
    x = studies.crosstalk_study(rec)
    assert x["coupling"].size == x["ber"].size == 6
    assert x["baseline"] > 0


def test_fixedpoint_wall_decreases_with_wordlength():
    rec = _adc_rec()
    fp = studies.fixedpoint_study(rec)
    assert "wl" in fp and fp["mismatch"][0] >= fp["mismatch"][-1]


def test_studies_gate_on_non_analytic_and_non_adc():
    # touchstone channel -> reach/com return an error note (no analytic length)
    cfg = cb.load_preset("NRZ 16G mixed-signal")
    cfg = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, n_symbols=4000))
    rec = runner.run_link(cfg)
    assert "error" in studies.reach_study(rec)
    # mixed-signal run has no ADC codes -> fixed-point returns an error note
    assert "error" in studies.fixedpoint_study(rec)
