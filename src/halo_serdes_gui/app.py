"""Halo_Serdes GUI — Dash application shell, sidebar, tabs, and callbacks.

Left sidebar edits a ``LinkConfig`` (presets, YAML I/O, live derived values,
mixed-signal envelope banner, the config form showing only what the
configuration uses). The main area is a set of capability tabs; each run
stashes its result in the server-side registry and every tab renders from it.
Above the tabs: the runs of this session (``history``), a banner when the form
has changed since the run shown, and that run's warnings, once. Study tabs
(``STUDY`` on the panel) wait for their own Run button.

Run starts a background job (``halo_serdes_app.api.start_job``, the same job
runner the phone uses) and an interval polls it: a progress bar with the stage
and the receiver loop's fraction, and a Cancel button that stops the run at the
next receiver chunk. Run the app with ``halo-serdes-gui`` or
``python -m halo_serdes_gui``.
"""

from __future__ import annotations

from collections import OrderedDict

import time

import dash_bootstrap_components as dbc
from dash import ALL, MATCH, Dash, Input, Output, State, ctx, dcc, html, no_update

from halo_serdes_app import api

from . import config_bridge as cb
from . import history as hist
from . import theme
from .config_form import render_form, values_from_states, visibility
from .panels import (
    adaptation, adc, ami_com, backchannel, cdr, channel, crosstalk, ctle,
    dual_engine, eyes, fec, fixed_point, jitter, jtol, optical, single_run, sweeps,
)
from .panels import common
from .runner import get as get_result

# --- panel registry --------------------------------------------------------
PANELS = [single_run, eyes, dual_engine, channel, ctle, jitter,
          adaptation, cdr, adc, backchannel,
          sweeps, fec, crosstalk, ami_com, fixed_point, jtol, optical]
_PANEL_BY_ID = {p.TAB_ID: p for p in PANELS}

# first-launch default: a runnable analytic preset (so Run works immediately
# and the sweep/COM tabs are populated), falling back to bare defaults.
_names = cb.preset_names()
DEFAULT_PRESET = next((n for n in _names if "analytic" in n), _names[0])
INITIAL = cb.config_to_values(cb.load_preset(DEFAULT_PRESET))


_HIDDEN = {"display": "none"}

#: How often the browser asks how the background run is doing.
POLL_MS = 400


def _sidebar():
    """Left column: preset picker, Run, live derived values, the mixed-signal
    envelope banner, YAML import/export and the generated config form."""
    return html.Div([
        html.Div([
            html.Img(src="/assets/icon.png", height="30px",
                     style={"marginRight": "0.5rem", "borderRadius": "6px"}),
            html.Div([
                html.Span("Halo", style={"fontWeight": 800, "color": theme.PRIMARY}),
                html.Span("_Serdes", style={"fontWeight": 800, "color": theme.INK}),
                html.Div("behavioral SerDes studio",
                         style={"fontSize": "0.72rem", "color": theme.MUTED,
                                "marginTop": "-0.15rem"}),
            ]),
        ], className="mb-2 d-flex align-items-center"),

        # choosing a preset loads it; there is no separate Load step
        dbc.Select(id="preset", options=[{"label": n, "value": n}
                   for n in cb.preset_names()], value=DEFAULT_PRESET,
                   size="sm", className="mb-2"),

        html.Div([
            dbc.Button("▶  Run", id="run-btn", color="primary", className="flex-grow-1"),
            dbc.Button("■ Cancel", id="cancel-btn", color="secondary", outline=True,
                       className="ms-2", style=_HIDDEN),
        ], className="d-flex mb-2"),
        html.Div(id="run-status"),

        html.Div(id="derived", className="mb-1"),
        html.Div(id="envelope"),

        dbc.Button("YAML ▾", id="yaml-toggle", size="sm", color="light",
                   className="w-100 mb-1 border"),
        dbc.Collapse(html.Div([
            dbc.Textarea(id="yaml-text", style={"height": "160px",
                         "fontFamily": "monospace", "fontSize": "0.72rem"}),
            dbc.ButtonGroup([
                dbc.Button("Export ↑", id="yaml-export", size="sm", color="light",
                           className="border"),
                dbc.Button("Import ↓", id="yaml-import", size="sm", color="light",
                           className="border"),
            ], className="mt-1 w-100"),
            # Import used to fail silently: a malformed paste returned no_update
            # for every field, so the button did nothing and said nothing. The
            # error lands here rather than in the textarea because overwriting
            # the textarea would destroy the text the user is trying to fix.
            html.Div(id="yaml-status"),
        ]), id="yaml-collapse", is_open=False, className="mb-2"),

        theme.section_title("Configuration"),
        html.Div(render_form(INITIAL), id="form-container"),
    ], style={"height": "100vh", "overflowY": "auto", "padding": "0.8rem",
              "borderRight": f"1px solid {theme.GRID}", "background": "#fbfcfe"})


def _main():
    """Right column: one tab per registered panel, rendered into a single
    content area (only the active tab is built)."""
    tabs = [dbc.Tab(label=p.TITLE, tab_id=p.TAB_ID) for p in PANELS]
    return html.Div([
        html.Div(id="history-strip"),
        html.Div(id="stale"),
        html.Div(id="run-warnings"),
        dbc.Tabs(tabs, id="tabs", active_tab=PANELS[0].TAB_ID, className="mb-2"),
        # the spinner covers the tab only while the tab itself is being built:
        # the verdict and a study's result fill in inside it, and must not
        # blank the cards already on screen while they compute
        dcc.Loading(html.Div(single_run.render(None), id="tab-content"),
                    type="default", color=theme.PRIMARY,
                    target_components={"tab-content": "children"}),
    ], style={"height": "100vh", "overflowY": "auto", "padding": "0.8rem 1rem"})


def build_app() -> Dash:
    """The Dash app with its layout and callbacks; what ``main`` and the
    desktop launcher serve."""
    # Bootstrap (Flatly) is vendored in assets/00_bootstrap.min.css and loaded
    # automatically, so the app is fully self-contained (no CDN needed). Pass an
    # absolute assets path so a frozen build (PyInstaller/Nuitka) still finds it.
    import os

    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    app = Dash(__name__, title="Halo_Serdes", assets_folder=assets,
               suppress_callback_exceptions=True)
    app.layout = dbc.Container([
        dcc.Store(id="run-store"),
        dcc.Store(id="run-values"),
        dcc.Store(id="job"),
        dcc.Store(id="job-done"),
        dcc.Store(id="history", data=[]),
        dcc.Interval(id="job-poll", interval=POLL_MS, disabled=True),
        dbc.Row([
            dbc.Col(_sidebar(), width=12, lg=4, xl=3, style={"padding": 0}),
            dbc.Col(_main(), width=12, lg=8, xl=9, style={"padding": 0}),
        ], className="g-0"),
    ], fluid=True, style={"padding": 0})
    _register_callbacks(app)
    return app


# --- study tabs --------------------------------------------------------------
# A study (reach sweep, crosstalk, JTOL, COM, fixed point, optical) costs from
# a second to ~20 s. Computing it on tab switch left the tab spinning with no
# word of what it was doing; it now waits for its own Run button, and a result
# is kept per (run, tab) so coming back to the tab does not recompute it.
_STUDY_CACHE: OrderedDict[tuple[str, str], object] = OrderedDict()
_STUDY_CACHE_MAX = 24


def _study_gate(panel, rec):
    """A study tab before it has run: what it computes, and its Run button."""
    return html.Div([
        html.Div(panel.STUDY, style={"fontSize": "0.85rem", "color": theme.INK,
                                     "marginBottom": "0.5rem"}),
        dbc.Button(f"▶  Run {panel.TITLE}", id="study-run", color="primary",
                   size="sm", className="mb-2"),
        dcc.Loading(html.Div(id="study-body"), type="default", color=theme.PRIMARY),
    ])


def _tab_view(panel, rec):
    """What a tab shows for ``rec``: a study's gate (or its kept result), or
    the panel itself."""
    if getattr(panel, "STUDY", None) and rec is not None and rec.ok:
        kept = _STUDY_CACHE.get((rec.id, panel.TAB_ID))
        return kept if kept is not None else _study_gate(panel, rec)
    return panel.render(rec)


def _run_study(panel, rec):
    """Compute a study tab for ``rec`` and keep the result."""
    out = panel.render(rec)
    _STUDY_CACHE[(rec.id, panel.TAB_ID)] = out
    while len(_STUDY_CACHE) > _STUDY_CACHE_MAX:
        _STUDY_CACHE.popitem(last=False)
    return out


def _progress(job):
    """The running job's stage, elapsed time and progress bar. The fraction
    is the receiver loop's; outside it (channel, StatEye) the bar is an
    indeterminate stripe."""
    if job is None:
        return None
    elapsed = time.perf_counter() - job.started
    if job.cancel.is_set():
        # the time engine's front end (transmitter, channel filtering) has no
        # cancel point inside it: ~10 s per million symbols on a desktop
        text = "Cancelling — stops when the current stage ends…"
    else:
        text = f"{job.stage or 'starting'} · {elapsed:.1f} s"
    frac = job.fraction
    bar = dbc.Progress(value=100 if frac is None else 100 * frac,
                       label="" if frac is None else f"{100 * frac:.0f} %",
                       striped=frac is None, animated=frac is None,
                       style={"height": "14px"})
    return html.Div([html.Div(text, style={"fontSize": "0.75rem", "color": theme.MUTED}),
                     bar], className="mb-2")


def _stale_banner(now: dict, then: dict | None):
    """The banner for a form that has moved on from the run shown, or None."""
    if not then:
        return None
    changed = [p for p in now if now.get(p) != then.get(p)]
    if not changed:
        return None
    labels = [cb.FIELD_BY_PATH[p]["label"] for p in changed if p in cb.FIELD_BY_PATH]
    more = f" and {len(labels) - 3} more" if len(labels) > 3 else ""
    return theme.banner(
        "warn", f"These results are from an earlier configuration: "
                f"{', '.join(labels[:3])}{more} changed since the run. "
                f"Press ▶ Run to update them.")


def _aligned(target: dict, ids: list[dict]) -> list:
    """Values for the form's ALL-pattern ids, in their order; fields the
    source does not set stay as they are (``no_update``)."""
    return [target.get(i["path"], no_update) for i in ids]


def _register_callbacks(app: Dash) -> None:
    """Wire the sidebar and the tabs. Every callback that can fail on user
    input reports the reason on screen rather than raising into Dash,
    where it would only reach the browser console."""

    @app.callback(Output("derived", "children"), Output("envelope", "children"),
                  Input({"type": "cfg", "path": ALL}, "value"),
                  State({"type": "cfg", "path": ALL}, "id"))
    def _live(vals, ids):
        """Derived values and the envelope banner as the form changes; a form that
        does not build yet (mid-edit) leaves the previous ones up."""
        try:
            cfg = cb.build_config(values_from_states(ids, vals))
        except Exception:
            return no_update, no_update
        d = cb.derived(cfg)
        chips = [dbc.Badge(f"{k}: {v}", color="light",
                           className="me-1 mb-1 border text-dark",
                           style={"fontWeight": 500}) for k, v in d.items()]
        lvl, msg = cb.envelope_status(cfg)
        return html.Div(chips), theme.banner(lvl, msg)

    @app.callback(Output({"type": "cfg", "path": ALL}, "value"),
                  Input("preset", "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  prevent_initial_call=True)
    def _load_preset(name, ids):
        """Fill the form from the preset as soon as it is chosen."""
        return _aligned(cb.config_to_values(cb.load_preset(name)), ids)

    @app.callback(Output({"type": "cfg-row", "path": ALL}, "style"),
                  Output({"type": "cfg-sec", "sid": ALL}, "style"),
                  Input({"type": "cfg", "path": ALL}, "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  State({"type": "cfg-row", "path": ALL}, "id"),
                  State({"type": "cfg-sec", "sid": ALL}, "id"),
                  prevent_initial_call=True)
    def _visibility(vals, ids, row_ids, sec_ids):
        """Show the fields and sections the configuration uses (architecture,
        channel kind, optics, ...); the rest stay in the form, hidden."""
        return visibility(values_from_states(ids, vals), row_ids, sec_ids)

    @app.callback(Output("stale", "children"),
                  Input({"type": "cfg", "path": ALL}, "value"),
                  Input("run-values", "data"),
                  State({"type": "cfg", "path": ALL}, "id"))
    def _stale(vals, then, ids):
        """Say so when the form no longer matches the run on screen."""
        return _stale_banner(values_from_states(ids, vals), then)

    @app.callback(Output({"type": "cfg", "path": ALL}, "value",
                         allow_duplicate=True),
                  Output("yaml-status", "children"),
                  Input("yaml-import", "n_clicks"),
                  State("yaml-text", "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  prevent_initial_call=True)
    def _import_yaml(_n, text, ids):
        """Fill the form from pasted YAML; an empty or invalid paste leaves every
        field unchanged and says why."""
        if not (text or "").strip():
            return [no_update] * len(ids), theme.banner(
                "warn", "Nothing to import — paste a config into the box first.")
        try:
            cfg = cb.yaml_to_config(text)
        except Exception as exc:
            # The reason, not just a failure: a schema rejection names the
            # offending field, and that is the whole value of the message.
            return [no_update] * len(ids), theme.banner(
                "crit", f"Import failed, fields unchanged. {type(exc).__name__}: {exc}")
        return _aligned(cb.config_to_values(cfg), ids), theme.banner(
            "ok", "Config imported into the form.")

    @app.callback(Output("yaml-text", "value"),
                  Input("yaml-export", "n_clicks"),
                  State({"type": "cfg", "path": ALL}, "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  prevent_initial_call=True)
    def _export_yaml(_n, vals, ids):
        """The form as YAML, or the reason it does not build."""
        try:
            return cb.config_to_yaml(cb.build_config(values_from_states(ids, vals)))
        except Exception as exc:
            return f"# export failed: {exc}"

    @app.callback(Output("yaml-collapse", "is_open"),
                  Input("yaml-toggle", "n_clicks"),
                  State("yaml-collapse", "is_open"), prevent_initial_call=True)
    def _toggle_yaml(_n, is_open):
        """Open / close the YAML card."""
        return not is_open

    @app.callback(Output("job", "data"), Output("job-poll", "disabled"),
                  Output("run-btn", "disabled"), Output("cancel-btn", "style"),
                  Output("run-status", "children"),
                  Input("run-btn", "n_clicks"),
                  State({"type": "cfg", "path": ALL}, "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  State("preset", "value"), prevent_initial_call=True)
    def _start(_n, vals, ids, preset):
        """Start the form's config on a background job (eye and jitter capture
        on, so those tabs have data) and start polling it. A run already
        going (another browser tab, a reload) is followed rather than refused."""
        values = values_from_states(ids, vals)
        try:
            # capture jitter opportunistically so the Jitter tab has data when
            # the pattern repeats enough; harmless (returns a note otherwise)
            job = api.start_job(values, collect_eye=True, collect_jitter=True)
        except api.Busy as exc:
            job = api.get_job(exc.job)
            # following a run that is being cancelled would show "cancelled"
            # for a press of Run; one that finished in between is just gone
            if job is None or job.cancel.is_set():
                return no_update, True, False, _HIDDEN, theme.banner(
                    "warn", "The previous run is still stopping; press ▶ Run again "
                            "in a moment.")
        return {"id": job.id, "preset": preset}, False, True, None, _progress(job)

    @app.callback(Output("job-done", "data"),
                  Output("job-poll", "disabled", allow_duplicate=True),
                  Output("run-btn", "disabled", allow_duplicate=True),
                  Output("cancel-btn", "style", allow_duplicate=True),
                  Output("run-status", "children", allow_duplicate=True),
                  Input("job-poll", "n_intervals"), State("job", "data"),
                  prevent_initial_call=True)
    def _poll(_n, ref):
        """The running job's progress; when it ends, polling stops and the
        finished job is handed to ``_finish``. Nothing here outputs to the tab
        area: the spinner over it would cover the page at every poll."""
        job = api.get_job((ref or {}).get("id"))
        idle = (True, False, _HIDDEN)
        if job is None:
            return (no_update, *idle, None)
        if job.active:
            return (no_update, no_update, no_update, no_update, _progress(job))
        if job.state == "cancelled":
            return (no_update, *idle, theme.banner(
                "info", f"Run cancelled after {job.elapsed_s:.1f} s; the results "
                        "shown are from the previous run."))
        if get_result(job.record) is None:      # the job died outside the run itself
            msg = (job.error or {}).get("message", "unknown error")
            return (no_update, *idle, theme.banner("crit", f"Run failed: {msg}"))
        return (job.id, *idle, None)

    @app.callback(Output("run-store", "data"), Output("tab-content", "children"),
                  Output("run-values", "data"), Output("run-warnings", "children"),
                  Output("history", "data"),
                  Input("job-done", "data"), State("job", "data"),
                  State("history", "data"), State("tabs", "active_tab"),
                  prevent_initial_call=True)
    def _finish(job_id, ref, history, active):
        """A finished run: on screen, with its warnings, and first in the history."""
        job = api.get_job(job_id)
        rec = get_result(job.record) if job is not None else None
        if rec is None:
            return (no_update,) * 5
        panel = _PANEL_BY_ID.get(active, single_run)
        return (rec.id, _tab_view(panel, rec), job.values, common.warnings_block(rec),
                hist.add(history, hist.entry(rec, job.values, (ref or {}).get("preset"),
                                             history)))

    @app.callback(Output("run-status", "children", allow_duplicate=True),
                  Input("cancel-btn", "n_clicks"), State("job", "data"),
                  prevent_initial_call=True)
    def _cancel(_n, ref):
        """Ask the running job to stop; the poll reports when it has."""
        return _progress(api.cancel_job((ref or {}).get("id")))

    @app.callback(Output("history-strip", "children"),
                  Input("history", "data"), Input("run-store", "data"))
    def _strip(history, rid):
        """The runs of this session, the one on screen highlighted."""
        return hist.strip(history, rid)

    @app.callback(Output("run-store", "data", allow_duplicate=True),
                  Output("tab-content", "children", allow_duplicate=True),
                  Output("run-values", "data", allow_duplicate=True),
                  Output("run-warnings", "children", allow_duplicate=True),
                  Input({"type": "hist-view", "rid": ALL}, "n_clicks"),
                  State("history", "data"), State("tabs", "active_tab"),
                  prevent_initial_call=True)
    def _view(clicks, history, active):
        """Put an earlier run on screen (its tabs, its warnings, and its values
        for the stale banner)."""
        if not ctx.triggered_id or not any(clicks or ()):
            return (no_update,) * 4      # the strip was redrawn, nothing clicked
        rid = ctx.triggered_id["rid"]
        e, rec = hist.find(history, rid), get_result(rid)
        if e is None or rec is None:
            return (no_update,) * 4
        panel = _PANEL_BY_ID.get(active, single_run)
        return rid, _tab_view(panel, rec), e["values"], common.warnings_block(rec)

    @app.callback(Output("history", "data", allow_duplicate=True),
                  Input({"type": "hist-pin", "rid": ALL}, "n_clicks"),
                  State("history", "data"), prevent_initial_call=True)
    def _pin(clicks, history):
        """Pin a run (kept in the strip and in the result registry) or unpin it."""
        if not ctx.triggered_id or not any(clicks or ()):
            return no_update
        return hist.toggle_pin(history, ctx.triggered_id["rid"])

    @app.callback(Output({"type": "cfg", "path": ALL}, "value", allow_duplicate=True),
                  Input("hist-load", "n_clicks"),
                  State("run-store", "data"), State("history", "data"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  prevent_initial_call=True)
    def _load_run(n, rid, history, ids):
        """Put the shown run's configuration back into the form."""
        e = hist.find(history, rid)
        if not n or e is None:
            return [no_update] * len(ids)
        return _aligned(e["values"], ids)

    @app.callback(Output("tab-content", "children", allow_duplicate=True),
                  Input("tabs", "active_tab"), State("run-store", "data"),
                  prevent_initial_call=True)
    def _switch_tab(active, rid):
        """Render the newly selected tab from the stored run (or its empty state)."""
        panel = _PANEL_BY_ID.get(active, single_run)
        return _tab_view(panel, get_result(rid))

    @app.callback(Output({"type": "verdict", "rid": MATCH}, "children"),
                  Input({"type": "verdict", "rid": MATCH}, "id"))
    def _fill_verdict(slot):
        """The verdict, once the StatEye cross-check it needs has run. Fired
        by the placeholder appearing, so the time-domain cards are already
        on screen while this computes."""
        rec = get_result(slot["rid"])
        if rec is None:
            return common.need_run_message()
        common.ensure_stat(rec)
        return single_run.verdict_block(rec)

    @app.callback(Output("study-body", "children"),
                  Output("study-run", "disabled"),
                  Input("study-run", "n_clicks"),
                  State("tabs", "active_tab"), State("run-store", "data"),
                  prevent_initial_call=True)
    def _study(_n, active, rid):
        """Run the active study tab on the stored run."""
        panel = _PANEL_BY_ID.get(active)
        rec = get_result(rid)
        if panel is None or rec is None:
            return common.need_run_message(), False
        return _run_study(panel, rec), True


def main() -> None:
    """``halo-serdes-gui``: serve the app on ``--host`` / ``--port``."""
    import argparse

    ap = argparse.ArgumentParser(description="Halo_Serdes behavioral SerDes GUI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    build_app().run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
