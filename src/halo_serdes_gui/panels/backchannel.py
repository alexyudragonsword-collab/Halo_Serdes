"""Backchannel tab — KR-style Tx FIR training trajectories."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Backchannel"
TAB_ID = "backchannel"

# Bounded, and deliberately so: training is seconds of work per record, which
# is worth caching, but this used to be an unbounded dict keyed by run id --
# one entry per run for the lifetime of the process, holding a result object
# each. Same eviction shape as halo_serdes_app.studies.
_CACHE: dict[str, object] = {}
_ORDER: list[str] = []
_MAX = 16


def _train(rec: RunRecord):
    if rec.id in _CACHE:
        return _CACHE[rec.id]
    try:
        from halo_serdes.channel import ChannelModel
        from halo_serdes.engine import train_tx_fir
        res = train_tx_fir(rec.cfg, channel=ChannelModel.from_config(rec.cfg),
                           n_pre=1, n_post=2, dfe_covered=rec.cfg.rx.dfe.n_taps)
    except Exception as exc:
        # Cached like any other outcome: training the same config twice gives
        # the same answer, so a failure here is a property of the config, not a
        # transient. `render` shows the reason.
        res = exc
    _CACHE[rec.id] = res
    _ORDER.append(rec.id)
    while len(_ORDER) > _MAX:
        _CACHE.pop(_ORDER.pop(0), None)
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
