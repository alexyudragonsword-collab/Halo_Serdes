"""Dynamic configuration form generated from ``config_bridge.SECTIONS``.

Every input carries a pattern-matching id ``{"type": "cfg", "path": <dotted>}``
so a single callback can read the whole form back with
``State({"type": "cfg", "path": ALL}, ...)`` and reconstruct a ``LinkConfig``.
"""

from __future__ import annotations

from typing import Any

import dash_bootstrap_components as dbc
from dash import html

from . import theme
from .config_bridge import SECTIONS

_NUMERIC = {"float", "int", "opt_float", "opt_int"}


def _cid(path: str) -> dict:
    return {"type": "cfg", "path": path}


def _control(field: dict, value: Any):
    kind = field["kind"]
    cid = _cid(field["path"])
    if kind == "bool":
        return dbc.Switch(id=cid, value=bool(value), style={"marginTop": "0.3rem"})
    if kind == "enum":
        opts = [{"label": o, "value": o} for o in field["options"]]
        return dbc.Select(id=cid, options=opts, value=value, size="sm")
    if kind in _NUMERIC:
        return dbc.Input(id=cid, type="number", value=value, size="sm",
                         step=1 if kind in ("int", "opt_int") else "any",
                         debounce=True)
    # text-like: str, opt_str, tuple_float, opt_tuple_float
    return dbc.Input(id=cid, type="text",
                     value="" if value is None else str(value), size="sm",
                     debounce=True)


def _field_row(field: dict, value: Any):
    return dbc.Row([
        dbc.Col(html.Label(field["label"],
                           style={"fontSize": "0.78rem", "color": theme.INK}),
                width=6, className="d-flex align-items-center"),
        dbc.Col(_control(field, value), width=6),
    ], className="g-1 mb-1")


def render_form(values: dict[str, Any], open_sections=("link", "channel",
                "tx", "rx", "sim")):
    """Return an Accordion with one item per section."""
    items = []
    for sid, title, fields in SECTIONS:
        rows = [_field_row(f, values.get(f["path"])) for f in fields]
        items.append(dbc.AccordionItem(rows, title=title, item_id=sid))
    return dbc.Accordion(items, active_item=list(open_sections),
                         always_open=True, flush=True)


def values_from_states(ids: list[dict], vals: list[Any]) -> dict[str, Any]:
    """Aligned ALL-pattern (ids, values) -> {path: value}."""
    return {i["path"]: v for i, v in zip(ids, vals) if isinstance(i, dict)}
