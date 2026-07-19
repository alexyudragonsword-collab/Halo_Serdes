"""Eyes tab — front-end eye + post-EQ eye.

For the mixed-signal architecture there is a real analog eye at the slicer
input, so two eyes are shown (CTLE-output front end + captured post-EQ fold).

For the ADC/DSP architecture the analog eye is closed at the ADC input and the
receiver reopens it digitally, so three views are shown: the closed analog
ADC-input eye, the *reconstructed* post-FFE eye (upsampled baud-rate taps
convolved with the analog waveform — a continuous-time equivalent of the
digital FFE output), and the slicer sample cloud (the DFE has no continuous
waveform; its output is the decision-instant value distribution). The
reconstruction is shared with example 16 via halo_serdes.analysis.reconstruct.
"""

from __future__ import annotations

import dataclasses

import dash_bootstrap_components as dbc
import numpy as np
from dash import html

from .. import figures
from ..runner import RunRecord
from . import common

TITLE = "Eyes"
TAB_ID = "eyes"


def _small(cfg, cap=4000):
    # cap symbols for a responsive reconstruction
    return dataclasses.replace(cfg, sim=dataclasses.replace(
        cfg.sim, n_symbols=min(cfg.sim.n_symbols, cap)))


def _fe_eye(cfg):
    from halo_serdes.analysis.reconstruct import front_end_eye
    try:
        return front_end_eye(_small(cfg))
    except Exception:
        return None


def _post_ffe_eye_fig(rec):
    """Reconstructed digital-FFE-output eye (ADC arch); needs converged taps."""
    from halo_serdes.analysis.reconstruct import post_ffe_eye
    sim = rec.sim
    if sim is None or sim.ffe_taps is None or len(np.asarray(sim.ffe_taps)) == 0:
        return figures.placeholder("run the time engine (needs converged FFE taps)")
    try:
        eye = post_ffe_eye(_small(rec.cfg), sim.ffe_taps)
    except Exception:
        return figures.placeholder("post-FFE eye reconstruction failed")
    return figures.eye_fig(eye, title="Reconstructed post-FFE eye (digital EQ)")


def _note(text: str):
    return html.Div(text, style={"fontSize": "0.75rem", "color": "#5b6472",
                                 "marginTop": "0.3rem"})


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    cfg = rec.cfg
    is_adc = cfg.rx.arch == "adc_dsp"
    fe = _fe_eye(cfg)
    fe_label = ("Analog ADC-input eye (Tx+channel+CTLE) — closed by design"
                if is_adc else "Analog front-end eye (CTLE output)")
    left = common.graph(figures.eye_fig(fe, title=fe_label))

    if is_adc:
        # digital domain: reconstructed FFE-output eye + true slicer cloud
        mid = common.graph(_post_ffe_eye_fig(rec))
        sim = rec.sim
        levels = sim.extras.get("levels") if sim is not None else None
        y_sl = sim.y_slicer if sim is not None else None
        right = common.graph(figures.slicer_cloud_fig(
            y_sl, levels, title="Slicer sample cloud (DFE output, what DSP sees)"))
        cols = [dbc.Col(left, lg=4), dbc.Col(mid, lg=4), dbc.Col(right, lg=4)]
        note = _note(
            "ADC/DSP receiver: the analog eye is closed at the ADC input and "
            "reopened digitally. Middle = FFE output reconstructed by upsampling "
            "the converged baud-rate taps onto the oversampled grid and "
            "convolving with the analog waveform (shared "
            "halo_serdes.analysis.reconstruct, as in example 16). The DFE is "
            "per-symbol nonlinear feedback with no continuous waveform, so its "
            "output is the slicer sample cloud on the right (1 point/UI) — the "
            "natural ADC-RX metrics are slicer-input SNR and SER, not a 2-D eye.")
    else:
        if rec.sim is not None and rec.sim.eye_data is not None:
            right = common.graph(figures.eye_fig(rec.sim.eye_data,
                                 title="Post-EQ eye (slicer input)"))
        else:
            right = common.graph(figures.placeholder("no post-EQ eye"))
        cols = [dbc.Col(left, lg=6), dbc.Col(right, lg=6)]
        note = _note(
            "Analog eye is reconstructed from the LTI front end (shared "
            "halo_serdes.analysis.reconstruct); the post-EQ eye is the engine's "
            "captured slicer-input fold.")

    return html.Div([
        common.warnings_block(rec),
        dbc.Row(cols, className="g-2"),
        note,
    ])
