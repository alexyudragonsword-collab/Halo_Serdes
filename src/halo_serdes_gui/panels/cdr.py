"""CDR tab — recovered-phase acquisition + phase-detector activity."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures
from ..runner import RunRecord
from . import common

TITLE = "CDR"
TAB_ID = "cdr"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok or rec.sim is None:
        return common.error_block(rec) if rec and not rec.ok \
            else common.need_run_message()
    e = rec.sim.extras
    settle, train_end = e.get("settle", 0), e.get("train_end", 0)
    return html.Div([
        common.warnings_block(rec),
        dbc.Row([
            dbc.Col(common.graph(figures.cdr_phase_fig(
                e.get("phase_track"), rec.cfg.osr, settle, train_end)), lg=7),
            dbc.Col(common.graph(figures.pd_activity_fig(e.get("pd_hist"))), lg=5),
        ], className="g-2"),
        html.Div(f"CDR kind: {rec.cfg.rx.cdr.kind} · Kp=2^-{rec.cfg.rx.cdr.kp_shift} "
                 f"Ki=2^-{rec.cfg.rx.cdr.ki_shift}. A sloped phase track = "
                 "residual frequency offset; a flat one = locked.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
