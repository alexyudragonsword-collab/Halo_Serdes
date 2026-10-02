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
    # COM falls as loss grows (both the RSS FoM and the faithful 802.3 COM)
    assert c["com_db"][0] > c["com_db"][-1]
    assert "com_93a" in c and c["com_93a"][0] > c["com_93a"][-1]


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


def test_optical_page_renders_every_section_on_the_lpo_preset():
    """The Optical tab draws its cascade, reach and TDECQ sections from the
    studies module the GUI imports -- a study missing from that re-export
    raises on render, which is how the stage-2 page shipped broken."""
    pytest.importorskip("dash", reason="GUI extra not installed")
    from halo_serdes_app import config_bridge as cb
    from halo_serdes_app import runner
    from halo_serdes_gui import studies as gui_studies
    from halo_serdes_gui.panels import optical

    assert gui_studies.optical_study is not None and gui_studies.tdecq_study is not None
    vals = cb.config_to_values(cb.load_preset("PAM4 100G/λ LPO (VCSEL + OM4)"))
    vals["sim.n_symbols"] = 20000
    vals["topology.optical.li_compression"] = 0.2
    rec = runner.run_from_values(vals, engines=("stat",))
    assert rec.ok, rec.error
    body = str(optical.render(rec))
    for section in ("Chain loss @ Nyquist", "Reach over fibre length", "TDECQ (after fibre)",
                    "Optical R_LM", "TDECQ vs extinction ratio"):
        assert section in body, section
    # an electrical link gets the explanation, not an exception
    elec = runner.run_from_values(cb.config_to_values(cb.load_preset("NRZ 28G analytic (COM/xtalk)")),
                                  engines=("stat",))
    assert "This link is electrical" in str(optical.render(elec))


def test_gui_shim_reexports_every_registered_study():
    # The panels import the old ``halo_serdes_gui.studies`` path; a study
    # registered only in the app layer passes every app/Android test and
    # fails when the desktop page is opened.
    from halo_serdes_app import api

    missing = [fn.__name__ for fn in api._STUDIES.values() if not hasattr(studies, fn.__name__)]
    assert not missing, f"add to halo_serdes_gui/studies.py: {missing}"
