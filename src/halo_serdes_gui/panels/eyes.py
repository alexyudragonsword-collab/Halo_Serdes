"""Eyes tab — analog front-end eye (reconstructed) + post-EQ eye."""

from __future__ import annotations

import dataclasses

import dash_bootstrap_components as dbc
from dash import html

from .. import figures
from ..runner import RunRecord
from . import common

TITLE = "Eyes"
TAB_ID = "eyes"


def _fe_eye(cfg):
    # cap symbols for a responsive reconstruction
    from halo_serdes.analysis.reconstruct import front_end_eye
    small = dataclasses.replace(cfg, sim=dataclasses.replace(
        cfg.sim, n_symbols=min(cfg.sim.n_symbols, 4000)))
    try:
        return front_end_eye(small)
    except Exception:
        return None


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    cfg = rec.cfg
    fe = _fe_eye(cfg)
    fe_label = ("Analog ADC-input eye (Tx+channel+CTLE)"
                if cfg.rx.arch == "adc_dsp"
                else "Analog front-end eye (CTLE output)")
    left = common.graph(figures.eye_fig(fe, title=fe_label))

    if rec.sim is not None and rec.sim.eye_data is not None:
        right = common.graph(figures.eye_fig(rec.sim.eye_data,
                             title="Post-EQ eye (slicer input)"))
    elif cfg.rx.arch == "adc_dsp":
        right = dbc.Alert("The ADC receiver opens the eye in the digital "
                          "domain — see the slicer histogram (Single Run) and "
                          "the ADC tab for per-lane diagnostics.", color="light",
                          className="border h-100 d-flex align-items-center")
    else:
        right = figures.placeholder("no post-EQ eye")
        right = common.graph(right)

    return html.Div([
        common.warnings_block(rec),
        dbc.Row([dbc.Col(left, lg=6), dbc.Col(right, lg=6)], className="g-2"),
        html.Div("Analog eye is reconstructed from the LTI front end (shared "
                 "halo_serdes.analysis.reconstruct); the post-EQ eye is the "
                 "engine's captured slicer-input fold.",
                 style={"fontSize": "0.75rem", "color": "#5b6472",
                        "marginTop": "0.3rem"}),
    ])
