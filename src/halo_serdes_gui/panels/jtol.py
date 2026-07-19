"""JTOL tab — jitter tolerance vs SJ frequency (tolerated amplitude + mask)."""

from __future__ import annotations

import numpy as np
from dash import html

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "JTOL"
TAB_ID = "jtol"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    j = studies.jtol_study(rec)
    f_mhz = j["freqs"] / 1e6
    traces = [
        {"x": f_mhz, "y": np.maximum(j["tol_ui"], 1e-3), "name": "tolerated SJ",
         "mode": "lines+markers", "color": theme.PRIMARY},
        {"x": f_mhz, "y": j["mask"], "name": "compliance mask (generic)",
         "mode": "lines", "dash": "dash", "color": theme.MUTED},
    ]
    fig = figures.lines_fig(
        traces, title=f"Jitter tolerance (BER < {j['threshold']:.0e})",
        xtitle="SJ frequency [MHz]", ytitle="tolerated SJ [UI, 0-pk]",
        logy=True, height=440)
    fig.update_xaxes(type="log")
    return html.Div([
        common.graph(fig),
        html.Div("Tx sinusoidal jitter is swept in amplitude (binary search) at "
                 "each frequency; flat where the CDR tracks, rolling off ~20 "
                 "dB/dec above the loop bandwidth. Reduced-fidelity sweep "
                 "(BER 1e-3, ~12k symbols) — raise n_symbols in scripts for a "
                 "precise curve.", style={"fontSize": "0.75rem",
                 "color": "#5b6472"}),
    ])
