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

def eye_fig(eye_traces, title: str = "Eye", height: int = 340,
            mode: str = "density") -> go.Figure:
    if eye_traces is None or getattr(eye_traces, "size", 0) == 0:
        return placeholder("no eye (run the time engine with eye capture)", height)
    traces = np.asarray(eye_traces)
    span = traces.shape[1]
    t_ui = (np.arange(span) - span / 2) / (span / 2)
    if mode == "lines":
        fig = go.Figure()
        for tr in traces[: min(300, traces.shape[0])]:
            fig.add_scatter(x=t_ui, y=tr, mode="lines",
                            line=dict(color="rgba(43,108,176,0.10)", width=1),
                            hoverinfo="skip", showlegend=False)
    else:
        counts, edges = eye_density(traces)
        with np.errstate(divide="ignore"):
            z = np.log10(counts + 1.0)
        v_c = (edges[:-1] + edges[1:]) / 2
        fig = go.Figure(go.Heatmap(z=z, x=t_ui, y=v_c, colorscale=theme.EYE_SCALE,
                                   colorbar=dict(title="log₁₀ hits", thickness=12)))
    fig.update_layout(**theme.layout(title, height=height))
    fig.update_xaxes(title_text="Time [UI]", **theme.axis())
    fig.update_yaxes(title_text="Amplitude [V]", **theme.axis())
    return fig


# --- statistical engine ----------------------------------------------------

def stat_eye_fig(stat, title="Statistical eye (log PDF)", height=340) -> go.Figure:
    if stat is None:
        return placeholder("run the statistical engine (engine = stat or both)",
                           height)
    pdf = np.asarray(stat.eye_pdf)
    with np.errstate(divide="ignore"):
        z = np.log10(pdf + 1e-18)
    fig = go.Figure(go.Heatmap(z=z, x=stat.phi_ui, y=stat.v_centers,
                               colorscale="Viridis", zmin=-12, zmax=z.max(),
                               colorbar=dict(title="log₁₀ PDF", thickness=12)))
    fig.update_layout(**theme.layout(title, height=height))
    fig.update_xaxes(title_text="phase [UI]", **theme.axis())
    fig.update_yaxes(title_text="Amplitude [V]", **theme.axis())
    return fig


def bathtub_fig(stat, mc_ber=None, title="Phase bathtub", height=340) -> go.Figure:
    if stat is None:
        return placeholder("run the statistical engine", height)
    fig = lines_fig([{"x": stat.phi_ui, "y": np.maximum(stat.ber_phi, 1e-30),
                      "name": "StatEye BER(φ)", "mode": "lines",
                      "color": theme.PRIMARY}],
                    title=title, xtitle="sampling phase [UI]", ytitle="BER",
                    logy=True, height=height)
    if mc_ber is not None and mc_ber > 0:
        fig.add_hline(y=mc_ber, line=dict(color=theme.ACCENT, dash="dash", width=1),
                      annotation_text=f"time-domain MC {mc_ber:.1e}")
    return fig


def slicer_pdf_compare_fig(stat, y_slicer, title="Slicer PDF: stat vs MC",
                           height=340) -> go.Figure:
    if stat is None:
        return placeholder("run the statistical engine", height)
    col = stat.eye_pdf[:, int(stat.best_phi)].astype(float)
    dv = stat.v_centers[1] - stat.v_centers[0]
    col = col / max(col.sum() * dv, 1e-30)
    traces = [{"x": stat.v_centers, "y": np.maximum(col, 1e-20),
               "name": "StatEye PDF", "mode": "lines", "color": theme.PRIMARY}]
    if y_slicer is not None and getattr(y_slicer, "size", 0):
        hist, edges = np.histogram(np.asarray(y_slicer), bins=120, density=True)
        centers = (edges[:-1] + edges[1:]) / 2
        traces.append({"x": centers, "y": np.maximum(hist, 1e-20),
                       "name": "MC histogram", "mode": "lines",
                       "dash": "dot", "color": theme.ACCENT})
    return lines_fig(traces, title=title, xtitle="Amplitude [V]",
                     ytitle="density", logy=True, height=height)


# --- channel ---------------------------------------------------------------

def channel_loss_fig(cm, f_nyquist, title="Insertion loss", height=320):
    f = cm.f / 1e9
    fig = lines_fig([{"x": f, "y": cm.insertion_loss_db(), "name": "|H| [dB]",
                      "mode": "lines", "color": theme.PRIMARY}],
                    title=title, xtitle="Frequency [GHz]", ytitle="Loss [dB]",
                    height=height)
    fig.add_vline(x=f_nyquist / 1e9, line=dict(color=theme.MUTED, dash="dot", width=1),
                  annotation_text="Nyquist")
    return fig


def impulse_fig(cm, dt, title="Impulse response", height=300):
    h = cm.impulse(dt)
    t = np.arange(h.y.size) * dt * 1e9
    return lines_fig([{"x": t, "y": h.y, "name": "h(t)", "mode": "lines",
                       "color": theme.ACCENT}], title=title,
                     xtitle="Time [ns]", ytitle="amplitude", height=height)


def pulse_cursors_fig(cm, dt, osr, title="Pulse response + ISI cursors", height=300):
    from halo_serdes.dsp.ffe import channel_cursors

    pulse = cm.pulse(dt, osr)
    t = (np.arange(pulse.y.size) - int(np.argmax(np.abs(pulse.y)))) * dt * 1e12
    peak = int(np.argmax(np.abs(pulse.y)))
    cur = channel_cursors(pulse, osr, 4, 20, peak_idx=peak)
    cidx = (np.arange(-4, 21)) * osr
    ct = cidx * dt * 1e12
    fig = go.Figure()
    fig.add_scatter(x=t, y=pulse.y, mode="lines", name="pulse",
                    line=dict(color=theme.PRIMARY))
    fig.add_scatter(x=ct, y=cur, mode="markers", name="UI cursors",
                    marker=dict(color=theme.CRIT, size=7))
    fig.update_layout(**theme.layout(title, height=height))
    fig.update_xaxes(title_text="Time [ps] (rel. peak)", **theme.axis())
    fig.update_yaxes(title_text="amplitude", **theme.axis())
    return fig


def com_breakdown_fig(com, title="COM noise breakdown", height=300):
    labels = ["ISI", "Crosstalk", "Noise", "Jitter"]
    vals = [com.fom_isi, com.fom_xtalk, com.fom_noise, com.fom_jitter]
    fig = go.Figure(go.Bar(x=labels, y=vals, marker_color=theme.COLORWAY[:4]))
    fig.update_layout(**theme.layout(title, height=height), showlegend=False)
    fig.update_yaxes(title_text="σ contribution [V]", **theme.axis())
    fig.update_xaxes(**theme.axis())
    return fig


# --- CTLE ------------------------------------------------------------------

def ctle_bode_fig(cfg, title="CTLE frequency response", height=320):
    from halo_serdes.afe import Ctle

    if not cfg.rx.ctle.enable:
        return placeholder("CTLE disabled", height)
    ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
    f = np.linspace(1e7, 2.5 * cfg.f_nyquist, 800)
    mag = 20 * np.log10(np.abs(ctle.transfer(f)) + 1e-12)
    fig = lines_fig([{"x": f / 1e9, "y": mag, "name": "|H_CTLE| [dB]",
                      "mode": "lines", "color": theme.PRIMARY}],
                    title=title, xtitle="Frequency [GHz]", ytitle="Gain [dB]",
                    height=height)
    fig.add_vline(x=cfg.f_nyquist / 1e9, line=dict(color=theme.MUTED, dash="dot",
                  width=1), annotation_text="Nyquist")
    return fig


# --- jitter ----------------------------------------------------------------

def jitter_bar_fig(budget, ui, title="Per-stage jitter budget", height=340):
    if not isinstance(budget, dict) or "_note" in budget or not budget:
        return placeholder("no jitter budget — needs a repeating pattern "
                           "(≥4 periods, e.g. prbs7)", height)
    from halo_serdes.analysis.jitter import total_jitter

    stages = [s for s in ("tx", "chnl", "ctle") if s in budget] or list(budget)
    u = 100.0 / ui
    comp = {"ISI": [budget[s].isi * u for s in stages],
            "DCD": [budget[s].dcd * u for s in stages],
            "Pj": [budget[s].pj * u for s in stages],
            "Rj→1e-12": [budget[s].rj * u * 14.07 for s in stages]}
    colors = {"ISI": theme.COLORWAY[0], "DCD": theme.COLORWAY[1],
              "Pj": theme.COLORWAY[2], "Rj→1e-12": theme.COLORWAY[3]}
    fig = go.Figure()
    for name, vals in comp.items():
        fig.add_bar(x=stages, y=vals, name=name, marker_color=colors[name])
    tj = [total_jitter(budget[s], 1e-12) * u for s in stages]
    fig.add_scatter(x=stages, y=tj, mode="markers", name="TJ@1e-12",
                    marker=dict(color="black", symbol="line-ew-open", size=22,
                                line=dict(width=2)))
    fig.update_layout(**theme.layout(title, height=height), barmode="stack")
    fig.update_yaxes(title_text="Jitter [%UI]", **theme.axis())
    fig.update_xaxes(**theme.axis())
    return fig


# --- adaptation / CDR ------------------------------------------------------

def _stage_shading(fig, settle, train_end):
    fig.add_vrect(x0=0, x1=settle, fillcolor=theme.MUTED, opacity=0.08,
                  line_width=0, annotation_text="CDR settle",
                  annotation_position="top left")
    fig.add_vrect(x0=settle, x1=train_end, fillcolor=theme.PRIMARY, opacity=0.06,
                  line_width=0, annotation_text="train", annotation_position="top left")


def dfe_traj_fig(w_hist, w_final, settle, train_end, n_ave,
                 title="DFE tap trajectories", height=340) -> go.Figure:
    if w_hist is None or np.size(w_hist) == 0:
        return placeholder("no adaptation history (set dfe.adapt to lms/sign_sign)",
                           height)
    w = np.asarray(w_hist)
    x = np.arange(w.shape[0]) * n_ave
    fig = go.Figure()
    for j in range(w.shape[1]):
        c = theme.COLORWAY[j % len(theme.COLORWAY)]
        fig.add_scatter(x=x, y=w[:, j], mode="lines", name=f"tap {j+1}",
                        line=dict(color=c))
        if w_final is not None and j < len(w_final):
            fig.add_hline(y=float(w_final[j]), line=dict(color=c, dash="dot",
                          width=1))
    _stage_shading(fig, settle, train_end)
    fig.update_layout(**theme.layout(title, height=height))
    fig.update_xaxes(title_text="symbol", **theme.axis())
    fig.update_yaxes(title_text="tap weight", **theme.axis())
    return fig


def convergence_fig(w_hist, w_final, n_ave, title="Convergence", height=340):
    if w_hist is None or np.size(w_hist) == 0 or w_final is None:
        return placeholder("no convergence history", height)
    w = np.asarray(w_hist)
    err = np.linalg.norm(w - np.asarray(w_final)[None, :], axis=1)
    x = np.arange(w.shape[0]) * n_ave
    return lines_fig([{"x": x, "y": np.maximum(err, 1e-12), "name": "‖w−w∞‖",
                       "mode": "lines", "color": theme.ACCENT}],
                     title=title, xtitle="symbol", ytitle="tap-vector error",
                     logy=True, height=height)


def cdr_phase_fig(phase_track, osr, settle, train_end,
                  title="CDR recovered phase", height=340) -> go.Figure:
    if phase_track is None or np.size(phase_track) == 0:
        return placeholder("no CDR phase track", height)
    ph = np.asarray(phase_track) / osr  # oversample units -> UI
    x = np.arange(ph.size)
    fig = go.Figure()
    fig.add_scatter(x=x, y=ph, mode="lines", name="phase [UI]",
                    line=dict(color=theme.PRIMARY))
    _stage_shading(fig, settle, train_end)
    fig.update_layout(**theme.layout(title, height=height))
    fig.update_xaxes(title_text="symbol", **theme.axis())
    fig.update_yaxes(title_text="recovered phase [UI]", **theme.axis())
    return fig


def pd_activity_fig(pd_hist, win=200, title="Phase-detector activity", height=300):
    if pd_hist is None or np.size(pd_hist) == 0:
        return placeholder("no PD history", height)
    pd = np.asarray(pd_hist, dtype=float)
    k = np.ones(win) / win
    ma = np.convolve(pd, k, mode="valid")
    x = np.arange(ma.size)
    fig = lines_fig([{"x": x, "y": ma, "name": f"PD mean ({win})", "mode": "lines",
                      "color": theme.ACCENT}], title=title, xtitle="symbol",
                    ytitle="early/late bias", height=height)
    fig.add_hline(y=0.0, line=dict(color=theme.MUTED, width=1, dash="dot"))
    return fig


# --- ADC -------------------------------------------------------------------

def lane_ser_fig(lane_ser, title="Per-lane SER (TI mismatch)", height=320):
    if lane_ser is None or np.size(lane_ser) == 0:
        return placeholder("no per-lane SER (ADC arch only)", height)
    ls = np.asarray(lane_ser, dtype=float)
    y = np.maximum(ls, 1e-9)
    fig = go.Figure(go.Bar(x=np.arange(ls.size), y=y, marker_color=theme.PRIMARY))
    fig.update_layout(**theme.layout(title, height=height), showlegend=False)
    fig.update_xaxes(title_text="ADC lane", **theme.axis())
    fig.update_yaxes(title_text="SER", type="log", **theme.axis())
    return fig


def adc_codes_fig(q_hist, title="ADC code histogram", height=300):
    if q_hist is None or np.size(q_hist) == 0:
        return placeholder("no ADC codes (ADC arch only)", height)
    fig = go.Figure(go.Histogram(x=np.asarray(q_hist), nbinsx=80,
                                 marker_color=theme.COLORWAY[4]))
    fig.update_layout(**theme.layout(title, height=height), bargap=0.02)
    fig.update_xaxes(title_text="ADC code", **theme.axis())
    fig.update_yaxes(title_text="count", **theme.axis())
    return fig


def lane_mismatch_fig(adc, title="Per-lane mismatch", height=300):
    if adc is None:
        return placeholder("no ADC model", height)
    lanes = np.arange(adc.n_lanes)
    fig = make_subplots(rows=1, cols=2, subplot_titles=["offset [code]", "skew [UI]"])
    fig.add_bar(x=lanes, y=np.asarray(adc.offsets), marker_color=theme.COLORWAY[1],
                row=1, col=1)
    fig.add_bar(x=lanes, y=np.asarray(adc.skews), marker_color=theme.COLORWAY[2],
                row=1, col=2)
    fig.update_layout(**theme.layout(title, height=height), showlegend=False)
    for c in (1, 2):
        fig.update_xaxes(title_text="lane", row=1, col=c, **theme.axis())
    return fig


# --- backchannel -----------------------------------------------------------

def backchannel_fig(res, title="Tx FIR training", height=340):
    if res is None:
        return placeholder("no training result", height)
    ch = np.asarray(res.cursor_history)
    th = np.asarray(res.tap_history)
    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=["cursors / main vs round", "Tx taps vs round"])
    rounds = np.arange(ch.shape[0])
    for j in range(ch.shape[1]):
        fig.add_scatter(x=rounds, y=ch[:, j], mode="lines", row=1, col=1,
                        line=dict(color=theme.COLORWAY[j % 8]),
                        name=f"cur {j}", showlegend=False)
    for j in range(th.shape[1]):
        fig.add_scatter(x=rounds, y=th[:, j], mode="lines+markers", row=1, col=2,
                        line=dict(color=theme.COLORWAY[j % 8]),
                        name=f"tap {j}", showlegend=False)
    fig.update_layout(**theme.layout(title, height=height))
    for c in (1, 2):
        fig.update_xaxes(title_text="round", row=1, col=c, **theme.axis())
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
