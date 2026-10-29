"""The GUI's phase-0 changes, each checked for what the user reads.

- A preset loads when it is chosen (no Load button).
- The form shows only what the configuration uses: the ADC and partial-
  response groups on an ADC receiver, the RLGC line on an analytic channel,
  the optical fields on an optical link.
- The results say when the form has moved on from the run they show.
- One spelling for every rate (``1.1e-5``, ``< 5e-5``), and a verdict against
  the FEC threshold from each engine, with the time / StatEye ratio.
- The run's warnings once, above the tabs, the envelope one left to the
  sidebar and the solvers' numerical noise folded away.
- Study tabs wait for their own Run button and keep what they computed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("dash")
pytest.importorskip("yaml")

from test_gui_error_surfaces import _text  # noqa: E402
from test_gui_panels import run_through_job  # noqa: E402

from halo_serdes_gui import app as gui_app  # noqa: E402
from halo_serdes_gui import config_bridge as cb  # noqa: E402
from halo_serdes_gui import field_meta  # noqa: E402
from halo_serdes_gui.app import PANELS, build_app  # noqa: E402
from halo_serdes_gui.config_form import render_form  # noqa: E402
from halo_serdes_gui.panels import common  # noqa: E402

NRZ = "NRZ 28G analytic (COM/xtalk)"
ADC = "PAM4 224G ADC (106 GBd)"
LPO = "PAM4 100G/λ LPO (VCSEL + OM4)"


@pytest.fixture(scope="module")
def callbacks():
    """The app's callbacks by name, undecorated."""
    out = {}
    for spec in build_app().callback_map.values():
        fn = getattr(spec.get("callback"), "__wrapped__", None)
        if fn is not None:
            out[fn.__name__] = fn
    return out


def _values(preset):
    return cb.config_to_values(cb.load_preset(preset))


def _ids():
    return [{"type": "cfg", "path": p} for p in cb.ALL_PATHS]


# ------------------------------------------------------------- the form ---

def test_every_help_text_and_rule_names_a_real_field():
    """A typo in a path would silently never show (or never hide)."""
    for path in list(field_meta.HELP) + list(field_meta.FIELD_SHOWN):
        assert path in cb.FIELD_BY_PATH, path
    sids = {sid for sid, _, _ in cb.SECTIONS}
    assert set(field_meta.SECTION_SHOWN) <= sids


def test_the_form_shows_what_the_configuration_uses():
    ms, adc, lpo = _values(NRZ), _values(ADC), _values(LPO)
    shown = field_meta.section_shown
    assert not shown("adc", ms) and not shown("pr", ms)
    assert shown("adc", adc) and shown("pr", adc)
    f = field_meta.field_shown
    # analytic channel: the line, not the file
    assert ms["channel.kind"] == "analytic"
    assert f("channel.r_skin", ms) and not f("channel.file", ms)
    touch = {**ms, "channel.kind": "touchstone"}
    assert f("channel.file", touch) and not f("channel.r_skin", touch)
    # an electrical link hides every optical field but the switch itself
    assert f("topology.optical.kind", ms)
    assert not f("topology.optical.oma_dbm", ms) and not f("topology.seg_a.length_m", ms)
    assert f("topology.optical.oma_dbm", lpo)
    # NRZ has no inner levels to mismatch
    assert not f("tx.rlm", ms) and f("tx.rlm", adc)
    hidden = [p for p in cb.ALL_PATHS if not f(p, ms)]
    assert len(hidden) > 50          # most of the 162 are not this link's


def test_a_partial_response_target_keeps_its_section_on_a_mixed_signal_receiver():
    """The config error it causes there names pr.target; the field has to be
    on screen to fix it."""
    v = {**_values(NRZ), "pr.target": "1.0, 0.5"}
    assert field_meta.section_shown("pr", v)


def test_labels_carry_their_help_and_rows_their_visibility():
    form = render_form(_values(NRZ), stage="channel")
    text = _text(form)
    assert field_meta.HELP["channel.r_skin"] in text      # the label's title
    secs = {sec.id["sid"]: sec for stage in form.children for sec in stage.children}
    assert secs["adc"].style == {"display": "none"} and secs["channel"].style is None


def test_the_visibility_callback_follows_the_architecture(callbacks):
    ids = _ids()
    rows = [{"type": "cfg-row", "path": p} for p in cb.ALL_PATHS]
    secs = [{"type": "cfg-sec", "sid": sid} for sid, _, _ in cb.SECTIONS]
    order = [s["sid"] for s in secs]
    stages = [{"type": "cfg-stage", "sid": sid} for sid, _, _ in field_meta.STAGES]
    for preset, adc_shown in ((NRZ, False), (ADC, True)):
        v = _values(preset)
        _, sec_styles, _ = callbacks["_visibility"]([v.get(i["path"]) for i in ids],
                                                    "adc", True, ids, rows, secs, stages)
        assert (sec_styles[order.index("adc")] is None) is adc_shown


def test_choosing_a_preset_loads_it(callbacks):
    """The preset menu is the callback's input now; there is no Load button."""
    ids = _ids()
    by_path = dict(zip(cb.ALL_PATHS, callbacks["_load_preset"](ADC, ids)))
    assert by_path["rx.arch"] == "adc_dsp"
    assert "preset-load" not in str(build_app().layout)


def test_the_results_say_when_the_form_has_moved_on():
    v = _values(NRZ)
    assert gui_app._stale_banner(v, None) is None           # nothing run yet
    assert gui_app._stale_banner(v, dict(v)) is None        # unchanged
    banner = _text(gui_app._stale_banner({**v, "rx.ctle.peak_db": 9.0}, v))
    assert "earlier configuration" in banner and "Peaking [dB]" in banner


# ------------------------------------------------------------ the cards ---

def test_one_spelling_for_every_rate():
    assert common.sci(2.4e-4) == "2.4e-4"
    assert common.sci(1.06e-5) == "1.1e-5"
    assert common.sci(0.0) == "0"
    assert common.rate_text(0.0, 20000) == ("< 5.0e-5", "good")
    assert common.rate_text(0.0, 20000)[0] == common.ber_text(0.0, 20000)[0]


def _rec(modulation="pam4", ber=None, errors=0, n=0, stat=None):
    sim = None
    if ber is not None:
        sim = SimpleNamespace(ber=SimpleNamespace(ber=ber, n_errors=errors, n_checked=n))
    return SimpleNamespace(cfg=SimpleNamespace(modulation=modulation), sim=sim,
                           stat=None if stat is None else SimpleNamespace(ber=stat))


def test_the_verdict_is_each_engines_call_and_their_agreement():
    _, kp4 = common.fec_target(SimpleNamespace(modulation="pam4"))
    code, kr4 = common.fec_target(SimpleNamespace(modulation="nrz"))
    assert 2.0e-4 < kp4 < 2.4e-4 and kr4 < kp4 and code.startswith("KR4")
    # both engines measured below the threshold: pass, with their ratio
    v = common.verdict(_rec(ber=4e-6, errors=8, n=2_000_000, stat=8e-6))
    assert v["overall"] == "pass" and v["ratio"] == pytest.approx(0.5)
    # one over, one under
    assert common.verdict(_rec(ber=1e-3, errors=900, n=900_000, stat=1e-5))["overall"] \
        == "disagree"
    assert common.verdict(_rec(ber=0.49, errors=95_000, n=193_000, stat=0.097))["overall"] \
        == "fail"
    # no errors in too few bits says nothing; StatEye then decides alone
    v = common.verdict(_rec("nrz", ber=0.0, n=20_000, stat=1e-30))
    assert v["calls"]["time"][0] == "open" and v["overall"] == "pass"
    assert v["ratio"] is None
    assert common.verdict(_rec("nrz", ber=0.0, n=20_000))["overall"] == "open"


def test_single_run_fills_its_verdict_after_the_cards(callbacks):
    ids = _ids()
    vals = [20000 if i["path"] == "sim.n_symbols" else _values(NRZ).get(i["path"])
            for i in ids]
    _, (rid, content, *_rest) = run_through_job(callbacks, vals, ids, "single")
    text = _text(content)
    assert "pre-FEC BER" in text and "Computing the StatEye cross-check" in text
    verdict = _text(callbacks["_fill_verdict"]({"type": "verdict", "rid": rid}))
    assert "KR4" in verdict and "Pass" in verdict and "StatEye BER" in verdict
    # the time run had no errors: no ratio to judge, and it says why
    assert "no time-domain errors to compare" in verdict
    # once known, the tab renders it directly
    assert "Computing" not in _text(callbacks["_switch_tab"]("single", rid))


# ------------------------------------------------------------- warnings ---

def test_warnings_once_without_the_envelope_or_the_solver_noise():
    rec = SimpleNamespace(ok=True, warnings=[
        "mixed_signal arch at 28 Gb/s NRZ is in the marginal zone ...",
        "overflow encountered in matmul",
        "Covariance of the parameters could not be estimated",
        "a real one", "a real one"])
    link, num = common.split_warnings(rec)
    assert link == ["a real one"] and len(num) == 2
    text = _text(common.warnings_block(rec))
    assert "a real one" in text and "2 numerical warnings" in text
    assert "marginal zone" not in text
    assert common.warnings_block(SimpleNamespace(ok=True, warnings=[])) is None


# ---------------------------------------------------------- study tabs ---

def test_study_tabs_wait_for_their_button_and_keep_the_result(callbacks, monkeypatch):
    studies = [p for p in PANELS if getattr(p, "STUDY", None)]
    assert {p.TAB_ID for p in studies} == {"sweeps", "crosstalk", "jtol", "amicom",
                                           "fixed", "optical"}
    ids = _ids()
    vals = [20000 if i["path"] == "sim.n_symbols" else _values(NRZ).get(i["path"])
            for i in ids]
    _, (rid, *_rest) = run_through_job(callbacks, vals, ids, "channel")
    calls = []
    for p in studies:
        monkeypatch.setattr(p, "render", lambda rec, p=p: calls.append(p.TAB_ID) or "done")
        gate = _text(callbacks["_switch_tab"](p.TAB_ID, rid))
        assert p.STUDY in gate and f"Run {p.TITLE}" in gate
    assert calls == []                         # switching computed nothing
    body, disabled = callbacks["_study"](1, "amicom", rid)
    assert body == "done" and disabled and calls == ["amicom"]
    assert callbacks["_switch_tab"]("amicom", rid) == "done"
    assert calls == ["amicom"]                 # kept, not recomputed


# ------------------------------------------------------- render hygiene ---

def _bare_figures(node, under_figure=False):
    """Plotly figures placed where Dash expects a component (React error #31
    in the browser, nothing on screen); a figure is only valid as a Graph's
    ``figure``."""
    import plotly.graph_objects as go
    if isinstance(node, go.Figure):
        return 0 if under_figure else 1
    if isinstance(node, (list, tuple)):
        return sum(_bare_figures(n) for n in node)
    n = 0
    for attr in ("children", "figure"):
        if hasattr(node, attr) and not isinstance(node, str):
            n += _bare_figures(getattr(node, attr), attr == "figure")
    return n


def test_no_tab_puts_a_figure_where_a_component_goes():
    """The Channel tab's COM chart was ``com_fig or placeholder``: a Graph with
    no children is falsy, so every run showed the bare placeholder figure."""
    from halo_serdes_gui.runner import run_from_values
    rec = run_from_values({**_values(NRZ), "sim.n_symbols": 20000},
                          collect_eye=True, collect_jitter=True)
    for p in PANELS:
        if getattr(p, "STUDY", None) and p.TAB_ID != "amicom":
            continue                       # seconds each; amicom is the cheap one
        assert _bare_figures(p.render(rec)) == 0, p.TAB_ID
    assert "COM unavailable" not in _text(next(p for p in PANELS
                                               if p.TAB_ID == "channel").render(rec))


def test_a_run_of_another_configuration_is_counted_not_listed():
    v = _values(NRZ)
    banner = _text(gui_app._stale_banner(_values(ADC), v))
    assert "different configuration" in banner and "fields differ" in banner
