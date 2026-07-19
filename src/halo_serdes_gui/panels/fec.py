"""FEC tab — pre->post-FEC projection (KP4/KR4/concatenated)."""

from __future__ import annotations

import numpy as np
from dash import html

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "FEC"
TAB_ID = "fec"


def render(rec: RunRecord):
    p = studies.fec_projection()
    traces = [
        {"x": p["pre"], "y": np.maximum(p["kp4"], 1e-30), "name": "KP4 (544,514)",
         "mode": "lines", "color": theme.PRIMARY},
        {"x": p["pre"], "y": np.maximum(p["kr4"], 1e-30), "name": "KR4 (528,514)",
         "mode": "lines", "color": theme.ACCENT},
        {"x": p["pre"], "y": np.maximum(p["concat"], 1e-30),
         "name": "+ concat inner BCH(255,5)", "mode": "lines", "color": theme.GOOD},
    ]
    fig = figures.lines_fig(traces, title="Pre → post-FEC BER projection",
                            xtitle="pre-FEC BER", ytitle="post-FEC BER",
                            logy=True, height=440,
                            hlines=[{"y": 1e-15, "text": "1e-15 target",
                                     "color": theme.GOOD}])
    fig.update_xaxes(type="log")
    cards = []
    if rec is not None and rec.ok and rec.sim is not None:
        pre = rec.sim.ber.ber
        if pre > 0:
            from halo_serdes.fec import pre_to_post_fec_ber
            post = pre_to_post_fec_ber(pre, "kp4")
            cards = [theme.metric_card("This run pre-FEC", f"{pre:.2e}", "info"),
                     theme.metric_card("→ post-KP4", f"{post:.1e}",
                                       "good" if post < 1e-15 else "warn")]
            fig.add_vline(x=pre, line=dict(color=theme.MUTED, dash="dash", width=1),
                          annotation_text="this run")
    return html.Div([
        common.cards_row(cards),
        common.graph(fig),
        html.Div("RS-KP4/KR4 hard-decision projection (bits_per_fec_symbol_error "
                 "model) plus a concatenated inner-BCH scheme that raises the "
                 "tolerable pre-FEC BER by ~2 orders.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
