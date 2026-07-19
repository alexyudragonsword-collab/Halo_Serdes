"""Channel tab — insertion loss, impulse, pulse+cursors, behavioral COM."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Channel"
TAB_ID = "channel"


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    cfg = rec.cfg
    try:
        from halo_serdes.channel import ChannelModel
        cm = ChannelModel.from_config(cfg)
    except Exception as exc:
        return dbc.Alert(f"channel unavailable: {exc}", color="warning",
                         className="border")

    cards = [theme.metric_card("Loss @ Nyquist",
                               f"{cm.loss_at(cfg.f_nyquist):.1f} dB", "info",
                               f"{cfg.f_nyquist/1e9:.1f} GHz")]
    com_fig = None
    try:
        from halo_serdes.io import NativeCom
        com = NativeCom(n_dfe=max(cfg.rx.dfe.n_taps, 1),
                        rx_ffe_taps=cfg.rx.ffe.n_pre + 1 + cfg.rx.ffe.n_post,
                        rx_ffe_pre=cfg.rx.ffe.n_pre).compute(cm, cfg)
        tone = "good" if com.com_db > 3 else "warn" if com.com_db > 0 else "crit"
        cards.append(theme.metric_card("Behavioral COM", f"{com.com_db:.1f} dB",
                                       tone, "≥3 dB pass"))
        cards.append(theme.metric_card("Signal / noise",
                                       f"{com.a_signal:.3f} / {com.a_noise:.3f} V",
                                       "muted"))
        com_fig = common.graph(figures.com_breakdown_fig(com))
    except Exception as exc:
        cards.append(theme.metric_card("COM", "n/a", "muted", str(exc)[:20]))

    body = [common.cards_row(cards), common.warnings_block(rec),
            dbc.Row([
                dbc.Col(common.graph(figures.channel_loss_fig(cm, cfg.f_nyquist)), lg=6),
                dbc.Col(common.graph(figures.impulse_fig(cm, cfg.dt)), lg=6),
            ], className="g-2"),
            dbc.Row([
                dbc.Col(common.graph(figures.pulse_cursors_fig(cm, cfg.dt, cfg.osr)), lg=6),
                dbc.Col(com_fig or figures.placeholder("COM unavailable"), lg=6),
            ], className="g-2")]
    return html.Div(body)
