"""Deep-BER JTOL tab — tolerated SJ at 1e-6 and 1e-12 from the statistical engine."""

from __future__ import annotations

import numpy as np
from dash import html

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "JTOL (deep BER)"
TAB_ID = "jtolstat"
#: Shown with a Run button instead of computing on tab switch (app.py).
STUDY = ("Tolerated sinusoidal jitter at BER 1e-6 and 1e-12 from the statistical "
         "engine, with the CDR's slew limit. About 40 statistical runs: one to "
         "two minutes.")


def render(rec: RunRecord):
    """Tolerated SJ against frequency at two deep thresholds, with the mask."""
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    j = studies.jtol_stat_study(rec)
    f_mhz = j["freqs"] / 1e6
    traces = [
        {"x": f_mhz, "y": np.maximum(j["tol_1e6"], 1e-3), "name": "BER 1e-6",
         "mode": "lines+markers", "color": theme.PRIMARY},
        {"x": f_mhz, "y": np.maximum(j["tol_1e12"], 1e-3), "name": "BER 1e-12",
         "mode": "lines+markers", "color": theme.INK},
        {"x": f_mhz, "y": j["slew_ui"], "name": "CDR slew limit (model edge)",
         "mode": "lines", "dash": "dot", "color": theme.MUTED},
        {"x": f_mhz, "y": j["mask"], "name": "compliance mask (generic)",
         "mode": "lines", "dash": "dash", "color": theme.MUTED},
    ]
    fig = figures.lines_fig(
        traces, title="Jitter tolerance, statistical engine",
        xtitle="SJ frequency [MHz]", ytitle="tolerated SJ [UI, 0-pk]",
        logy=True, height=440)
    fig.update_xaxes(type="log")
    return html.Div([
        common.graph(fig),
        html.Div("Each point is the statistical engine with the tone taken through the "
                 "CDR loop's error response: thresholds no simulation reaches. That loop "
                 "is linear; above the dotted line a bang-bang CDR follows the tone at "
                 "more than 0.4 of its slew, where it can slip cycles or err more than "
                 "this reads — check those points with the time-domain JTOL tab. A curve "
                 "on the 0.001 floor means the link misses the threshold without any SJ.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
