"""Crosstalk tab — FEXT/NEXT coupling sweep (statistical)."""

from __future__ import annotations

import numpy as np
from dash import html

import dash_bootstrap_components as dbc

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "Crosstalk"
TAB_ID = "crosstalk"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    x = studies.crosstalk_study(rec)
    fig = figures.lines_fig(
        [{"x": x["coupling"], "y": np.maximum(x["ber"], 1e-30),
          "name": "with FEXT+NEXT", "mode": "lines+markers", "color": theme.PRIMARY}],
        title="BER vs crosstalk coupling (statistical)",
        xtitle="coupling strength [dB]", ytitle="BER", logy=True, height=440,
        hlines=[{"y": max(x["baseline"], 1e-30), "text": "no crosstalk",
                 "color": theme.MUTED, "dash": "dash"},
                {"y": 2.4e-4, "text": "KP4 pre-FEC", "color": theme.GOOD}])
    # multi-lane: ICN (~sqrt N) and 802.3 COM vs number of aggressor lanes
    m = studies.multilane_study(rec)
    icn_fig = figures.lines_fig(
        [{"x": m["counts"], "y": m["icn_mv"], "name": "ICN",
          "mode": "lines+markers", "color": theme.CRIT}],
        title="Integrated crosstalk noise vs lanes",
        xtitle="aggressor lanes", ytitle="ICN [mV rms]", height=360)
    com_fig = figures.lines_fig(
        [{"x": m["counts"], "y": m["com_db"], "name": "802.3 COM",
          "mode": "lines+markers", "color": theme.PRIMARY}],
        title="802.3 COM vs aggressor lanes",
        xtitle="aggressor lanes", ytitle="COM [dB]", height=360,
        hlines=[{"y": 3.0, "text": "≈3 dB pass", "color": theme.GOOD}])

    return html.Div([
        common.cards_row([theme.metric_card("Baseline BER",
                          f"{x['baseline']:.2e}", "muted", "no aggressors")]),
        common.graph(fig),
        html.Div("One FEXT + one NEXT synthetic aggressor at a common coupling "
                 "level are injected into the StatEye engine (the same "
                 "XtalkAggressor drives the time engine). Configure real "
                 "couplings via channel/crosstalk.import_xtalk in scripts.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
        html.Hr(className="my-2"),
        html.Div("Multi-lane environment (aggressor_bank): independent FEXT+NEXT "
                 "lanes at −30 dB coupling. ICN (the behavioral MDFEXT/MDNEXT "
                 "power sum) grows ~sqrt(N); the same bank feeds the 802.3 COM "
                 "engine as σ_XT, so the margin falls as lanes are added.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
        dbc.Row([dbc.Col(common.graph(icn_fig), lg=6),
                 dbc.Col(common.graph(com_fig), lg=6)], className="g-2"),
    ])
