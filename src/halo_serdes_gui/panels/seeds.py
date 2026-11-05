"""Draw-spread tab — the time engine at several seeds against the statistical engine."""

from __future__ import annotations

import numpy as np
from dash import html

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "Draw spread"
TAB_ID = "seeds"
#: Shown with a Run button instead of computing on tab switch (app.py).
STUDY = ("The time engine at eight seeds against the statistical engine. With ADC "
         "lane mismatch every seed is another chip. Eight short time-domain runs.")


def render(rec: RunRecord):
    """Per-seed time-domain BER, their pooled count and the statistical read."""
    if rec is None:
        return common.need_run_message()
    if not rec.ok:
        return common.error_block(rec)
    d = studies.seeds_study(rec)
    seeds = d["seed"]
    floor = 0.5 / max(float(d["symbols"][0]) * max(rec.cfg.bits_per_symbol, 1), 1.0)
    per = np.where(d["errors"] > 0, d["ber"], floor)
    traces = [
        {"x": seeds, "y": per, "name": "time engine, one seed (no errors: on the floor)",
         "mode": "markers", "color": theme.PRIMARY},
        {"x": seeds, "y": d["pooled"], "name": "time engine, all seeds pooled",
         "mode": "lines", "color": theme.INK},
    ]
    if np.all(np.isfinite(d["stat"])):
        traces.append({"x": seeds, "y": d["stat"], "name": "statistical engine (over draws)",
                       "mode": "lines", "dash": "dash", "color": theme.MUTED})
    fig = figures.lines_fig(traces, title="One link, eight draws", xtitle="seed",
                            ytitle="BER", logy=True, height=420)
    n_err = int(np.sum(d["errors"]))
    return html.Div([
        common.graph(fig),
        html.Div(f"{int(d['symbols'][0]):,} symbols a seed, {n_err} errors in all. The seed sets "
                 "the noise and every per-link draw — ADC lane offset, gain and skew, "
                 "comparator offsets — so with mismatch each point is one chip and they can "
                 "spread over decades. The statistical engine takes those draws as white noise "
                 "and reads their average: compare it with the pooled line, not with one seed. "
                 "Eight chips are still a small sample — on the TI-mismatch preset an eight-seed "
                 "pool lands anywhere from a fifth of to twice what 128 seeds pool to.",
                 style={"fontSize": "0.75rem", "color": "#5b6472"}),
    ])
