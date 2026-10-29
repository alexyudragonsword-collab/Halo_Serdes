"""Shared panel helpers: result cards, warnings/error blocks, BER formatting."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import dcc, html

from .. import theme
from ..runner import RunRecord


def sci(x: float, digits: int = 2) -> str:
    """A rate the way every card writes it: ``2.4e-4``, ``1.1e-5``, ``0``.

    One spelling for BER, SER and the bounds, so ``< 1e-05`` beside
    ``0.00e+00`` (two ways of saying "no errors" on one row) cannot recur."""
    if x == 0.0:
        return "0"
    mant, exp = f"{x:.{max(digits - 1, 0)}e}".split("e")
    return f"{mant}e{int(exp)}"


def rate_text(rate: float, n: int) -> tuple[str, str]:
    """(display, tone) for an error rate over ``n`` trials; none counted is
    the upper bound ``< 1/n``, not a zero."""
    if n and rate == 0.0:
        return f"< {sci(1.0 / n)}", "good"
    tone = "good" if rate < 1e-4 else "warn" if rate < 1e-2 else "crit"
    return sci(rate), tone


def ber_text(ber: float, n: int) -> tuple[str, str]:
    """(display, tone) for a BER value; 0 errors -> confidence upper bound."""
    return rate_text(ber, n)


def ensure_stat(rec: RunRecord):
    """Compute the (cheap, analytic) statistical result on demand and cache it.

    A failure is recorded on the record rather than swallowed. The statistical
    result is half of invariant #3 -- the statistical/time-domain cross-check --
    so "the statistical engine raised" and "the statistical engine was never
    run" must not render the same way.
    """
    if rec.stat is not None or rec.stat_error is not None:
        return rec.stat
    try:
        from halo_serdes.channel import ChannelModel
        from halo_serdes.engine.statistical import run_statistical
        from halo_serdes_app.runner import stat_equaliser
        cm = ChannelModel.from_config(rec.cfg)
        taps, pre = stat_equaliser(rec.cfg, cm, rec.sim)
        rec.stat = run_statistical(rec.cfg, channel=cm, ffe_taps=taps, ffe_pre=pre)
    except Exception as exc:
        rec.stat = None
        rec.stat_error = f"{type(exc).__name__}: {exc}"
    return rec.stat


# --- verdict ----------------------------------------------------------------

def fec_target(cfg) -> tuple[str, float]:
    """(name, pre-FEC BER threshold) the verdict is judged against: KR4
    RS(528,514) for NRZ, KP4 RS(544,514) for PAM, each at post-FEC 1e-15."""
    from halo_serdes.fec import fec_threshold
    code = "kr4" if cfg.modulation == "nrz" else "kp4"
    return f"{code.upper()} RS({528 if code == 'kr4' else 544},514)", fec_threshold(code)


def _engine_call(ber: float, n: int, errors: int, thr: float) -> tuple[str, str]:
    """(call, why) for one engine: ``pass`` / ``fail`` / ``open``. A time run
    with no errors passes only if its bound 1/n is already below ``thr``."""
    if n and errors == 0:
        bound = 1.0 / n
        if bound < thr:
            return "pass", f"0 errors in {n:,} bits (< {sci(bound)})"
        return "open", (f"0 errors in {n:,} bits only bounds the BER at {sci(bound)}, "
                        f"above the threshold -- run more symbols")
    call = "pass" if ber < thr else "fail"
    return call, f"{sci(ber)}" + (f" ({errors:,} errors)" if n else "")


def verdict(rec: RunRecord) -> dict:
    """Pass / fail against the FEC threshold, from whichever engines ran.

    Each engine gets its own call; the verdict is theirs when they agree,
    ``disagree`` when one passes and the other fails, ``open`` when neither
    can say. ``ratio`` is time BER / StatEye BER when both are measured
    numbers (the time run counted errors), else None."""
    name, thr = fec_target(rec.cfg)
    calls, ratio = {}, None
    sim, stat = rec.sim, rec.stat
    if sim is not None:
        calls["time"] = _engine_call(sim.ber.ber, sim.ber.n_checked,
                                     sim.ber.n_errors, thr)
    if stat is not None:
        calls["stat"] = _engine_call(stat.ber, 0, 0, thr)
    if sim is not None and stat is not None and sim.ber.n_errors > 0 and stat.ber > 0:
        ratio = sim.ber.ber / stat.ber
    said = {c for c, _ in calls.values()} - {"open"}
    overall = ("open" if not said else "disagree" if len(said) > 1 else said.pop())
    best = None
    if sim is not None and sim.ber.n_errors > 0:
        best = sim.ber.ber
    elif stat is not None:
        best = stat.ber
    return {"code": name, "threshold": thr, "calls": calls, "overall": overall,
            "ratio": ratio, "best": best,
            "errors": sim.ber.n_errors if sim is not None else None}


# --- warnings ---------------------------------------------------------------

#: Warnings the numerics raise in passing (an overflow inside a fit, a
#: covariance that could not be estimated): worth keeping, not worth a banner.
_NUMERICAL = ("overflow encountered", "invalid value encountered",
              "divide by zero encountered", "Covariance of the parameters",
              "Mean of empty slice", "Degrees of freedom")


def split_warnings(rec: RunRecord) -> tuple[list[str], list[str]]:
    """(link, numerical) warnings of a run, each once, in order raised.

    The mixed-signal envelope warning is dropped: the sidebar banner already
    states it for the configuration in the form, and the stale-result banner
    says when the form has moved on from the run."""
    link, num = [], []
    for w in dict.fromkeys(rec.warnings or ()):
        if w.startswith("mixed_signal arch "):
            continue
        (num if any(k in w for k in _NUMERICAL) else link).append(w)
    return link, num


def error_block(rec: RunRecord):
    """The run's error with its traceback (collapsed)."""
    return html.Div([
        dbc.Alert(rec.error, color="danger", className="mb-2"),
        dbc.Collapse(html.Pre(rec.tb or "", style={"fontSize": "0.72rem",
                     "whiteSpace": "pre-wrap"}),
                     id="err-tb-collapse", is_open=False),
    ])


def warnings_block(rec: RunRecord):
    """The run's warnings, shown once above the tabs: one banner per link
    warning, the numerical ones folded into a single collapsed line."""
    if rec is None or not rec.ok:
        return None
    link, num = split_warnings(rec)
    if not (link or num):
        return None
    body = [theme.banner("warn", w) for w in link]
    if num:
        body.append(html.Details([
            html.Summary(f"{len(num)} numerical warning{'s' if len(num) > 1 else ''} "
                         "from the solvers", style={"cursor": "pointer"}),
            html.Ul([html.Li(w) for w in num], className="mb-0"),
        ], style={"fontSize": "0.78rem", "color": theme.MUTED,
                  "marginBottom": "0.4rem"}))
    return html.Div(body)


def need_run_message():
    """The empty state: what to do before this tab has anything to show."""
    return dbc.Alert("Load a preset (or edit the config) and press "
                     "▶ Run to populate this tab.", color="light",
                     className="border")


def graph(fig, **kw):
    """A ``dcc.Graph`` with its container pinned to the figure's height."""
    # Pin the graph's container to the figure's own height. Without a fixed
    # container height, Plotly's responsive resize and the auto-height parent
    # feed back on each other inside a webview and the chart grows without
    # bound. Fixed height breaks that loop; width stays responsive.
    height = 360
    try:
        if getattr(fig, "layout", None) is not None and fig.layout.height:
            height = int(fig.layout.height)
    except Exception:
        pass
    style = kw.pop("style", {})
    style = {"height": f"{height}px", **style}
    return dcc.Graph(figure=fig, style=style,
                     config={"displaylogo": False, "responsive": True,
                             "toImageButtonOptions": {"format": "png", "scale": 2}},
                     **kw)


def cards_row(cards):
    """Metric cards on one row, skipping any that are None."""
    return dbc.Row([c for c in cards if c is not None], className="g-0 mb-1")
