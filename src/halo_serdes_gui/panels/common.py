"""Shared panel helpers: result cards, warnings/error blocks, BER formatting."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import dcc, html

from .. import theme
from ..runner import RunRecord


def ber_text(ber: float, n: int) -> tuple[str, str]:
    """(display, tone) for a BER value; 0 errors -> confidence upper bound."""
    if n and ber == 0.0:
        return f"< {1.0 / n:.0e}", "good"
    tone = "good" if ber < 1e-4 else "warn" if ber < 1e-2 else "crit"
    return f"{ber:.2e}", tone


def error_block(rec: RunRecord):
    return html.Div([
        dbc.Alert(rec.error, color="danger", className="mb-2"),
        dbc.Collapse(html.Pre(rec.tb or "", style={"fontSize": "0.72rem",
                     "whiteSpace": "pre-wrap"}),
                     id="err-tb-collapse", is_open=False),
    ])


def warnings_block(rec: RunRecord):
    if not rec.warnings:
        return None
    return html.Div([theme.banner("warn", w) for w in dict.fromkeys(rec.warnings)])


def need_run_message():
    return dbc.Alert("Load a preset (or edit the config) and press "
                     "▶ Run to populate this tab.", color="light",
                     className="border")


def graph(fig, **kw):
    return dcc.Graph(figure=fig, config={"displaylogo": False,
                     "toImageButtonOptions": {"format": "png", "scale": 2}},
                     **kw)


def cards_row(cards):
    return dbc.Row([c for c in cards if c is not None], className="g-0 mb-1")
