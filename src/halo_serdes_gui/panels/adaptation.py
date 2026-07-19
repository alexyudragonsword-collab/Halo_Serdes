"""Adaptation tab — DFE tap trajectories + convergence learning curve."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures
from ..runner import RunRecord
from . import common

TITLE = "Adaptation"
TAB_ID = "adapt"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    if rec.sim is None:
        return common.need_run_message()
    e = rec.sim.extras
    w_hist = e.get("w_dfe_hist")
    w_final = rec.sim.dfe_taps
    settle, train_end = e.get("settle", 0), e.get("train_end", 0)
    n_ave = e.get("n_ave", 1)
    return html.Div([
        common.warnings_block(rec),
        dbc.Row([
            dbc.Col(common.graph(figures.dfe_traj_fig(
                w_hist, w_final, settle, train_end, n_ave)), lg=7),
            dbc.Col(common.graph(figures.convergence_fig(
                w_hist, w_final, n_ave)), lg=5),
        ], className="g-2"),
        html.Div("Dotted lines mark converged tap values; shaded bands are the "
                 "CDR-settle and data-aided training windows. Enable DFE "
                 "adaptation (dfe.adapt = lms/sign_sign) to see it move.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
