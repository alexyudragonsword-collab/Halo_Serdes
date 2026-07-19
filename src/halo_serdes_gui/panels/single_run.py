"""Single Run tab — result cards + eye + slicer histogram + adapted taps."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Single Run"
TAB_ID = "single"


def _metric_cards(rec: RunRecord):
    cards = []
    sim, stat = rec.sim, rec.stat
    if sim is not None:
        disp, tone = common.ber_text(sim.ber.ber, sim.ber.n_checked)
        cards.append(theme.metric_card("pre-FEC BER", disp, tone,
                                       f"{sim.ber.n_errors} err / {sim.n_symbols} sym"))
        cards.append(theme.metric_card("SER", f"{sim.ser:.2e}",
                                       "good" if sim.ser < 1e-3 else "warn"))
        cards.append(theme.metric_card("Slicer SNR", f"{sim.slicer_snr_db:.1f} dB",
                                       "info"))
        jb = sim.extras.get("jitter_budget")
        if isinstance(jb, dict) and "_note" not in jb and jb:
            from halo_serdes.analysis.jitter import total_jitter
            stage = "ctle" if "ctle" in jb else next(iter(jb))
            tj = total_jitter(jb[stage], 1e-12) / rec.cfg.ui * 100
            cards.append(theme.metric_card("TJ@1e-12", f"{tj:.1f} %UI", "info",
                                           f"@{stage}"))
    if stat is not None:
        d, t = common.ber_text(stat.ber, 0)
        cards.append(theme.metric_card("StatEye BER", f"{stat.ber:.2e}", t,
                                       "extrapolated"))
    cards.append(theme.metric_card("Engine", "+".join(rec.engines) or "—",
                                   "muted", f"{rec.elapsed_s:.2f} s"))
    return common.cards_row(cards)


def _stat_bathtub(stat, cfg):
    return figures.lines_fig(
        [{"x": stat.phi_ui, "y": stat.ber_phi, "name": "BER(φ)", "mode": "lines"}],
        title="Phase bathtub (statistical)", xtitle="sampling phase [UI]",
        ytitle="BER", logy=True, height=340)


def render(rec: RunRecord):
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)

    body = [_metric_cards(rec), common.warnings_block(rec)]
    sim, stat, cfg = rec.sim, rec.stat, rec.cfg

    if sim is not None:
        levels = sim.extras.get("levels")
        ffe_pre = cfg.rx.ffe.n_pre
        row = dbc.Row([
            dbc.Col(common.graph(figures.eye_fig(
                sim.eye_data, title="Eye (post-EQ node)")), lg=6),
            dbc.Col(common.graph(figures.slicer_hist_fig(
                sim.y_slicer, levels, title="Slicer-input histogram")), lg=6),
        ], className="g-2")
        taps = common.graph(figures.taps_fig(sim.ffe_taps, sim.dfe_taps,
                                             ffe_pre=ffe_pre))
        body += [row, taps,
                 html.Pre(sim.summary(), style={"fontSize": "0.78rem",
                          "background": "#f7f9fc", "padding": "0.6rem",
                          "borderRadius": "6px"})]
    elif stat is not None:
        body.append(common.graph(_stat_bathtub(stat, cfg)))

    return html.Div(body)
