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
    import shutil

    banners = []
    # real C-ABI execution: a compiled AMI model, no vendor binary needed
    if shutil.which("cc") or shutil.which("gcc"):
        banners.append(theme.banner(
            "info", "C compiler present — the shipped reference IBIS-AMI model "
            "(io/ami_c/halo_fir_ami.c) compiles and runs over the real AMI C ABI "
            "via load_ami_model(so_file=...) / AmiCModel."))
    else:
        banners.append(theme.banner(
            "warn", "No C compiler — AmiCModel (real .so execution) unavailable; "
            "the native FIR reference AMI model still works."))
    # pyibisami backend for vendor .ami/.dll
    try:
        import pyibisami  # noqa: F401
        banners.append(theme.banner(
            "info", "pyibisami available — vendor .ami/.dll models loadable via "
            "load_ami_model(ami_file=..., dll_file=...)."))
    except Exception:
        banners.append(theme.banner(
            "warn", "pyibisami not installed — vendor .ami/.dll binding disabled; "
            "the compiled reference model and native FIR still exercise the seam."))
    return html.Div(banners)


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    body = [_ami_status()]
    c = studies.com_study(rec)
    if "error" in c:
        body.append(dbc.Alert(c["error"], color="light", className="border"))
    else:
        traces = [{"x": c["loss"], "y": c["com_93a"], "name": "802.3 COM (93A/178A)",
                   "mode": "lines+markers", "color": theme.PRIMARY}]
        if "com_db" in c:
            traces.append({"x": c["loss"], "y": c["com_db"],
                           "name": "behavioral RSS FoM", "mode": "lines+markers",
                           "dash": "dot", "color": theme.MUTED})
        fig = figures.lines_fig(
            traces, title="COM vs channel loss",
            xtitle="channel loss @ Nyquist [dB]", ytitle="COM [dB]", height=420,
            hlines=[{"y": 3.0, "text": "≈3 dB pass", "color": theme.GOOD}])
        body.append(common.graph(fig))
    body.append(html.Div("Solid = faithful IEEE 802.3 COM (Clause 93A/178A): "
                "the equalizer is optimized over a CTLE/DFE grid by FOM, the DFE "
                "taps are derived from the cursors with a b_max bound, and A_ni "
                "is read off the convolved interference-plus-noise PDF at the "
                "target DER — not a Gaussian RSS. Dotted = the transparent RSS "
                "figure of merit (NativeCom) for reference. Both plug into the "
                "same ComAdapter.compute seam.",
                style={"fontSize": "0.75rem", "color": "#5b6472"}))
    return html.Div(body)
