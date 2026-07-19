"""Crosstalk tab — FEXT/NEXT coupling sweep (statistical)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import numpy as np
from dash import html

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
    return html.Div([
        common.cards_row([theme.metric_card("Baseline BER",
                          f"{x['baseline']:.2e}", "muted", "no aggressors")]),
        common.graph(fig),
        html.Div("One FEXT + one NEXT synthetic aggressor at a common coupling "
                 "level are injected into the StatEye engine (the same "
                 "XtalkAggressor drives the time engine). Configure real "
                 "couplings via channel/crosstalk.import_xtalk in scripts.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
