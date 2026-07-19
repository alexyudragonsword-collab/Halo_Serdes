"""Plotly figure builders from ``SimResult`` / ``StatResult`` / ``extras``.

These consume only what the library already produces (result fields, ``extras``
keys, and library data functions such as ``eye_density``); they add no signal
processing. Every builder tolerates ``None`` / missing data and returns a
labelled placeholder instead of raising, so panels stay robust.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from halo_serdes.analysis.eye import eye_density

from . import theme


def placeholder(text: str = "no data", height: int = 320) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**theme.layout(height=height))
    fig.add_annotation(text=text, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(size=13, color=theme.MUTED))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


# --- eye -------------------------------------------------------------------

def eye_fig(eye_traces, title: str = "Eye", height: int = 340) -> go.Figure:
    if eye_traces is None or getattr(eye_traces, "size", 0) == 0:
        return placeholder("no eye (run the time engine with eye capture)", height)
    counts, edges = eye_density(np.asarray(eye_traces))
    with np.errstate(divide="ignore"):
        z = np.log10(counts + 1.0)
    span = eye_traces.shape[1]
    t_ui = (np.arange(span) - span / 2) / (span / 2)
    v_c = (edges[:-1] + edges[1:]) / 2
    fig = go.Figure(go.Heatmap(z=z, x=t_ui, y=v_c, colorscale=theme.EYE_SCALE,
                               colorbar=dict(title="log₁₀ hits", thickness=12)))
    fig.update_layout(**theme.layout(title, height=height))
    fig.update_xaxes(title_text="Time [UI]", **theme.axis())
    fig.update_yaxes(title_text="Amplitude [V]", **theme.axis())
    return fig


# --- slicer histogram ------------------------------------------------------

def slicer_hist_fig(y_slicer, levels=None, title="Slicer input",
                    height: int = 340) -> go.Figure:
    if y_slicer is None or getattr(y_slicer, "size", 0) == 0:
        return placeholder("no slicer samples", height)
    y = np.asarray(y_slicer)
    fig = go.Figure(go.Histogram(x=y, nbinsx=140, marker_color=theme.PRIMARY,
                                 opacity=0.85))
    if levels is not None:
        for lv in np.asarray(levels):
            fig.add_vline(x=float(lv), line=dict(color=theme.CRIT, width=1,
                                                 dash="dash"))
    fig.update_layout(**theme.layout(title, height=height), bargap=0.02)
    fig.update_xaxes(title_text="Slicer-input amplitude [V]", **theme.axis())
    fig.update_yaxes(title_text="Count", **theme.axis())
    return fig


# --- equalizer taps --------------------------------------------------------

def taps_fig(ffe_taps=None, dfe_taps=None, ffe_pre: int = 0,
             title="Equalizer taps", height: int = 320) -> go.Figure:
    have_ffe = ffe_taps is not None and len(np.asarray(ffe_taps)) > 0
    have_dfe = dfe_taps is not None and len(np.asarray(dfe_taps)) > 0
    if not have_ffe and not have_dfe:
        return placeholder("no adapted taps", height)
    n = int(have_ffe) + int(have_dfe)
    titles = ([f"FFE ({len(np.asarray(ffe_taps))} taps)"] if have_ffe else []) + \
             ([f"DFE ({len(np.asarray(dfe_taps))} taps)"] if have_dfe else [])
    fig = make_subplots(rows=1, cols=n, subplot_titles=titles)
    col = 1
    if have_ffe:
        w = np.asarray(ffe_taps)
        idx = np.arange(w.size) - ffe_pre
        fig.add_bar(x=idx, y=w, marker_color=theme.PRIMARY, row=1, col=col)
        fig.update_xaxes(title_text="tap (rel. main)", row=1, col=col, **theme.axis())
        fig.update_yaxes(title_text="weight", row=1, col=col, **theme.axis())
        col += 1
    if have_dfe:
        w = np.asarray(dfe_taps)
        fig.add_bar(x=np.arange(1, w.size + 1), y=w, marker_color=theme.ACCENT,
                    row=1, col=col)
        fig.update_xaxes(title_text="postcursor tap", row=1, col=col, **theme.axis())
        fig.update_yaxes(title_text="weight", row=1, col=col, **theme.axis())
    fig.update_layout(**theme.layout(title, height=height), showlegend=False)
    return fig


# --- generic line / semilogy (reused by many study tabs) -------------------

def lines_fig(traces: list[dict], title="", xtitle="", ytitle="",
              logy: bool = False, height: int = 360,
              hlines: list[dict] | None = None,
              vspans: list[dict] | None = None) -> go.Figure:
    """traces: list of {x, y, name, mode?, dash?, color?}."""
    fig = go.Figure()
    for t in traces:
        fig.add_scatter(x=t["x"], y=t["y"], name=t.get("name", ""),
                        mode=t.get("mode", "lines+markers"),
                        line=dict(dash=t.get("dash"), color=t.get("color")),
                        marker=dict(size=6))
    for h in (hlines or []):
        fig.add_hline(y=h["y"], line=dict(color=h.get("color", theme.GOOD),
                      width=1, dash=h.get("dash", "dot")),
                      annotation_text=h.get("text", ""))
    for s in (vspans or []):
        fig.add_vrect(x0=s["x0"], x1=s["x1"], fillcolor=s.get("color", "#000"),
                      opacity=s.get("opacity", 0.06), line_width=0,
                      annotation_text=s.get("text", ""))
    fig.update_layout(**theme.layout(title, height=height))
    fig.update_xaxes(title_text=xtitle, **theme.axis())
    fig.update_yaxes(title_text=ytitle, type="log" if logy else "linear",
                     **theme.axis())
    return fig
