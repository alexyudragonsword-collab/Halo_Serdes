"""Run history: the runs this session made, shown as a strip above the tabs.

Each entry is a small JSON dict kept in a ``dcc.Store`` -- the record id, a
label, a one-line result, the form values it ran with (so its configuration can
be put back into the form) and whether it is pinned. The record itself stays in
the runner's registry; pinning an entry pins the record there too, so the
registry's cap (24 records) cannot evict it. Unpinned entries past
``MAX_UNPINNED`` drop off the strip, oldest first.
"""

from __future__ import annotations

import time
from functools import lru_cache

import dash_bootstrap_components as dbc
from dash import html

from halo_serdes_app import runner

from . import config_bridge as cb
from . import theme
from .panels import common

MAX_UNPINNED = 8


@lru_cache(maxsize=32)
def _preset_values(name: str) -> dict:
    """A preset's form values (cached; presets do not change while running)."""
    return cb.config_to_values(cb.load_preset(name))


def _as_config(path: str, value):
    """A form value as the config sees it. The browser hands back what the
    widgets hold -- "" for a blank optional, the text of a tuple -- so form and
    preset values are compared after the same coercion ``build_config`` does."""
    field = cb.FIELD_BY_PATH.get(path)
    try:
        return cb.coerce_in(field, value) if field else value
    except (TypeError, ValueError):
        return value


def label(values: dict, preset: str | None) -> str:
    """The preset's name, with how many fields the run changed from it."""
    if not preset or preset not in cb.preset_names():
        return "custom"
    base = _preset_values(preset)
    changed = sum(1 for p, v in values.items()
                  if p in base and _as_config(p, base[p]) != _as_config(p, v))
    return preset if not changed else f"{preset} · {changed} changed"


def summary(rec) -> str:
    """One line for the strip: the run's BER, or that it failed."""
    if rec is None:
        return "gone"
    if not rec.ok:
        return "failed"
    if rec.sim is not None:
        return "BER " + common.rate_text(rec.sim.ber.ber, rec.sim.ber.n_checked)[0]
    if rec.stat is not None:
        return "StatEye " + common.sci(rec.stat.ber)
    return "—"


def entry(rec, values: dict, preset: str | None, history: list | None) -> dict:
    """The history entry for a finished run."""
    seq = 1 + max((e["seq"] for e in history or ()), default=0)
    return {"rid": rec.id, "seq": seq, "label": label(values, preset), "preset": preset,
            "summary": summary(rec), "ok": rec.ok,
            "time": time.strftime("%H:%M:%S"), "values": values, "pinned": False}


def add(history: list | None, new: dict) -> list:
    """``history`` with ``new`` first, unpinned entries past the cap dropped."""
    out, unpinned = [], 0
    for e in [new] + list(history or ()):
        if not e["pinned"]:
            unpinned += 1
            if unpinned > MAX_UNPINNED:
                continue
        out.append(e)
    return out


def toggle_pin(history: list | None, rid: str) -> list:
    """``history`` with ``rid``'s pin flipped, in the registry too."""
    out = []
    for e in history or ():
        if e["rid"] == rid:
            e = {**e, "pinned": not e["pinned"]}
            runner.pin(rid, e["pinned"])
        out.append(e)
    return out


def find(history: list | None, rid: str | None) -> dict | None:
    """The entry for ``rid``, or None."""
    return next((e for e in history or () if e["rid"] == rid), None)


def strip(history: list | None, current: str | None):
    """The strip above the tabs: one button per run (the one on screen
    highlighted) with its pin, and a button that puts the shown run's
    configuration back into the form."""
    live = [e for e in history or () if runner.get(e["rid"]) is not None]
    if not live:
        return None
    items = []
    for e in live:
        on_screen = e["rid"] == current
        items.append(dbc.ButtonGroup([
            dbc.Button(f"#{e['seq']} {e['label']} · {e['summary']}",
                       id={"type": "hist-view", "rid": e["rid"]}, size="sm",
                       color="primary" if on_screen else "light",
                       className="border", title=f"run at {e['time']}"),
            dbc.Button("★" if e["pinned"] else "☆",
                       id={"type": "hist-pin", "rid": e["rid"]}, size="sm",
                       color="light", className="border",
                       title="unpin" if e["pinned"] else "pin: keep this run"),
        ], className="me-1 mb-1"))
    tools = []
    if find(live, current) is not None:
        tools.append(dbc.Button("↺ Load this run's config", id="hist-load", size="sm",
                                color="link", className="mb-1 p-0 ms-1"))
    return html.Div([
        html.Span("Runs", style={"fontSize": "0.72rem", "fontWeight": 700,
                                 "textTransform": "uppercase", "color": theme.MUTED,
                                 "marginRight": "0.5rem"}),
        *items, *tools,
    ], className="d-flex flex-wrap align-items-center mb-1")
