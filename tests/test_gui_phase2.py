"""The GUI's phase-2 layout: the link as a chain of stages, four workspaces.

- Every configuration field sits in exactly one stage of the chain (``rx.arch``
  is the top bar's switch); a stage shows its common fields and the rest
  behind Advanced, and hiding never removes an input.
- The chain follows the configuration (an ADC stage only on an ADC receiver)
  and says what each stage is set to.
- Verdict / Diagnose / Studies / Compare replace the 17 flat tabs; picking a
  stage on the left in Diagnose shows that stage's view.
- Compare puts runs side by side and can run the shown run through the other
  receiver.
- The Verdict view of an ADC run shows the reconstructed FFE-output eye (it
  showed "no eye") and no CTLE-node TJ (151 %UI on the 224G preset).
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("dash")
pytest.importorskip("yaml")

from dash import no_update  # noqa: E402
from test_gui_error_surfaces import _text  # noqa: E402
from test_gui_panels import run_through_job  # noqa: E402

from halo_serdes_app import api  # noqa: E402
from halo_serdes_gui import app as gui_app  # noqa: E402
from halo_serdes_gui import config_bridge as cb  # noqa: E402
from halo_serdes_gui import field_meta as fm  # noqa: E402
from halo_serdes_gui.app import build_app  # noqa: E402
from halo_serdes_gui.config_form import advanced_count, render_form, visibility  # noqa: E402
from halo_serdes_gui.panels import single_run  # noqa: E402
from halo_serdes_gui.runner import run_from_values  # noqa: E402

NRZ = "NRZ 28G analytic (COM/xtalk)"
ADC = "PAM4 224G ADC (106 GBd)"


@pytest.fixture(scope="module")
def callbacks():
    out = {}
    for spec in build_app().callback_map.values():
        fn = getattr(spec.get("callback"), "__wrapped__", None)
        if fn is not None:
            out[fn.__name__] = fn
    return out


def _values(preset, **over):
    return {**cb.config_to_values(cb.load_preset(preset)), **over}


def _ids():
    return [{"type": "cfg", "path": p} for p in cb.ALL_PATHS]


def _walk_ids(node, kind):
    """Every pattern id of ``kind`` in a component tree."""
    out = []

    def walk(n):
        if isinstance(n, (list, tuple)):
            for c in n:
                walk(c)
            return
        cid = getattr(n, "id", None)
        if isinstance(cid, dict) and cid.get("type") == kind:
            out.append(cid)
        kids = getattr(n, "children", None)
        if kids is not None:        # not `or []`: a childless component is falsy
            walk(kids)
    walk(node)
    return out


# ------------------------------------------------------------- the chain ---

def test_every_field_is_in_exactly_one_place():
    """The read-back needs every input in the page once: the stages hold all
    of them but rx.arch, which is the top bar's switch."""
    assert sorted(fm.STAGE_OF_SECTION) == sorted(s for s, _, _ in cb.SECTIONS)
    layout = build_app().layout
    paths = [i["path"] for i in _walk_ids(layout, "cfg")]
    assert sorted(paths) == sorted(cb.ALL_PATHS)
    in_form = [i["path"] for i in _walk_ids(render_form(_values(NRZ)), "cfg")]
    assert set(cb.ALL_PATHS) - set(in_form) == fm.NOT_IN_FORM == {"rx.arch"}


def test_a_stage_shows_its_common_fields_and_advanced_the_rest():
    v = _values(NRZ)
    rows = [{"type": "cfg-row", "path": p} for p in cb.ALL_PATHS if p not in fm.NOT_IN_FORM]
    secs = [{"type": "cfg-sec", "sid": s} for s, _, _ in cb.SECTIONS]
    stages = [{"type": "cfg-stage", "sid": s} for s, _, _ in fm.STAGES]

    def shown(stage, adv):
        r, _, st = visibility(v, rows, secs, stages, stage, adv)
        assert [x is None for x in st] == [sid == stage for sid, _, _ in fm.STAGES]
        stage_secs = next(x for sid, _, x in fm.STAGES if sid == stage)
        in_stage = {f["path"] for sid, _, fs in cb.SECTIONS if sid in stage_secs for f in fs}
        return {i["path"] for i, style in zip(rows, r) if style is None} & in_stage
    common, everything = shown("channel", False), shown("channel", True)
    assert common == {"channel.kind", "channel.length_m", "channel.r_skin",
                      "channel.loss_tangent", "crosstalk.n_fext", "crosstalk.n_next",
                      "crosstalk.coupling_db", "topology.optical.kind"}
    assert common < everything and "channel.rdc" in everything
    assert len(everything - common) == advanced_count("channel", v) == 12
    assert "channel.file" not in everything            # analytic: no file, even advanced


def test_the_chain_follows_the_configuration(callbacks):
    ids = _ids()
    for preset, has_adc in ((NRZ, False), (ADC, True)):
        v = _values(preset)
        cards, title, adv = callbacks["_chain"]([v.get(i["path"]) for i in ids], "eq",
                                                False, ids)
        names = [c.id["sid"] for c in cards]
        assert ("adc" in names) is has_adc and names[0] == "link"
        assert title == "Equaliser" and adv.startswith("Advanced (")
        active = [c for c in cards if "active" in c.className]
        assert [c.id["sid"] for c in active] == ["eq"]
    text = _text(cards)
    assert "8 b · ENOB 6.5 · 16 lanes" in text and "FFE 4+10 · DFE 1" in text


def test_picking_a_stage_in_diagnose_shows_its_view(callbacks, monkeypatch):
    monkeypatch.setattr("halo_serdes_gui.app.ctx",
                        SimpleNamespace(triggered_id={"type": "stage", "sid": "cdr"}))
    assert callbacks["_pick_stage"]([0, 1], "diag") == ("cdr", "cdr")
    assert callbacks["_pick_stage"]([0, 1], "verdict") == ("cdr", no_update)
    monkeypatch.setattr("halo_serdes_gui.app.ctx",
                        SimpleNamespace(triggered_id={"type": "stage", "sid": "fe"}))
    assert callbacks["_pick_stage"]([0, 1], "diag") == ("fe", "ctle")
    # the chain redrawn (n_clicks reset) is not a pick
    assert callbacks["_pick_stage"]([0, 0], "diag") == (no_update, no_update)


def test_workspaces_and_their_views(callbacks):
    opts, pick, style = callbacks["_subnav"]("diag", "mixed_signal", "single", "channel")
    assert pick == "channel" and style is None
    assert "adc" not in [o["value"] for o in opts]       # no ADC view on mixed-signal
    opts, _, _ = callbacks["_subnav"]("diag", "adc_dsp", "single", "channel")
    assert "adc" in [o["value"] for o in opts]
    opts, pick, _ = callbacks["_subnav"]("study", "adc_dsp", "jitter", "link")
    assert pick == "sweeps" and len(opts) == 8           # 2026-11-01: + JTOL (deep BER)
    assert callbacks["_subnav"]("study", "adc_dsp", "fec", "link")[1] == "fec"
    assert callbacks["_subnav"]("verdict", "adc_dsp", "fec", "link")[2] == {"display": "none"}
    assert callbacks["_set_view"]("verdict", "fec") == "single"
    assert callbacks["_set_view"]("compare", None) == "compare"
    assert callbacks["_set_view"]("study", "fec") == "fec"
    assert callbacks["_set_view"]("study", "channel") is no_update   # catching up
    # every view has exactly one workspace and every panel is reachable
    assert set(gui_app._WS_OF) == {p.TAB_ID for p in gui_app.PANELS}


def test_results_come_first_on_a_narrow_screen():
    row = next(c for c in build_app().layout.children
               if "app-body" in (getattr(c, "className", None) or ""))
    cfg_col, main_col = row.children
    assert "order-2" in cfg_col.className and "order-lg-1" in cfg_col.className
    assert "order-1" in main_col.className and "order-lg-2" in main_col.className


# ---------------------------------------------------------- the verdict ---

def test_an_adc_verdict_shows_the_reconstructed_eye_and_no_ctle_tj():
    rec = run_from_values(_values(ADC, **{"sim.n_symbols": 20000}),
                          collect_eye=True, collect_jitter=True)
    assert rec.ok, rec.error
    page = single_run.render(rec)
    text = _text(page)
    titles = []

    def walk(n):
        if isinstance(n, (list, tuple)):
            for c in n:
                walk(c)
            return
        fig = getattr(n, "figure", None)
        if fig is not None:
            titles.append(fig.layout.title.text or "")
            titles.extend(a.text for a in fig.layout.annotations or ())
        kids = getattr(n, "children", None)
        if kids is not None:
            walk(kids)
    walk(page)
    assert any("Reconstructed post-FFE eye" in t for t in titles), titles
    assert not any("no eye" in t for t in titles)
    assert "TJ@1e-12" not in text
    ms = run_from_values(_values(NRZ, **{"sim.n_symbols": 20000, "sim.pattern": "prbs7"}),
                         collect_eye=True, collect_jitter=True)
    assert "at the ctle output" in _text(single_run.render(ms))


def test_an_underflowed_stateye_says_so():
    from halo_serdes_gui.panels import common
    rec = SimpleNamespace(cfg=SimpleNamespace(modulation="nrz"), sim=None,
                          stat=SimpleNamespace(ber=0.0))
    assert common.verdict(rec)["calls"]["stat"] == ("pass", "0 (below double precision)")


# ------------------------------------------------------------- compare ---

def test_compare_puts_runs_side_by_side(callbacks):
    ids = _ids()
    vals = [_values(NRZ, **{"sim.n_symbols": 20000}).get(i["path"]) for i in ids]
    _, out_a = run_through_job(callbacks, vals, ids, "single", preset=NRZ)
    vals_b = [_values(NRZ, **{"sim.n_symbols": 20000, "channel.length_m": 0.36}).get(i["path"])
              for i in ids]
    _, out_b = run_through_job(callbacks, vals_b, ids, "single", preset=NRZ,
                               history=out_a[4])
    history = out_b[4]
    picker = callbacks["_switch_tab"]("compare", out_b[0], history, None)
    pick = picker.children[0].children[0]                 # the cmp-pick checklist
    assert pick.id == "cmp-pick"
    assert [o["label"].split()[0] for o in pick.options] == ["#2", "#1"]
    assert pick.value == [out_b[0], out_a[0]]             # the latest two by default
    assert "other architecture" in _text(picker)
    body, kept = callbacks["_cmp_body"]([out_b[0], out_a[0]], history)
    text = _text(body)
    assert kept == [out_b[0], out_a[0]]
    fig = body.children[0].figure
    assert fig.layout.title.text == "StatEye phase bathtubs" and len(fig.data) == 2
    assert "Slicer SNR" in text
    assert "Configuration differences (1)" in text and "Length [m] (analytic)" in text
    assert "Pick at least two runs" in _text(callbacks["_cmp_body"]([out_a[0]], history)[0])


def test_the_other_architecture_lands_beside_its_run(callbacks):
    ids = _ids()
    vals = [_values(NRZ, **{"sim.n_symbols": 20000}).get(i["path"]) for i in ids]
    _, out = run_through_job(callbacks, vals, ids, "compare", preset=NRZ)
    rid, history = out[0], out[4]
    ref, poll_on, run_off, _, _ = callbacks["_cmp_other"](1, rid, history)
    assert ref["compare_with"] == rid and poll_on is False and run_off is True
    job = api.get_job(ref["id"])
    assert job.values["rx.arch"] == "adc_dsp"
    deadline = time.monotonic() + 600
    while job.active and time.monotonic() < deadline:
        time.sleep(0.05)
    done, *_ = callbacks["_poll"](1, ref)
    new_rid, content, _, _, history = callbacks["_finish"](done, ref, history, "single")
    assert content.children[0].children[0].value == [rid, new_rid]   # picked together
    e = next(e for e in history if e["rid"] == new_rid)
    assert e["label"] == f"{NRZ} · 2 changed"   # preset carried over (+ symbols, arch)
    assert callbacks["_cmp_after_run"](done, ref) == ([rid, new_rid], "compare")
    # the picker shows the two together
    body, _ = callbacks["_cmp_body"]([rid, new_rid], history)
    text = _text(body)
    assert "mixed-signal" in text and "ADC + DSP" in text
