"""Compare workspace — runs of this session side by side.

Pick two or more runs from the history: their StatEye phase bathtubs on one
axis, a metric table (BER, SER, SNR, StatEye, verdict, time / StatEye ratio,
run time), and the configuration fields that differ between them. The app
wires the pick list to the history store; ``render`` alone (no history) only
says what to do.

"Run on the other architecture" puts the run on screen through the other
receiver with every other field unchanged -- the like-for-like comparison of
invariant 2, as far as the form's ADC / FFE settings make it one.
"""

from __future__ import annotations

import numpy as np
import dash_bootstrap_components as dbc
from dash import html

from .. import config_bridge as cb
from .. import figures, theme
from ..runner import RunRecord
from . import common

TITLE = "Compare"
TAB_ID = "compare"

#: Config differences listed at most (two runs of different presets differ in
#: dozens of fields; the table is for reading, not for diffing YAML).
MAX_DIFF_ROWS = 25

_VERDICT = {"pass": "pass", "fail": "fail", "disagree": "engines disagree",
            "open": "not decided"}


def render(rec: RunRecord):
    """Without the history there is nothing to compare."""
    if rec is None:
        return common.need_run_message()
    return dbc.Alert("Pick two or more runs from the history to compare them.",
                     color="light", className="border")


def picker(history: list | None, selected: list | None, current: str | None):
    """The pick list and the other-architecture button; the body fills in
    from the selection (``body``)."""
    entries = [e for e in history or () if e.get("ok")]
    if not entries:
        return common.need_run_message()
    options = [{"label": f" #{e['seq']} {e['label']} · {e['summary']}", "value": e["rid"]}
               for e in entries]
    return html.Div([
        html.Div([
            dbc.Checklist(id="cmp-pick", options=options, value=list(selected or ()),
                          inline=True, className="mb-1",
                          style={"fontSize": "0.82rem"}),
            dbc.Button("⇄ Run the shown run on the other architecture", id="cmp-other",
                       size="sm", color="primary", outline=True, className="mb-2",
                       disabled=current is None,
                       title="Same configuration with rx.arch flipped; the form's "
                             "ADC / FFE settings apply"),
        ]),
        html.Div(id="cmp-body"),
    ])


def _changed(entries: list[dict]) -> list[tuple[str, list]]:
    """(label, value per run) for the fields whose values differ."""
    out = []
    for path, field in cb.FIELD_BY_PATH.items():
        vals = []
        for e in entries:
            v = e["values"].get(path)
            try:
                v = cb.coerce_in(field, v)
            except (TypeError, ValueError):
                pass
            vals.append(v)
        if any(v != vals[0] for v in vals[1:]):
            shown = [e["values"].get(path) for e in entries]
            out.append((field["label"], ["—" if v in (None, "") else str(v) for v in shown]))
    return out


def _metrics(rec) -> list[str]:
    """One run's column of the metric table."""
    common.ensure_stat(rec)
    v = common.verdict(rec)
    sim, stat = rec.sim, rec.stat
    return [
        "mixed-signal" if rec.cfg.rx.arch == "mixed_signal" else "ADC + DSP",
        "+".join(rec.engines),
        common.rate_text(sim.ber.ber, sim.ber.n_checked)[0] if sim is not None else "—",
        f"{sim.ber.n_errors:,}" if sim is not None else "—",
        common.rate_text(sim.ser, sim.n_symbols)[0] if sim is not None else "—",
        f"{sim.slicer_snr_db:.1f} dB" if sim is not None else "—",
        common.sci(stat.ber) if stat is not None else "—",
        f"{v['ratio']:.2f}×" if v["ratio"] is not None else "—",
        f"{_VERDICT[v['overall']]} ({v['code'].split()[0]})",
        f"{rec.elapsed_s:.1f} s",
    ]


_METRIC_ROWS = ["Receiver", "Engines", "pre-FEC BER", "Errors counted", "SER",
                "Slicer SNR", "StatEye BER", "Time / StatEye", "Verdict", "Run time"]


def _table(head: list[str], rows: list[tuple[str, list]]):
    return dbc.Table([
        html.Thead(html.Tr([html.Th("")] + [html.Th(h) for h in head])),
        html.Tbody([html.Tr([html.Td(k, style={"color": theme.MUTED})]
                            + [html.Td(c) for c in cells]) for k, cells in rows]),
    ], bordered=False, hover=True, size="sm", className="mb-2",
        style={"fontSize": "0.82rem", "fontVariantNumeric": "tabular-nums"})


def body(entries: list[dict], recs: list):
    """Bathtubs, metrics and config differences of the picked runs."""
    pairs = [(e, r) for e, r in zip(entries, recs) if r is not None and r.ok]
    if len(pairs) < 2:
        return dbc.Alert("Pick at least two runs.", color="light", className="border")
    head = [f"#{e['seq']} {e['label']}" for e, _ in pairs]
    traces = []
    for (e, r), color in zip(pairs, theme.COLORWAY * 3):
        common.ensure_stat(r)
        if r.stat is not None:
            traces.append({"x": r.stat.phi_ui, "y": np.maximum(r.stat.ber_phi, 1e-30),
                           "name": f"#{e['seq']} {e['label']}", "mode": "lines",
                           "color": color})
    fig = (figures.lines_fig(traces, title="StatEye phase bathtubs", xtitle="sampling phase [UI]",
                             ytitle="BER", logy=True, height=380)
           if traces else figures.placeholder("no statistical result to plot"))
    metrics = list(zip(_METRIC_ROWS, zip(*[_metrics(r) for _, r in pairs])))
    diffs = _changed([e for e, _ in pairs])
    more = len(diffs) - MAX_DIFF_ROWS
    return html.Div([
        common.graph(fig),
        theme.section_title("Results"),
        _table(head, [(k, list(v)) for k, v in metrics]),
        theme.section_title(f"Configuration differences ({len(diffs)})"),
        _table(head, diffs[:MAX_DIFF_ROWS]) if diffs else html.Div(
            "Same configuration.", style={"fontSize": "0.82rem", "color": theme.MUTED}),
        html.Div(f"… and {more} more" if more > 0 else "",
                 style={"fontSize": "0.75rem", "color": theme.MUTED}),
    ])
