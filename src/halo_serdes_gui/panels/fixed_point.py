"""Fixed-Point tab — datapath word-length sweep (BER wall)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "Fixed-Point"
TAB_ID = "fixed"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    fp = studies.fixedpoint_study(rec)
    if "error" in fp:
        return dbc.Alert(fp["error"], color="light", className="border")
    fig = figures.lines_fig(
        [{"x": fp["wl"], "y": fp["mismatch"], "name": "decision mismatch",
          "mode": "lines+markers", "color": theme.PRIMARY}],
        title="Fixed-point BER wall (FFE/DFE weight word length)",
        xtitle="weight word length [bits]",
        ytitle="mismatch vs wide-word reference", logy=True, height=440)
    fig.add_vline(x=10, line=dict(color=theme.MUTED, dash="dash", width=1),
                  annotation_text="DragonPHY2 silicon width (10b)")
    return html.Div([
        common.graph(fig),
        html.Div("Bit-true replay of the captured ADC codes through the "
                 "int64/shift datapath at each word length (run_fixed_datapath); "
                 "the knee is the RTL word-length guidance. Requires an ADC run.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
