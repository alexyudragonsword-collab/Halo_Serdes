"""Single Run tab — verdict, result cards, eye, slicer histogram, adapted taps."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Single Run"
TAB_ID = "single"


def _metric_cards(rec: RunRecord):
    """BER / SER / SNR / TJ of the time run, engines and run time."""
    cards = []
    sim = rec.sim
    if sim is not None:
        disp, tone = common.rate_text(sim.ber.ber, sim.ber.n_checked)
        cards.append(theme.metric_card("pre-FEC BER", disp, tone,
                                       f"{sim.ber.n_errors:,} err / {sim.ber.n_checked:,} bits"))
        disp, tone = common.rate_text(sim.ser, sim.n_symbols)
        cards.append(theme.metric_card("SER", disp, tone, f"{sim.n_symbols:,} sym"))
        cards.append(theme.metric_card("Slicer SNR", f"{sim.slicer_snr_db:.1f} dB",
                                       "info"))
        jb = sim.extras.get("jitter_budget")
        # the jitter budget is read at the analog nodes: on a mixed-signal
        # receiver that is the CTLE output the slicer works on; on an ADC
        # receiver the CTLE output is the closed analog eye before the DSP
        # (151 %UI on the 224G preset), which would mislead as a headline
        if (isinstance(jb, dict) and "_note" not in jb and jb
                and rec.cfg.rx.arch == "mixed_signal"):
            from halo_serdes.analysis.jitter import total_jitter
            stage = "ctle" if "ctle" in jb else next(iter(jb))
            tj = total_jitter(jb[stage], 1e-12) / rec.cfg.ui * 100
            cards.append(theme.metric_card("TJ@1e-12", f"{tj:.1f} %UI", "info",
                                           f"at the {stage} output"))
    cards.append(theme.metric_card("Engine", "+".join(rec.engines) or "—",
                                   "muted", f"{rec.elapsed_s:.2f} s"))
    return common.cards_row(cards)


_CALL = {"pass": ("Pass", "good"), "fail": ("Fail", "crit"),
         "disagree": ("Engines disagree", "warn"), "open": ("Not decided", "muted")}
_ENGINE = {"time": "Time domain", "stat": "StatEye"}


def _margin(ratio: float) -> str:
    """How far below the threshold: a factor up to 100x, decades beyond, and
    no number past 10 decades (a StatEye BER of 2.4e-33 read "8.75e+27x")."""
    import math
    if ratio < 100:
        return f"{ratio:.3g}× below the threshold"
    decades = math.log10(ratio)
    if decades > 10:
        # an extrapolated StatEye tail this deep is not a margin anyone uses
        return "more than 10 decades below the threshold"
    return f"{decades:.1f} decades below the threshold"


def verdict_block(rec: RunRecord):
    """The verdict against the FEC threshold with each engine's call, the
    StatEye BER and the time / StatEye ratio. Needs the statistical result,
    so the app fills it in after the run (``ensure_stat``) rather than
    holding the time-domain cards back for it."""
    v = common.verdict(rec)
    word, tone = _CALL[v["overall"]]
    color = theme.TONE[tone]
    lines = [html.Div(f"{_ENGINE[e]}: {c} — {why}") for e, (c, why) in v["calls"].items()]
    if rec.stat is None and rec.stat_error:
        lines.append(html.Div(f"StatEye failed, no cross-check: {rec.stat_error}",
                              style={"color": theme.CRIT}))
    margin = ""
    if v["overall"] == "pass" and v["best"]:
        margin = " · " + _margin(v["threshold"] / v["best"])
    head = html.Div([
        html.Div(f"vs {v['code']} pre-FEC threshold {common.sci(v['threshold'], 3)} "
                 f"(post-FEC 1e-15)", style={"fontSize": "0.72rem", "color": theme.MUTED}),
        html.Div(word + margin, style={"fontSize": "1.35rem", "fontWeight": 700,
                                       "color": color}),
        html.Div(lines, style={"fontSize": "0.78rem", "color": theme.INK}),
    ], className="verdict-card", style={
        "border": f"1px solid {theme.GRID}", "borderLeft": f"4px solid {color}",
        "background": theme.PANEL, "borderRadius": "6px",
        "padding": "0.6rem 0.8rem", "marginBottom": "0.5rem"})

    cards = []
    if rec.stat is not None:
        # an exact 0 is an underflow, not a measurement: the tail is below
        # what a double holds
        cards.append(theme.metric_card("StatEye BER", common.sci(rec.stat.ber),
                                       common.rate_text(rec.stat.ber, 0)[1],
                                       "below double precision" if rec.stat.ber == 0
                                       else "extrapolated"))
    if v["ratio"] is not None:
        n = v["errors"]
        # under ~10 counted errors the time BER itself is uncertain by more
        # than the 2x the cross-check allows, so the ratio is shown, not judged
        rtone = ("muted" if n < 10 else "good" if 0.5 <= v["ratio"] <= 2.0 else "warn")
        cards.append(theme.metric_card("Time / StatEye", f"{v['ratio']:.2f}×", rtone,
                                       f"{n:,} time-domain errors"))
    elif rec.sim is not None and rec.stat is not None:
        cards.append(theme.metric_card("Time / StatEye", "—", "muted",
                                       "no time-domain errors to compare"))
    bathtub = None
    if rec.stat is not None:
        mc = rec.sim.ber.ber if rec.sim is not None else None
        bathtub = common.graph(figures.bathtub_fig(rec.stat, mc_ber=mc, height=300))
    return html.Div([head, common.cards_row(cards), bathtub])


def verdict_slot(rec: RunRecord):
    """Where the verdict goes: the block itself when the statistical result
    is already known, else a placeholder the app's callback replaces."""
    if rec.stat is not None or rec.stat_error is not None:
        return verdict_block(rec)
    return html.Div(theme.banner("info", "Computing the StatEye cross-check and the "
                                 "verdict…"),
                    id={"type": "verdict", "rid": rec.id})


def _stat_bathtub(stat, cfg):
    """Phase bathtub for a statistical-only run (no time-domain plots to show)."""
    return figures.lines_fig(
        [{"x": stat.phi_ui, "y": stat.ber_phi, "name": "BER(φ)", "mode": "lines"}],
        title="Phase bathtub (statistical)", xtitle="sampling phase [UI]",
        ytitle="BER", logy=True, height=340)


def render(rec: RunRecord):
    """Headline cards, eye, slicer histogram, taps and the result summary."""
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)

    body = [verdict_slot(rec), _metric_cards(rec)]
    sim, stat, cfg = rec.sim, rec.stat, rec.cfg

    if sim is not None:
        levels = sim.extras.get("levels")
        ffe_pre = cfg.rx.ffe.n_pre
        if sim.eye_data is not None:
            eye = figures.eye_fig(sim.eye_data, title="Eye (post-EQ node)")
        elif cfg.rx.arch == "adc_dsp":
            # an ADC receiver captures no analog post-EQ eye: show the FFE
            # output reconstructed from the converged taps (the Eyes view's)
            from .eyes import _post_ffe_eye_fig
            eye = _post_ffe_eye_fig(rec)
        else:
            eye = figures.eye_fig(None, title="Eye (post-EQ node)")
        row = dbc.Row([
            dbc.Col(common.graph(eye), lg=6),
            dbc.Col(common.graph(figures.slicer_hist_fig(
                sim.y_slicer, levels, title="Slicer-input histogram")), lg=6),
        ], className="g-2")
        taps = common.graph(figures.taps_fig(sim.ffe_taps, sim.dfe_taps,
                                             ffe_pre=ffe_pre))
        body += [row, taps,
                 html.Details([
                     html.Summary("Run summary (text)", style={"cursor": "pointer",
                                  "fontSize": "0.78rem", "color": theme.MUTED}),
                     html.Pre(sim.summary(), style={"fontSize": "0.78rem",
                              "background": "#f7f9fc", "padding": "0.6rem",
                              "borderRadius": "6px"})])]
    elif stat is not None:
        body.append(common.graph(_stat_bathtub(stat, cfg)))

    return html.Div(body)
