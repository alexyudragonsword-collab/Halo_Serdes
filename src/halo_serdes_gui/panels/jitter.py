"""Jitter tab — per-stage decomposition table, stacked bar, timing bathtub,
and for a profile clock: the phase-noise profile against what the CDR leaves of it."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import numpy as np
from dash import html

from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Jitter"
TAB_ID = "jitter"


def _budget_table(budget, ui):
    """ISI / DCD / Pj / Rj / TJ per stage as %UI."""
    from halo_serdes.analysis.jitter import total_jitter
    u = 100.0 / ui
    head = html.Thead(html.Tr([html.Th(c) for c in
                      ["Stage", "ISI", "DCD", "Pj", "Rj(rms)", "TJ@1e-12"]]))
    rows = []
    for name, jr in budget.items():
        rows.append(html.Tr([
            html.Td(name),
            html.Td(f"{jr.isi*u:.2f}%"), html.Td(f"{jr.dcd*u:.2f}%"),
            html.Td(f"{jr.pj*u:.2f}%"), html.Td(f"{jr.rj*u:.3f}%"),
            html.Td(f"{total_jitter(jr,1e-12)*u:.2f}%"),
        ]))
    return dbc.Table([head, html.Tbody(rows)], bordered=True, hover=True,
                     size="sm", striped=True, className="mb-2",
                     style={"maxWidth": "560px", "fontVariantNumeric": "tabular-nums"})


def _fs(x_s: float) -> str:
    """Seconds as femtoseconds, for the clock-profile table."""
    return f"{x_s * 1e15:.0f} fs"


def _profile_section(rec: RunRecord):
    """The transmit clock's L(f), the loop's error response, and the jitter
    numbers that follow -- model beside measurement where a time run exists.

    Why the comparison is std(TIE) and the CDR tracking error, not calc_jitter's
    Rj: the decomposition files coloured low-frequency jitter under Pj, so its
    Rj is a few percent of the true sigma for a 1/f^2 clock
    (``cairn/engineering-pitfalls.md``). The integral of the profile is the
    number the time engine's total TIE should land on, and the loop model's
    sigma is what the sampler sees after the CDR.
    """
    from halo_serdes.cdr.linear import LoopParams, error_response
    from halo_serdes.tx.clock import ClockProfile

    cfg = rec.cfg
    try:
        prof = ClockProfile.load(cfg.tx.clock.file, f0_hz=cfg.tx.clock.f0_hz)
    except Exception as exc:  # the file went missing between config and render
        return dbc.Alert(f"clock profile could not be read: {exc}", color="warning")

    n_sym = max(int(cfg.sim.n_symbols), 2)
    f_lo = cfg.symbol_rate / n_sym
    f = np.geomspace(max(prof.f_hz[0], 1.0), prof.f_hz[-1], 400)
    l_dbc = 10.0 * np.log10(np.maximum(prof.s_phi(f) / 2.0, 1e-300))
    traces = [{"x": f, "y": l_dbc, "name": "profile L(f)", "mode": "lines",
               "color": theme.PRIMARY}]
    if prof.spurs:
        traces.append({"x": [s for s, _ in prof.spurs], "y": [d for _, d in prof.spurs],
                       "name": "spurs [dBc]", "mode": "markers", "color": theme.GOOD})

    sol = rec.stat.extras.get("clock_loop") if rec.stat is not None else None
    rows = [("profile RMS, 1/(N UI) .. f0/2", _fs(prof.rms_jitter_s(f_lo, prof.f0_hz / 2.0)))]
    if sol is not None:
        loop = LoopParams.from_config(cfg)
        e = error_response(f, loop, sol.k_pd)
        traces.append({"x": f, "y": l_dbc + 20.0 * np.log10(np.maximum(e, 1e-12)),
                       "name": "left on the sampler: L(f) + 20 log|1-H|", "mode": "lines",
                       "dash": "dash", "color": theme.INK})
        rows += [
            (f"CDR tracking bandwidth (k_pd {sol.k_pd:.3g})", f"{sol.bandwidth_hz / 1e6:.2f} MHz"),
            ("model: clock jitter left by the loop", _fs(sol.sigma_untracked_ui * cfg.ui)),
            ("model: loop's own (detector) noise", _fs(sol.sigma_self_ui * cfg.ui)),
            ("model: sampling-instant sigma", _fs(sol.sigma_ui * cfg.ui)),
        ]
    else:
        rows.append(("CDR model", "run the statistical engine (engine: stat or both)"))
    if rec.sim is not None and rec.sim.extras.get("phase_track") is not None:
        from halo_serdes.analysis.cdr_tracking import cdr_tracking_error_s
        err = cdr_tracking_error_s(cfg, rec.sim)
        if err.size:
            rows.append(("measured: CDR tracking error sigma (time engine)", _fs(float(np.std(err)))))
    jb = rec.sim.extras.get("jitter_budget") if rec.sim is not None else None
    if isinstance(jb, dict) and "tx" in jb and hasattr(jb["tx"], "tie"):
        rows.append(("measured: std(TIE) at the Tx stage", _fs(float(np.std(jb["tx"].tie)))))

    fig = figures.lines_fig(traces, title="Tx clock phase noise through the CDR",
                            xtitle="offset frequency [Hz]", ytitle="dBc/Hz")
    fig.update_xaxes(type="log")
    table = dbc.Table([html.Tbody([html.Tr([html.Td(k), html.Td(v)]) for k, v in rows])],
                      bordered=True, size="sm", className="mb-2",
                      style={"maxWidth": "560px", "fontVariantNumeric": "tabular-nums"})
    return html.Div([
        theme.section_title("Clock profile vs CDR"),
        html.Div(f"{cfg.tx.clock.file} — f0 {prof.f0_hz / 1e9:.3f} GHz"
                 + (f" — {prof.source}" if prof.source else ""),
                 style={"fontSize": "0.78rem", "color": theme.INK}),
        dbc.Row([dbc.Col(common.graph(fig), lg=7), dbc.Col(table, lg=5)], className="g-2"),
    ])


def render(rec: RunRecord):
    """Per-stage jitter budget, stacked bars and bathtub when the pattern
    repeats enough to decompose; the clock-profile section for a profile clock."""
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    blocks = [common.warnings_block(rec)]
    if rec.cfg.tx.clock.kind == "profile":
        blocks.append(_profile_section(rec))
    jb = rec.sim.extras.get("jitter_budget") if rec.sim is not None else None
    if not isinstance(jb, dict) or not jb or "_note" in jb:
        note = (jb.get("_note") if isinstance(jb, dict) else None) or \
            "no jitter budget"
        blocks.append(html.Div([
            dbc.Alert([html.Div("Jitter decomposition needs a repeating "
                       "pattern (≥4 periods)."),
                       html.Div(f"detail: {note}", style={"fontSize": "0.78rem"}),
                       html.Div("Set pattern to prbs7 and symbols ≥ ~1500, "
                                "then Run.", style={"fontSize": "0.78rem"})],
                      color="light", className="border")]))
        return html.Div(blocks)

    stage = "ctle" if "ctle" in jb else next(iter(jb))
    from halo_serdes.analysis.jitter import make_bathtub
    t, ber = make_bathtub(jb[stage], rec.cfg.ui)
    bath = figures.lines_fig([{"x": t / rec.cfg.ui, "y": np.maximum(ber, 1e-30),
                               "name": f"bathtub @ {stage}", "mode": "lines",
                               "color": theme.PRIMARY}],
                             title=f"Timing bathtub ({stage} stage)",
                             xtitle="phase [UI]", ytitle="BER", logy=True)
    blocks += [
        theme.section_title("Per-stage jitter budget"),
        _budget_table(jb, rec.cfg.ui),
        dbc.Row([
            dbc.Col(common.graph(figures.jitter_bar_fig(jb, rec.cfg.ui)), lg=7),
            dbc.Col(common.graph(bath), lg=5),
        ], className="g-2"),
    ]
    return html.Div(blocks)
