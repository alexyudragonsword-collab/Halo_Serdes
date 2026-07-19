"""Halo_Serdes GUI — Dash application shell, sidebar, tabs, and callbacks.

Left sidebar edits a ``LinkConfig`` (presets, YAML I/O, live derived values,
mixed-signal envelope banner, full config form). The main area is a set of
capability tabs; each run stashes its result in the server-side registry and
every tab renders from it. Run the app with ``halo-serdes-gui`` or
``python -m halo_serdes_gui``.
"""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import ALL, Dash, Input, Output, State, dcc, html, no_update

from . import config_bridge as cb
from . import theme
from .config_form import render_form, values_from_states
from .panels import (
    adaptation, adc, ami_com, backchannel, cdr, channel, crosstalk, ctle,
    dual_engine, eyes, fec, fixed_point, jitter, single_run, sweeps,
)
from .runner import get as get_result
from .runner import run_from_values

# --- panel registry --------------------------------------------------------
PANELS = [single_run, eyes, dual_engine, channel, ctle, jitter,
          adaptation, cdr, adc, backchannel,
          sweeps, fec, crosstalk, ami_com, fixed_point]
_PANEL_BY_ID = {p.TAB_ID: p for p in PANELS}

INITIAL = cb.config_to_values(cb.LinkConfig())


def _sidebar():
    return html.Div([
        html.Div([
            html.Span("Halo", style={"fontWeight": 800, "color": theme.PRIMARY}),
            html.Span("_Serdes", style={"fontWeight": 800, "color": theme.INK}),
            html.Span("  behavioral SerDes studio",
                      style={"fontSize": "0.72rem", "color": theme.MUTED}),
        ], className="mb-2"),

        dbc.InputGroup([
            dbc.Select(id="preset", options=[{"label": n, "value": n}
                       for n in cb.preset_names()], value=cb.preset_names()[0],
                       size="sm"),
            dbc.Button("Load", id="preset-load", size="sm", color="secondary"),
        ], className="mb-2"),

        dbc.Button("▶  Run", id="run-btn", color="primary", className="w-100 mb-2"),

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
        ]), id="yaml-collapse", is_open=False, className="mb-2"),

        theme.section_title("Configuration"),
        html.Div(render_form(INITIAL), id="form-container"),
    ], style={"height": "100vh", "overflowY": "auto", "padding": "0.8rem",
              "borderRight": f"1px solid {theme.GRID}", "background": "#fbfcfe"})


def _main():
    tabs = [dbc.Tab(label=p.TITLE, tab_id=p.TAB_ID) for p in PANELS]
    return html.Div([
        dbc.Tabs(tabs, id="tabs", active_tab=PANELS[0].TAB_ID, className="mb-2"),
        dcc.Loading(html.Div(single_run.render(None), id="tab-content"),
                    type="default", color=theme.PRIMARY),
    ], style={"height": "100vh", "overflowY": "auto", "padding": "0.8rem 1rem"})


def build_app() -> Dash:
    # Bootstrap (Flatly) is vendored in assets/00_bootstrap.min.css and loaded
    # automatically, so the app is fully self-contained (no CDN needed).
    app = Dash(__name__, title="Halo_Serdes",
               suppress_callback_exceptions=True)
    app.layout = dbc.Container([
        dcc.Store(id="run-store"),
        dbc.Row([
            dbc.Col(_sidebar(), width=12, lg=4, xl=3, style={"padding": 0}),
            dbc.Col(_main(), width=12, lg=8, xl=9, style={"padding": 0}),
        ], className="g-0"),
    ], fluid=True, style={"padding": 0})
    _register_callbacks(app)
    return app


def _aligned(target: dict, ids: list[dict]) -> list:
    return [target.get(i["path"], no_update) for i in ids]


def _register_callbacks(app: Dash) -> None:

    @app.callback(Output("derived", "children"), Output("envelope", "children"),
                  Input({"type": "cfg", "path": ALL}, "value"),
                  State({"type": "cfg", "path": ALL}, "id"))
    def _live(vals, ids):
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
                  Input("preset-load", "n_clicks"),
                  State("preset", "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  prevent_initial_call=True)
    def _load_preset(_n, name, ids):
        return _aligned(cb.config_to_values(cb.load_preset(name)), ids)

    @app.callback(Output({"type": "cfg", "path": ALL}, "value",
                         allow_duplicate=True),
                  Input("yaml-import", "n_clicks"),
                  State("yaml-text", "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  prevent_initial_call=True)
    def _import_yaml(_n, text, ids):
        try:
            cfg = cb.yaml_to_config(text or "")
        except Exception:
            return [no_update] * len(ids)
        return _aligned(cb.config_to_values(cfg), ids)

    @app.callback(Output("yaml-text", "value"),
                  Input("yaml-export", "n_clicks"),
                  State({"type": "cfg", "path": ALL}, "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  prevent_initial_call=True)
    def _export_yaml(_n, vals, ids):
        try:
            return cb.config_to_yaml(cb.build_config(values_from_states(ids, vals)))
        except Exception as exc:
            return f"# export failed: {exc}"

    @app.callback(Output("yaml-collapse", "is_open"),
                  Input("yaml-toggle", "n_clicks"),
                  State("yaml-collapse", "is_open"), prevent_initial_call=True)
    def _toggle_yaml(_n, is_open):
        return not is_open

    @app.callback(Output("run-store", "data"), Output("tab-content", "children"),
                  Input("run-btn", "n_clicks"),
                  State({"type": "cfg", "path": ALL}, "value"),
                  State({"type": "cfg", "path": ALL}, "id"),
                  State("tabs", "active_tab"), prevent_initial_call=True)
    def _run(_n, vals, ids, active):
        values = values_from_states(ids, vals)
        # capture jitter opportunistically so the Jitter tab has data when the
        # pattern repeats enough; harmless (returns a note otherwise)
        rec = run_from_values(values, collect_eye=True, collect_jitter=True)
        panel = _PANEL_BY_ID.get(active, single_run)
        return rec.id, panel.render(rec)

    @app.callback(Output("tab-content", "children", allow_duplicate=True),
                  Input("tabs", "active_tab"), State("run-store", "data"),
                  prevent_initial_call=True)
    def _switch_tab(active, rid):
        panel = _PANEL_BY_ID.get(active, single_run)
        return panel.render(get_result(rid))


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Halo_Serdes behavioral SerDes GUI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    build_app().run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
