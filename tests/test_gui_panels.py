"""Every GUI tab, rendered the way a user reaches it: before any run, after a
failed run, and after real runs of each receiver architecture.

The other GUI tests build figures from canned data or render one panel each;
none of them had ever rendered the Jitter, Adaptation, CDR, ADC, Channel,
Sweeps or Eyes tabs on a real record, so a tab could break on a renamed
``extras`` key and only a user would notice. Here each tab is checked for
what the user should *read* on it -- the gate that says why it is empty, or
the cards that say it has data -- not merely that it rendered.

Also here: the sidebar callbacks (called through Dash's ``__wrapped__``, the
undecorated function, so no browser is needed) and the desktop launcher's
port and readiness helpers.
"""

from __future__ import annotations

import http.server
import threading

import pytest

pytest.importorskip("dash")
pytest.importorskip("yaml")

from dash import no_update  # noqa: E402
from test_gui_error_surfaces import _text  # noqa: E402

from halo_serdes_gui import config_bridge as cb  # noqa: E402
from halo_serdes_gui.app import PANELS, build_app  # noqa: E402
from halo_serdes_gui.runner import get as get_result  # noqa: E402
from halo_serdes_gui.runner import run_from_values  # noqa: E402

NEED_RUN = "press ▶ Run to populate this tab"


def _run(preset, **over):
    vals = cb.config_to_values(cb.load_preset(preset))
    vals["sim.n_symbols"] = 20000
    vals.update(over)
    return run_from_values(vals, collect_eye=True, collect_jitter=True)


@pytest.fixture(scope="module")
def recs():
    """One record per situation a tab has to handle. prbs7 so the mixed-signal
    run repeats its pattern often enough for the jitter decomposition."""
    return {
        "ms": _run("NRZ 16G mixed-signal", **{"sim.pattern": "prbs7"}),
        "adc": _run("PAM4 224G ADC (106 GBd)"),
        "bad": _run("NRZ 16G mixed-signal", symbol_rate=-1),
    }


def _render(tab_id, rec):
    return _text(next(p for p in PANELS if p.TAB_ID == tab_id).render(rec))


def test_every_tab_says_what_to_do_before_a_run():
    """No run yet: every tab that needs one asks for it. FEC is the
    exception -- the projection needs no run, so it shows the curves."""
    for p in PANELS:
        text = _text(p.render(None))
        if p.TAB_ID == "fec":
            assert "RS-KP4/KR4" in text and NEED_RUN not in text
        else:
            assert NEED_RUN in text, p.TAB_ID


def test_a_failed_run_shows_its_error_on_every_tab_that_needs_the_run(recs):
    """A config that does not build leaves the defaults in ``rec.cfg``; the
    run-driven tabs must show the config error, not a result."""
    rec = recs["bad"]
    assert not rec.ok and "symbol_rate must be > 0" in rec.error
    run_driven = {"single", "eyes", "dual", "jitter", "adapt", "cdr", "adc",
                  "sweeps", "crosstalk", "fixed", "jtol"}
    for p in PANELS:
        text = _text(p.render(rec))                 # no tab may raise
        if p.TAB_ID in run_driven:
            assert "symbol_rate must be > 0" in text, p.TAB_ID


def test_mixed_signal_run_fills_its_tabs_and_gates_the_adc_ones(recs):
    rec = recs["ms"]
    assert rec.ok, rec.error
    expect = {
        "single": ["pre-FEC BER", "Slicer SNR"],
        "eyes": ["Analog eye is reconstructed"],
        "dual": ["dual-engine cross-check"],
        "channel": ["Loss @ Nyquist", "Behavioral COM"],
        "ctle": ["Realized peaking"],
        "jitter": ["Per-stage jitter budget"],
        "adapt": ["Dotted lines mark converged tap values"],
        "cdr": ["CDR kind:"],
        "backchannel": ["Converged", "Trained taps"],
        "crosstalk": ["Baseline BER", "Multi-lane environment"],
        "jtol": ["Tx sinusoidal jitter is swept"],
        # gates: what an ADC-only or analytic-only tab says on this run
        "adc": ["Set rx.arch = adc_dsp"],
        "fixed": ["fixed-point replay needs an ADC run"],
        "sweeps": ["set channel.kind = analytic"],
        "optical": ["This link is electrical"],
    }
    for tab, needles in expect.items():
        text = _render(tab, rec)
        for n in needles:
            assert n in text, (tab, n)


def test_adc_run_fills_the_adc_tabs(recs):
    rec = recs["adc"]
    assert rec.ok, rec.error
    assert "Worst-lane SER" in _render("adc", rec)
    assert "ADC/DSP receiver" in _render("eyes", rec)
    assert "Bit-true replay of the captured ADC codes" in _render("fixed", rec)
    assert "KP4 reach" in _render("sweeps", rec)
    # a 13q pattern does not repeat in 20k symbols: the jitter tab says so
    assert "needs a repeating pattern" in _render("jitter", rec)


def test_the_statistical_engine_sees_the_adc_receivers_ffe(recs):
    """Called bare, the statistical engine scored the 106 GBd ADC preset's
    unequalised channel -- SER 0.32, StatEye BER 0.16 beside a time-domain
    1e-4 -- and the Single Run card, the Dual-Engine view and the crosstalk
    baseline all showed it. With the time run's FFE it lands within 2x of the
    time engine (89 errors at 2e5 symbols: 2.29e-4 vs 2.40e-4); a
    statistical-only run gets the MMSE starting FFE and is in the same
    decade, not at 0.16."""
    vals = cb.config_to_values(cb.load_preset("PAM4 224G ADC (106 GBd)"))
    vals.update({"sim.n_symbols": 200_000, "sim.engine": "both"})
    both = run_from_values(vals)
    assert both.sim.ber.n_errors > 30
    assert 0.5 < both.stat.ber / both.sim.ber.ber < 2.0
    alone = run_from_values({**vals, "sim.engine": "stat"})
    assert alone.stat.ber < 1e-2
    # the crosstalk study's baseline goes through the same equaliser
    from halo_serdes_app import studies
    assert studies.crosstalk_study(both)["baseline"] < 1e-2
    # a mixed-signal receiver has no FFE to hand over
    from halo_serdes_app.runner import stat_equaliser
    assert stat_equaliser(recs["ms"].cfg, None) == (None, 0)


def test_a_profile_clock_adds_the_clock_section(recs):
    """A phase-noise profile clock: the jitter tab plots it against what the
    CDR leaves and states the model's numbers."""
    fields = {f["path"]: f for _, _, fs in cb.SECTIONS for f in fs}
    profile = fields["tx.clock.file"]["options"][0]
    rec = _run("NRZ 16G mixed-signal", **{"sim.pattern": "prbs7", "sim.engine": "both",
                                          "tx.clock.kind": "profile",
                                          "tx.clock.file": profile})
    assert rec.ok, rec.error
    text = _render("jitter", rec)
    assert "Clock profile vs CDR" in text and profile in text
    assert "CDR tracking bandwidth" in text


# ------------------------------------------------------------ callbacks ---

@pytest.fixture(scope="module")
def callbacks():
    """The app's callbacks by name, undecorated."""
    app = build_app()
    out = {}
    for spec in app.callback_map.values():
        fn = getattr(spec.get("callback"), "__wrapped__", None)
        if fn is not None:
            out[fn.__name__] = fn
    return out


def _form():
    """The form's ids and values, as the ALL-pattern states hand them over."""
    vals = cb.config_to_values(cb.load_preset("NRZ 28G analytic (COM/xtalk)"))
    ids = [{"type": "cfg", "path": f["path"]} for _, _, fs in cb.SECTIONS for f in fs]
    return ids, [vals.get(i["path"]) for i in ids]


def test_live_values_follow_the_form_and_ignore_a_half_edited_one(callbacks):
    ids, vals = _form()
    chips, banner = callbacks["_live"](vals, ids)
    assert "Nyquist" in _text(chips)
    broken = [("abc" if i["path"] == "symbol_rate" else v) for i, v in zip(ids, vals)]
    assert callbacks["_live"](broken, ids) == (no_update, no_update)


def test_preset_load_and_yaml_round_trip_through_the_form(callbacks):
    ids, vals = _form()
    loaded = callbacks["_load_preset"](1, "PAM4 224G ADC (106 GBd)", ids)
    by_path = dict(zip((i["path"] for i in ids), loaded))
    assert by_path["rx.arch"] == "adc_dsp"

    text = callbacks["_export_yaml"](1, vals, ids)
    assert "schema_version" in text
    back, status = callbacks["_import_yaml"](1, text, ids)
    assert "imported" in _text(status)
    assert dict(zip((i["path"] for i in ids), back))["symbol_rate"] == \
        dict(zip((i["path"] for i in ids), vals))["symbol_rate"]

    unchanged, status = callbacks["_import_yaml"](1, "   ", ids)
    assert unchanged == [no_update] * len(ids) and "Nothing to import" in _text(status)
    broken = [("abc" if i["path"] == "symbol_rate" else v) for i, v in zip(ids, vals)]
    assert callbacks["_export_yaml"](1, broken, ids).startswith("# export failed")
    assert callbacks["_toggle_yaml"](1, False) is True


def test_run_renders_the_active_tab_and_tabs_switch_on_the_stored_run(callbacks):
    ids, vals = _form()
    vals = [20000 if i["path"] == "sim.n_symbols" else v for i, v in zip(ids, vals)]
    rid, content = callbacks["_run"](1, vals, ids, "channel")
    assert get_result(rid) is not None
    assert "Loss @ Nyquist" in _text(content)
    assert "pre-FEC BER" in _text(callbacks["_switch_tab"]("single", rid))
    # an unknown tab id falls back to Single Run; an unknown run to its empty state
    assert "pre-FEC BER" in _text(callbacks["_switch_tab"]("nope", rid))
    assert NEED_RUN in _text(callbacks["_switch_tab"]("single", "no-such-run"))


# -------------------------------------------------------------- desktop ---

def test_desktop_helpers_find_a_port_and_wait_for_the_server(monkeypatch):
    """The packaged build's launcher: a free port, and a readiness probe that
    says yes to a server that answers and gives up on one that does not.
    Importing it sets HALO_NO_JIT for the frozen build; undone here so the
    rest of the session keeps its JIT setting."""
    import importlib
    import os
    import sys

    had = "HALO_NO_JIT" in os.environ
    try:
        desktop = importlib.import_module("halo_serdes_gui.desktop")
    finally:
        if not had:
            os.environ.pop("HALO_NO_JIT", None)
    assert "halo_serdes_gui.desktop" in sys.modules

    port = desktop._free_port()
    assert 1024 <= port < 65536

    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert desktop._wait_up(f"http://127.0.0.1:{server.server_port}/", timeout=5.0)
    finally:
        server.shutdown()
        server.server_close()
    assert not desktop._wait_up(f"http://127.0.0.1:{desktop._free_port()}/", timeout=0.5)
