"""Sweeps / Reach tab — statistical reach vs channel loss + FEC."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import numpy as np
from dash import html

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "Sweeps / Reach"
TAB_ID = "sweeps"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    r = studies.reach_study(rec)
    if "error" in r:
        return dbc.Alert(r["error"], color="light", className="border")
    traces = [
        {"x": -r["loss"], "y": np.maximum(r["pre"], 1e-30), "name": "pre-FEC (StatEye)",
         "mode": "lines+markers", "color": theme.MUTED, "dash": "dot"},
        {"x": -r["loss"], "y": np.maximum(r["kp4"], 1e-30), "name": "post-KP4",
         "mode": "lines+markers", "color": theme.PRIMARY},
        {"x": -r["loss"], "y": np.maximum(r["kr4"], 1e-30), "name": "post-KR4",
         "mode": "lines+markers", "color": theme.ACCENT},
    ]
    fig = figures.lines_fig(traces, title="Reach vs channel loss (statistical)",
                            xtitle="channel loss @ Nyquist [dB]", ytitle="BER",
                            logy=True, height=440,
                            hlines=[{"y": 1e-15, "text": "1e-15 target",
                                     "color": theme.GOOD}])
    rc = r["reach_kp4"]
    card = theme.metric_card("KP4 reach",
                             f"{rc:.1f} dB" if rc else "> swept range",
                             "info", "post-FEC < 1e-15")
    return html.Div([
        common.cards_row([card]),
        common.graph(fig),
        html.Div("Reach uses the analytic StatEye engine over a channel-length "
                 "sweep (fast, extrapolates to 1e-15; MLSD/DSP gains not "
                 "modelled here — see the example scripts for the full ladder).",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
