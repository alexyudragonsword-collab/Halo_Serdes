"""Dual-Engine tab — statistical eye, phase bathtub, slicer-PDF cross-check."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Dual-Engine"
TAB_ID = "dual"


def _ensure_stat(rec: RunRecord):
    """Compute the (cheap, analytic) statistical result on demand and cache it.

    A failure is recorded on the record rather than swallowed. This view *is*
    invariant #3 — the statistical/time-domain cross-check — so "the statistical
    engine raised" and "the statistical engine was never run" must not render
    the same way.
    """
    if rec.stat is not None or rec.stat_error is not None:
        return rec.stat
    try:
        from halo_serdes.channel import ChannelModel
        from halo_serdes.engine.statistical import run_statistical
        rec.stat = run_statistical(rec.cfg,
                                   channel=ChannelModel.from_config(rec.cfg))
    except Exception as exc:
        rec.stat = None
        rec.stat_error = f"{type(exc).__name__}: {exc}"
    return rec.stat


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    stat = _ensure_stat(rec)
    mc_ber = rec.sim.ber.ber if rec.sim is not None else None
    y_slicer = rec.sim.y_slicer if rec.sim is not None else None

    body = [common.warnings_block(rec)]
    if stat is None and rec.stat_error:
        body.append(theme.banner(
            "crit", f"Statistical engine failed, so there is no cross-check to "
                    f"compare against: {rec.stat_error}"))
    body += [dbc.Row([
                dbc.Col(common.graph(figures.stat_eye_fig(stat)), lg=6),
                dbc.Col(common.graph(figures.bathtub_fig(stat, mc_ber=mc_ber)), lg=6),
            ], className="g-2"),
             common.graph(figures.slicer_pdf_compare_fig(stat, y_slicer)),
             html.Div("Statistical StatEye (analytic PDF, extrapolates to low "
                     "BER) vs the time-domain Monte-Carlo point — the framework's "
                      "dual-engine cross-check.", style={"fontSize": "0.75rem",
                      "color": "#5b6472", "marginTop": "0.3rem"})]
    return html.Div(body)
