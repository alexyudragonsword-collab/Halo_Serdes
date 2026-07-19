"""Jitter tab — per-stage decomposition table, stacked bar, timing bathtub."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import numpy as np
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Jitter"
TAB_ID = "jitter"


def _budget_table(budget, ui):
    from halo_serdes.analysis.jitter import total_jitter
    u = 100.0 / ui
    head = html.Thead(html.Tr([html.Th(c) for c in
                      ["Stage", "ISI", "DCD", "Pj", "Rj(rms)", "TJ@1e-12"]]))
    rows = []
    for name, jr in budget.items():
        rows.append(html.Tr([
            html.Td(name),
            html.Td(f"{jr.isi*u:.2f}%"), html.Td(f"{jr.dcd*u:.2f}%"),
            html.Td(f"{jr.pj*u:.2f}%"), html.Td(f"{jr.rj*u:.3f}%"),
            html.Td(f"{total_jitter(jr,1e-12)*u:.2f}%"),
        ]))
    return dbc.Table([head, html.Tbody(rows)], bordered=True, hover=True,
                     size="sm", striped=True, className="mb-2",
                     style={"maxWidth": "560px", "fontVariantNumeric": "tabular-nums"})


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    jb = rec.sim.extras.get("jitter_budget") if rec.sim is not None else None
    if not isinstance(jb, dict) or not jb or "_note" in jb:
        note = (jb.get("_note") if isinstance(jb, dict) else None) or \
            "no jitter budget"
        return html.Div([
            dbc.Alert([html.Div("Jitter decomposition needs a repeating "
                       "pattern (≥4 periods)."),
                       html.Div(f"detail: {note}", style={"fontSize": "0.78rem"}),
                       html.Div("Set pattern to prbs7 and symbols ≥ ~1500, "
                                "then Run.", style={"fontSize": "0.78rem"})],
                      color="light", className="border")])

    stage = "ctle" if "ctle" in jb else next(iter(jb))
    from halo_serdes.analysis.jitter import make_bathtub
    t, ber = make_bathtub(jb[stage], rec.cfg.ui)
    bath = figures.lines_fig([{"x": t / rec.cfg.ui, "y": np.maximum(ber, 1e-30),
                               "name": f"bathtub @ {stage}", "mode": "lines",
                               "color": theme.PRIMARY}],
                             title=f"Timing bathtub ({stage} stage)",
                             xtitle="phase [UI]", ytitle="BER", logy=True)
    return html.Div([
        common.warnings_block(rec),
        theme.section_title("Per-stage jitter budget"),
        _budget_table(jb, rec.cfg.ui),
        dbc.Row([
            dbc.Col(common.graph(figures.jitter_bar_fig(jb, rec.cfg.ui)), lg=7),
            dbc.Col(common.graph(bath), lg=5),
        ], className="g-2"),
    ])
