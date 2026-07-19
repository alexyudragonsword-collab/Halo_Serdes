"""Backchannel tab — KR-style Tx FIR training trajectories."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Backchannel"
TAB_ID = "backchannel"

_CACHE: dict[str, object] = {}


def _train(rec: RunRecord):
    if rec.id in _CACHE:
        return _CACHE[rec.id]
    try:
        from halo_serdes.channel import ChannelModel
        from halo_serdes.engine import train_tx_fir
        res = train_tx_fir(rec.cfg, channel=ChannelModel.from_config(rec.cfg),
                           n_pre=1, n_post=2, dfe_covered=rec.cfg.rx.dfe.n_taps)
    except Exception as exc:
        res = exc
    _CACHE[rec.id] = res
    return res


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    res = _train(rec)
    if isinstance(res, Exception):
        return dbc.Alert(f"Tx FIR training unavailable: {res}", color="warning",
                         className="border")
    cards = [
        theme.metric_card("Converged", "yes" if res.converged else "no",
                          "good" if res.converged else "warn",
                          f"{res.rounds} rounds"),
        theme.metric_card("Trained taps",
                          ", ".join(f"{t:.3f}" for t in res.taps), "muted"),
    ]
    return html.Div([
        common.cards_row(cards),
        common.graph(figures.backchannel_fig(res, height=380)),
        html.Div("KR-style sign-LMS Tx FIR training (peak-power constrained); "
                 "postcursors the RX DFE covers are excluded from the objective.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
