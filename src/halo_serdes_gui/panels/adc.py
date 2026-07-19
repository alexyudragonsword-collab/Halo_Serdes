"""ADC tab — per-lane SER, TI mismatch, code histogram, FFE taps."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import numpy as np
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "ADC"
TAB_ID = "adc"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    if rec.cfg.rx.arch != "adc_dsp" or rec.sim is None:
        return dbc.Alert("Set rx.arch = adc_dsp (e.g. the 'PAM4 224G ADC' "
                         "preset) and Run to see ADC diagnostics.",
                         color="light", className="border")
    e = rec.sim.extras
    adc = e.get("adc")
    lane_ser = e.get("lane_ser")
    cards = []
    if lane_ser is not None and np.size(lane_ser):
        ls = np.asarray(lane_ser)
        cards.append(theme.metric_card("Lanes", str(getattr(adc, "n_lanes", "—")),
                                       "info"))
        cards.append(theme.metric_card("Worst-lane SER", f"{ls.max():.2e}",
                                       "warn" if ls.max() > 1e-2 else "good"))
        best = f"{ls.min():.2e}" if ls.min() > 0 else "0 (no errors)"
        cards.append(theme.metric_card("Best-lane SER", best, "muted"))
    return html.Div([
        common.cards_row(cards), common.warnings_block(rec),
        dbc.Row([
            dbc.Col(common.graph(figures.lane_ser_fig(lane_ser)), lg=6),
            dbc.Col(common.graph(figures.adc_codes_fig(e.get("q_hist_head"))), lg=6),
        ], className="g-2"),
        dbc.Row([
            dbc.Col(common.graph(figures.lane_mismatch_fig(adc)), lg=6),
            dbc.Col(common.graph(figures.taps_fig(
                rec.sim.ffe_taps, rec.sim.dfe_taps, ffe_pre=rec.cfg.rx.ffe.n_pre,
                title="Converged FFE / DFE taps")), lg=6),
        ], className="g-2"),
    ])
