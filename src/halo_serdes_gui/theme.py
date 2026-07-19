"""Visual system: palette, Plotly layout defaults, and shared components."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

# --- palette ---------------------------------------------------------------
INK = "#1a202c"
MUTED = "#5b6472"
PRIMARY = "#2b6cb0"
ACCENT = "#dd6b20"
GOOD = "#2f855a"
WARN = "#b7791f"
CRIT = "#c53030"
GRID = "#e6eaf0"
PANEL = "#ffffff"
EYE_SCALE = "Inferno"

COLORWAY = ["#2b6cb0", "#dd6b20", "#2f855a", "#c53030", "#805ad5",
            "#319795", "#d69e2e", "#718096"]

TEMPLATE = "plotly_white"
BOOTSTRAP = dbc.themes.FLATLY  # clean professional bootstrap theme

_TONE = {"ok": GOOD, "good": GOOD, "warn": WARN, "crit": CRIT,
         "info": PRIMARY, "muted": MUTED}


def layout(title: str = "", height: int | None = None, **kw) -> dict:
    """Standard Plotly layout dict for every figure in the GUI."""
    lay = dict(
        template=TEMPLATE, colorway=COLORWAY,
        title=dict(text=title, x=0.02, xanchor="left",
                   font=dict(size=15, color=INK)),
        margin=dict(l=64, r=20, t=44 if title else 16, b=48),
        font=dict(family="Inter, Segoe UI, system-ui, sans-serif",
                  size=12, color=INK),
        legend=dict(bgcolor="rgba(255,255,255,0.7)", font=dict(size=11)),
        hovermode="closest",
    )
    if height:
        lay["height"] = height
    lay.update(kw)
    return lay


def axis(**kw) -> dict:
    d = dict(gridcolor=GRID, zerolinecolor=GRID, linecolor="#cbd5e0",
             ticks="outside", ticklen=4, tickcolor="#cbd5e0")
    d.update(kw)
    return d


# --- components ------------------------------------------------------------

def metric_card(label: str, value: str, tone: str = "info",
                sub: str = "") -> dbc.Col:
    color = _TONE.get(tone, PRIMARY)
    body = [
        html.Div(label, style={"fontSize": "0.72rem", "letterSpacing": "0.04em",
                               "textTransform": "uppercase", "color": MUTED}),
        html.Div(value, style={"fontSize": "1.5rem", "fontWeight": 700,
                               "color": color, "fontVariantNumeric": "tabular-nums",
                               "lineHeight": 1.1}),
    ]
    if sub:
        body.append(html.Div(sub, style={"fontSize": "0.72rem", "color": MUTED}))
    return dbc.Col(dbc.Card(dbc.CardBody(body, style={"padding": "0.6rem 0.8rem"}),
                            style={"borderLeft": f"3px solid {color}"}),
                   width="auto", className="me-2 mb-2")


def banner(level: str, message: str):
    if not message:
        return None
    kind = {"warn": "warning", "crit": "danger", "ok": "success",
            "info": "info"}.get(level, "info")
    return dbc.Alert(message, color=kind, className="py-2 mb-2",
                     style={"fontSize": "0.85rem"})


def section_title(text: str):
    return html.Div(text, style={"fontSize": "0.78rem", "fontWeight": 700,
                                 "textTransform": "uppercase",
                                 "letterSpacing": "0.05em", "color": MUTED,
                                 "margin": "0.4rem 0 0.2rem"})
