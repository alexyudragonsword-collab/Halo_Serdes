"""AMI / COM tab — behavioral COM vs loss + IBIS-AMI seam status."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "AMI / COM"
TAB_ID = "amicom"


def _ami_status():
    try:
        import pyibisami  # noqa: F401
        txt, tone = "pyibisami available — vendor .ami/.so models loadable", "good"
    except Exception:
        txt, tone = ("pyibisami not installed — native FIR reference AMI model "
                     "in use (load_ami_model(taps=...)); install pyibisami to "
                     "bind vendor models."), "muted"
    return theme.banner("info" if tone == "good" else "warn", txt)


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    body = [_ami_status()]
    c = studies.com_study(rec)
    if "error" in c:
        body.append(dbc.Alert(c["error"], color="light", className="border"))
    else:
        fig = figures.lines_fig(
            [{"x": c["loss"], "y": c["com_db"], "name": "behavioral COM",
              "mode": "lines+markers", "color": theme.PRIMARY}],
            title="Behavioral COM vs channel loss",
            xtitle="channel loss @ Nyquist [dB]", ytitle="COM [dB]", height=420,
            hlines=[{"y": 3.0, "text": "≈3 dB pass", "color": theme.GOOD}])
        body.append(common.graph(fig))
    body.append(html.Div("COM here is a transparent behavioral figure of merit "
                "(signal / RSS of ISI+crosstalk+noise+jitter) via the same "
                "ComAdapter.compute seam the official 802.3 tool would plug "
                "into.", style={"fontSize": "0.75rem", "color": "#5b6472"}))
    return html.Div(body)
