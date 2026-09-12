"""CTLE tab — frequency response (Bode) and peaking summary."""

from __future__ import annotations

from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "CTLE"
TAB_ID = "ctle"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    cfg = rec.cfg
    cards = []
    note = None
    if cfg.rx.ctle.enable:
        try:
            from halo_serdes.afe import Ctle
            c = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
            cards = [theme.metric_card("Realized peaking",
                                       f"{c.peaking_db():.1f} dB", "info",
                                       f"target {cfg.rx.ctle.peak_db:.1f} dB"),
                     theme.metric_card("DC gain", f"{cfg.rx.ctle.gdc_db:.1f} dB",
                                       "muted")]
        except Exception as exc:
            # `pass` here dropped the cards with no trace, so an unbuildable
            # CTLE (fz above fp1, say) looked identical to one that had simply
            # not been configured.
            note = theme.banner(
                "warn", f"Cannot build the CTLE from this config, so realized "
                        f"peaking is unknown: {type(exc).__name__}: {exc}")
    return html.Div([
        note,
        common.cards_row(cards),
        common.graph(figures.ctle_bode_fig(cfg, height=420)),
        html.Div("CTLE zero/poles default from the peaking target and Nyquist; "
                 "override fz/fp1/fp2 in the config to shape the response.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
