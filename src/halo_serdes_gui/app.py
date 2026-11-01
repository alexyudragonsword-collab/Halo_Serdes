"""Halo_Serdes GUI — Dash application shell, layout and callbacks.

The page is the link it simulates:

- a top bar with the preset, the receiver architecture switch (``rx.arch``),
  the derived values and Run / Cancel with the run's progress;
- on the left, the link as a chain of stages (``field_meta.STAGES``); picking
  one opens its parameters below the chain, the common ones first and the rest
  behind "Advanced";
- on the right, four workspaces: **Verdict** (pass / fail against the FEC
  threshold, eye, bathtub), **Diagnose** (the analysis views, one per stage,
  following the stage picked on the left), **Studies** (sweeps and
  projections, each run on its own button) and **Compare** (runs of this
  session side by side). Above them, the runs of this session, a banner when
  the form has moved on from the run shown, and that run's warnings, once.

Below 992 px the results come first and the configuration after them.

Run starts a background job (``halo_serdes_app.api.start_job``, the same job
runner the phone uses) and an interval polls it. Every view is a panel module
with ``TITLE``, ``TAB_ID`` and ``render(rec)``; the ``view`` store holds the
``TAB_ID`` on screen. Run the app with ``halo-serdes-gui`` or
``python -m halo_serdes_gui``.
"""

from __future__ import annotations

from collections import OrderedDict

import time

import dash_bootstrap_components as dbc
from dash import ALL, MATCH, Dash, Input, Output, State, ctx, dcc, html, no_update

from halo_serdes_app import api

from . import config_bridge as cb
from . import field_meta as fm
from . import history as hist
from . import theme
from .config_form import (
    ROW_BAD, ROW_CLASS, advanced_count, arch_control, render_form, values_from_states,
    visibility,
)
from .panels import (
    adaptation, adc, ami_com, backchannel, cdr, channel, compare, crosstalk, ctle,
    dual_engine, eyes, fec, fixed_point, jitter, jtol, jtol_stat, optical, single_run, sweeps,
)
from .panels import common
from .runner import get as get_result

# --- views -------------------------------------------------------------------
#: (workspace id, title)
WORKSPACES = [("verdict", "Verdict"), ("diag", "Diagnose"), ("study", "Studies"),
              ("compare", "Compare")]
#: Diagnose: one view per part of the link, in chain order.
DIAG = [eyes, dual_engine, backchannel, channel, ctle, adc, adaptation, cdr, jitter]
_DIAG_LABEL = {"eyes": "Eyes", "dual": "Dual-engine", "backchannel": "Tx training",
               "channel": "Channel", "ctle": "CTLE", "adc": "ADC", "adapt": "Equaliser",
               "cdr": "CDR", "jitter": "Jitter"}
#: Studies: sweeps and projections, each behind its own Run button.
STUDIES = [sweeps, fec, crosstalk, jtol, jtol_stat, ami_com, fixed_point, optical]
#: The diagnose view a stage on the left opens.
STAGE_TO_DIAG = {"link": "eyes", "tx": "backchannel", "channel": "channel", "fe": "ctle",
                 "adc": "adc", "eq": "adapt", "cdr": "cdr"}

PANELS = [single_run, *DIAG, *STUDIES, compare]
_PANEL_BY_ID = {p.TAB_ID: p for p in PANELS}
_WS_OF = {single_run.TAB_ID: "verdict", compare.TAB_ID: "compare",
          **{p.TAB_ID: "diag" for p in DIAG}, **{p.TAB_ID: "study" for p in STUDIES}}

# first-launch default: a runnable analytic preset (so Run works immediately
# and the sweep/COM tabs are populated), falling back to bare defaults.
_names = cb.preset_names()
DEFAULT_PRESET = next((n for n in _names if "analytic" in n), _names[0])
INITIAL = cb.config_to_values(cb.load_preset(DEFAULT_PRESET))

_HIDDEN = {"display": "none"}

#: How often the browser asks how the background run is doing.
POLL_MS = 400


def _topbar():
    """Brand, preset, architecture, derived values, Run / Cancel and progress."""
    return html.Div([
        html.Div([
            html.Div([
                html.Img(src="/assets/icon.png", height="28px",
                         style={"marginRight": "0.45rem", "borderRadius": "6px"}),
                html.Span("Halo", style={"fontWeight": 800, "color": theme.PRIMARY}),
                html.Span("_Serdes", style={"fontWeight": 800, "color": theme.INK}),
            ], className="d-flex align-items-center me-3"),
            # choosing a preset loads it; there is no separate Load step
            dbc.Select(id="preset", options=[{"label": n, "value": n}
                       for n in cb.preset_names()], value=DEFAULT_PRESET,
                       size="sm", style={"maxWidth": "300px"}, className="me-3"),
            html.Div(arch_control(INITIAL.get("rx.arch")), className="me-3",
                     title="Receiver architecture (rx.arch)"),
            html.Div(id="derived", className="me-auto"),
            html.Div([
                dbc.Button("▶  Run", id="run-btn", color="primary", style={"minWidth": "110px"}),
                dbc.Button("■ Cancel", id="cancel-btn", color="secondary", outline=True,
                           className="ms-2", style=_HIDDEN),
            ], className="d-flex"),
        ], className="d-flex flex-wrap align-items-center gap-2"),
        html.Div(id="run-status", className="mt-1"),
        html.Div(id="envelope"),
    ], className="app-topbar")


#: the configuration column: beside the results on a wide screen, a drawer
#: over them on a narrow one (``.cfg-drawer`` in assets/app.css)
_DRAWER = "order-2 order-lg-1 cfg-drawer"


def _bottom_nav():
    """A phone-width bar (hidden from 992 px): open the configuration
    drawer, run, back to the results."""
    def item(label, bid):
        return dbc.Button(label, id=bid, color="link", className="bottom-nav-btn")
    return html.Div([item("⚙ Configure", "nav-config"), item("▶ Run", "nav-run"),
                     item("▤ Results", "nav-results")],
                    className="bottom-nav d-lg-none")


def _config_pane():
    """The link as a chain of stages and the picked stage's parameters, then YAML."""
    return html.Div([
        theme.section_title("Link"),
        html.Div(id="chain", className="mb-2"),
        html.Div([
            html.Div(id="stage-title", style={"fontWeight": 700, "fontSize": "0.9rem"}),
            dbc.Button(id="adv-toggle", size="sm", color="link", className="p-0"),
        ], className="d-flex justify-content-between align-items-baseline"),
        html.Div(render_form(INITIAL, "link"), id="form-container", className="mb-3"),

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
    ], className="pane", style={"padding": "0.6rem 0.8rem",
                                "borderRight": f"1px solid {theme.GRID}",
                                "background": "#fbfcfe"})


def _main():
    """The runs, the stale banner, the warnings, the workspaces and the view."""
    return html.Div([
        html.Div(id="history-strip"),
        html.Div(id="stale"),
        html.Div(id="run-warnings"),
        dbc.Tabs([dbc.Tab(label=t, tab_id=w) for w, t in WORKSPACES], id="ws",
                 active_tab="verdict", className="mb-2"),
        html.Div(dbc.RadioItems(id="sub", options=[], value=None, inline=True,
                                className="btn-group flex-wrap",
                                inputClassName="btn-check",
                                labelClassName="btn btn-outline-primary btn-sm",
                                labelCheckedClassName="active"),
                 id="sub-wrap", className="mb-2", style=_HIDDEN),
        # the spinner covers the view only while the view itself is being built:
        # the verdict and a study's result fill in inside it, and must not
        # blank the cards already on screen while they compute
        dcc.Loading(html.Div(single_run.render(None), id="tab-content"),
                    type="default", color=theme.PRIMARY,
                    target_components={"tab-content": "children"}),
    ], className="pane", style={"padding": "0.6rem 1rem"})


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
    app.layout = html.Div([
        dcc.Store(id="run-store"),
        dcc.Store(id="run-values"),
        dcc.Store(id="job"),
        dcc.Store(id="job-done"),
        dcc.Store(id="history", data=[]),
        dcc.Store(id="view", data=single_run.TAB_ID),
        dcc.Store(id="stage", data="link"),
        dcc.Store(id="adv", data=False),
        dcc.Store(id="cmp-sel", data=[]),
        dcc.Interval(id="job-poll", interval=POLL_MS, disabled=True),
        _topbar(),
        dbc.Row([
            # results first on a narrow screen, configuration first beside them;
            # below 992 px the configuration is a drawer the bottom bar opens
            dbc.Col(_config_pane(), id="cfg-col", width=12, lg=4, xl=3,
                    className=_DRAWER, style={"padding": 0}),
            dbc.Col(_main(), width=12, lg=8, xl=9, className="order-1 order-lg-2",
                    style={"padding": 0}),
        ], className="g-0 app-body"),
        _bottom_nav(),
    ], className="app-shell")
    _register_callbacks(app)
    return app


# --- study tabs --------------------------------------------------------------
# A study (reach sweep, crosstalk, JTOL, COM, fixed point, optical) costs from
# a second to ~20 s. Computing it on tab switch left the tab spinning with no
# word of what it was doing; it now waits for its own Run button, and a result
# is kept per (run, tab) so coming back to the tab does not recompute it.
# It runs as a background job (``api.start_task``) like a run does: the top
# bar shows its progress and its cancel, and the page stays usable meanwhile.
_STUDY_CACHE: OrderedDict[tuple[str, str], object] = OrderedDict()
_STUDY_CACHE_MAX = 24


def _study_gate(panel, rec):
    """A study tab before it has run: what it computes, and its Run button."""
    return html.Div([
        html.Div(panel.STUDY, style={"fontSize": "0.85rem", "color": theme.INK,
                                     "marginBottom": "0.5rem"}),
        dbc.Button(f"▶  Run {panel.TITLE}", id="study-run", color="primary",
                   size="sm", className="mb-2"),
    ])


def _tab_view(panel, rec):
    """What a tab shows for ``rec``: a study's gate (or its kept result), or
    the panel itself."""
    if getattr(panel, "STUDY", None) and rec is not None and rec.ok:
        kept = _STUDY_CACHE.get((rec.id, panel.TAB_ID))
        return kept if kept is not None else _study_gate(panel, rec)
    return panel.render(rec)


def _render_view(view, rec, history=None, selected=None):
    """The view ``view`` for ``rec``: Compare needs the history to pick from,
    every other view only the run."""
    if view == compare.TAB_ID:
        if rec is None:
            return compare.render(None)
        sel = [r for r in selected or () if hist.find(history, r)] or \
            [e["rid"] for e in (history or [])[:2]]
        return compare.picker(history, sel, rec.id)
    return _tab_view(_PANEL_BY_ID.get(view, single_run), rec)


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
        # a run stops at its next receiver chunk or front-end step (a step
        # is a few seconds on millions of symbols); a study between two of
        # its engine runs
        text = "Cancelling — stops when the current step ends…"
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
    if len(labels) > 6:
        # a run of another preset differs in dozens of fields: their names
        # are noise, the count and the way back are what help
        return theme.banner(
            "warn", f"These results are from a different configuration ({len(labels)} "
                    f"fields differ from the form). Press ▶ Run to run the form, or "
                    f"↺ load that run's config.")
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
    """Wire the top bar, the chain, the form and the workspaces. Every
    callback that can fail on user input reports the reason on screen rather
    than raising into Dash, where it would only reach the browser console."""

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

    @app.callback(Output({"type": "cfg-row", "path": ALL}, "className"),
                  Output({"type": "cfg-hint", "path": ALL}, "children"),
                  Input({"type": "cfg", "path": ALL}, "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  State({"type": "cfg-row", "path": ALL}, "id"))
    def _ranges(vals, ids, row_ids):
        """Mark the rows whose value is outside the field's range, with the
        range under the input -- before Run says the configuration is
        invalid (the bounds ``__post_init__`` reads, ``schema.FIELD_BOUNDS``)."""
        bad = cb.range_errors(values_from_states(ids, vals))
        return ([ROW_BAD if r["path"] in bad else ROW_CLASS for r in row_ids],
                [bad.get(r["path"], "") for r in row_ids])

    @app.callback(Output({"type": "cfg", "path": ALL}, "value"),
                  Input("preset", "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  prevent_initial_call=True)
    def _load_preset(name, ids):
        """Fill the form from the preset as soon as it is chosen."""
        return _aligned(cb.config_to_values(cb.load_preset(name)), ids)

    @app.callback(Output({"type": "cfg-row", "path": ALL}, "style"),
                  Output({"type": "cfg-sec", "sid": ALL}, "style"),
                  Output({"type": "cfg-stage", "sid": ALL}, "style"),
                  Input({"type": "cfg", "path": ALL}, "value"),
                  Input("stage", "data"), Input("adv", "data"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  State({"type": "cfg-row", "path": ALL}, "id"),
                  State({"type": "cfg-sec", "sid": ALL}, "id"),
                  State({"type": "cfg-stage", "sid": ALL}, "id"),
                  prevent_initial_call=True)
    def _visibility(vals, stage, adv, ids, row_ids, sec_ids, stage_ids):
        """The picked stage's fields that the configuration uses (architecture,
        channel kind, optics, ...), the common ones unless Advanced is open;
        the rest stay in the form, hidden."""
        return visibility(values_from_states(ids, vals), row_ids, sec_ids, stage_ids,
                          stage or "link", bool(adv))

    @app.callback(Output("chain", "children"), Output("stage-title", "children"),
                  Output("adv-toggle", "children"),
                  Input({"type": "cfg", "path": ALL}, "value"),
                  Input("stage", "data"), Input("adv", "data"),
                  State({"type": "cfg", "path": ALL}, "id"))
    def _chain(vals, stage, adv, ids):
        """The chain of stages with what each is set to, the picked one
        highlighted, and the Advanced toggle with how much it holds."""
        values = values_from_states(ids, vals)
        stage = stage or "link"
        cards = []
        for sid, title, _ in fm.STAGES:
            if not fm.stage_shown(sid, values):
                continue
            cards.append(html.Button([
                html.Div(title, className="stage-name"),
                html.Div(fm.stage_summary(sid, values), className="stage-sub"),
            ], id={"type": "stage", "sid": sid}, n_clicks=0,
                className="stage-card" + (" active" if sid == stage else "")))
        n_adv = advanced_count(stage, values)
        label = ("Hide advanced" if adv else f"Advanced ({n_adv})") if n_adv else ""
        title = next(t for s_, t, _ in fm.STAGES if s_ == stage)
        return cards, title, label

    @app.callback(Output("stage", "data"),
                  Output("sub", "value", allow_duplicate=True),
                  Input({"type": "stage", "sid": ALL}, "n_clicks"),
                  State("ws", "active_tab"), prevent_initial_call=True)
    def _pick_stage(clicks, ws):
        """Open a stage's parameters; in Diagnose, also show its view."""
        if not ctx.triggered_id or not any(clicks or ()):
            return no_update, no_update      # the chain was redrawn
        sid = ctx.triggered_id["sid"]
        return sid, (STAGE_TO_DIAG.get(sid, no_update) if ws == "diag" else no_update)

    @app.callback(Output("adv", "data"), Input("adv-toggle", "n_clicks"),
                  State("adv", "data"), prevent_initial_call=True)
    def _toggle_adv(_n, adv):
        """Show or hide the picked stage's advanced fields."""
        return not adv

    @app.callback(Output("sub", "options"), Output("sub", "value"),
                  Output("sub-wrap", "style"),
                  Input("ws", "active_tab"), Input({"type": "cfg", "path": "rx.arch"}, "value"),
                  State("view", "data"), State("stage", "data"))
    def _subnav(ws, arch, view, stage):
        """The views of a workspace: Diagnose opens on the stage picked on the
        left (no ADC view on a mixed-signal receiver), Studies on the first
        study; Verdict and Compare are one view."""
        if ws == "diag":
            opts = [{"label": _DIAG_LABEL[p.TAB_ID], "value": p.TAB_ID} for p in DIAG
                    if p is not adc or arch == "adc_dsp"]
            pick = view if _WS_OF.get(view) == "diag" else STAGE_TO_DIAG.get(stage, "eyes")
            return opts, pick, None
        if ws == "study":
            opts = [{"label": p.TITLE, "value": p.TAB_ID} for p in STUDIES]
            pick = view if _WS_OF.get(view) == "study" else STUDIES[0].TAB_ID
            return opts, pick, None
        return [], None, _HIDDEN

    @app.callback(Output("view", "data"),
                  Input("ws", "active_tab"), Input("sub", "value"),
                  prevent_initial_call=True)
    def _set_view(ws, sub):
        """The view on screen: the workspace's, or the one picked inside it."""
        if ws == "verdict":
            return single_run.TAB_ID
        if ws == "compare":
            return compare.TAB_ID
        if sub and _WS_OF.get(sub) == ws:
            return sub
        return no_update                     # the sub-navigation is catching up

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
            if job is not None and job.kind == "task":
                return no_update, no_update, no_update, no_update, theme.banner(
                    "warn", "A study is running: press ▶ Run when it has finished, "
                            "or cancel it.")
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
        if (ref or {}).get("study"):
            # a study: _finish puts its tab back on screen however it ended
            note = None
            if job.state == "cancelled":
                note = theme.banner("info", f"Study cancelled after {job.elapsed_s:.1f} s.")
            elif job.state == "error":
                msg = (job.error or {}).get("message", "unknown error")
                note = theme.banner("crit", f"Study failed: {msg}")
            return (job.id, *idle, note)
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
                  State("history", "data"), State("view", "data"),
                  prevent_initial_call=True)
    def _finish(job_id, ref, history, active):
        """A finished run: on screen, with its warnings, and first in the history.
        A finished study: its tab redrawn if it is the one on screen (the
        result kept, or the gate again after a cancel); elsewhere it waits in
        the cache for the tab."""
        job = api.get_job(job_id)
        if job is not None and (ref or {}).get("study"):
            rec = get_result(ref.get("rid"))
            if rec is None or active != ref["study"]:
                return (no_update,) * 5
            return (no_update, _tab_view(_PANEL_BY_ID[active], rec), no_update,
                    no_update, no_update)
        rec = get_result(job.record) if job is not None else None
        if rec is None:
            return (no_update,) * 5
        history = hist.add(history, hist.entry(rec, job.values, (ref or {}).get("preset"),
                                               history))
        # a run started from Compare is shown beside the run it came from
        other = (ref or {}).get("compare_with")
        view = compare.TAB_ID if other else active
        return (rec.id, _render_view(view, rec, history, [other, rec.id] if other else None),
                job.values, common.warnings_block(rec), history)

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
                  State("history", "data"), State("view", "data"),
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
        return rid, _render_view(active, rec, history), e["values"], common.warnings_block(rec)

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
                  Input("view", "data"), State("run-store", "data"),
                  State("history", "data"), State("cmp-sel", "data"),
                  prevent_initial_call=True)
    def _switch_tab(active, rid, history=None, selected=None):
        """Render the newly selected view from the stored run (or its empty state)."""
        return _render_view(active, get_result(rid), history, selected)

    @app.callback(Output("cmp-body", "children"), Output("cmp-sel", "data"),
                  Input("cmp-pick", "value"), State("history", "data"))
    def _cmp_body(picked, history):
        """The picked runs side by side; the pick is kept for the next visit."""
        entries = [e for e in (hist.find(history, r) for r in picked or ()) if e]
        return compare.body(entries, [get_result(e["rid"]) for e in entries]), picked

    @app.callback(Output("job", "data", allow_duplicate=True),
                  Output("job-poll", "disabled", allow_duplicate=True),
                  Output("run-btn", "disabled", allow_duplicate=True),
                  Output("cancel-btn", "style", allow_duplicate=True),
                  Output("run-status", "children", allow_duplicate=True),
                  Input("cmp-other", "n_clicks"), State("run-store", "data"),
                  State("history", "data"), prevent_initial_call=True)
    def _cmp_other(n, rid, history):
        """Run the shown run's configuration through the other receiver; when
        it finishes, the two are compared."""
        e = hist.find(history, rid)
        if not n or e is None:
            return (no_update,) * 5
        values = dict(e["values"])
        values["rx.arch"] = "adc_dsp" if values.get("rx.arch") != "adc_dsp" else "mixed_signal"
        try:
            job = api.start_job(values, collect_eye=True, collect_jitter=True)
        except api.Busy:
            return no_update, True, False, _HIDDEN, theme.banner(
                "warn", "A run is in progress; try again when it ends.")
        return ({"id": job.id, "preset": e.get("preset"), "compare_with": rid}, False, True,
                None, _progress(job))

    @app.callback(Output("cmp-sel", "data", allow_duplicate=True),
                  Output("ws", "active_tab"),
                  Input("job-done", "data"), State("job", "data"),
                  prevent_initial_call=True)
    def _cmp_after_run(job_id, ref):
        """A run started from Compare lands next to the run it was started from."""
        other = (ref or {}).get("compare_with")
        job = api.get_job(job_id)
        if not other or job is None or job.record is None:
            return no_update, no_update
        return [other, job.record], "compare"

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

    @app.callback(Output("cfg-col", "className"),
                  Input("nav-config", "n_clicks"), Input("nav-results", "n_clicks"),
                  Input("nav-run", "n_clicks"), Input("run-btn", "n_clicks"),
                  State("cfg-col", "className"), prevent_initial_call=True)
    def _drawer(_c, _r, _n, _run, cls):
        """The configuration drawer on a narrow screen: Configure toggles it,
        Results and Run close it (the results are what a run is for)."""
        is_open = "open" in (cls or "").split()
        if ctx.triggered_id == "nav-config" and not is_open:
            return f"{_DRAWER} open"
        return _DRAWER

    # the bottom bar's Run is the top bar's: one start path, one set of guards
    app.clientside_callback(
        """function (n) {
            const b = document.getElementById("run-btn");
            if (n && b && !b.disabled) { b.click(); }
            return window.dash_clientside.no_update;
        }""",
        Output("nav-run", "title"), Input("nav-run", "n_clicks"),
        prevent_initial_call=True)

    @app.callback(Output("job", "data", allow_duplicate=True),
                  Output("job-poll", "disabled", allow_duplicate=True),
                  Output("run-btn", "disabled", allow_duplicate=True),
                  Output("cancel-btn", "style", allow_duplicate=True),
                  Output("run-status", "children", allow_duplicate=True),
                  Output("study-run", "disabled"),
                  Input("study-run", "n_clicks"),
                  State("view", "data"), State("run-store", "data"),
                  prevent_initial_call=True)
    def _study(n, active, rid):
        """Start the active study tab on the stored run as a background job;
        the run's poll follows it and ``_finish`` shows it."""
        panel = _PANEL_BY_ID.get(active)
        rec = get_result(rid)
        if not n or panel is None or rec is None:
            return (no_update,) * 6
        try:
            job = api.start_task(lambda: _run_study(panel, rec),
                                 stage=f"{panel.TITLE} study")
        except api.Busy:
            return (no_update, no_update, no_update, no_update, theme.banner(
                "warn", "A run is in progress: start the study when it has finished, "
                        "or cancel the run."), False)
        return ({"id": job.id, "study": active, "rid": rid}, False, True, None,
                _progress(job), True)


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
