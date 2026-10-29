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
from .field_meta import HELP, field_shown, section_shown

_NUMERIC = {"float", "int", "opt_float", "opt_int"}


def _cid(path: str) -> dict:
    """The pattern-matching id every form input carries."""
    return {"type": "cfg", "path": path}


def _control(field: dict, value: Any):
    """The input widget for one field, by its kind: switch, select, optional
    select (blank = None), number, or free text for the tuple/str kinds."""
    kind = field["kind"]
    cid = _cid(field["path"])
    if kind == "bool":
        return dbc.Switch(id=cid, value=bool(value), style={"marginTop": "0.3rem"})
    if kind == "enum":
        opts = [{"label": o, "value": o} for o in field["options"]]
        return dbc.Select(id=cid, options=opts, value=value, size="sm")
    if kind == "opt_enum":
        # Blank is a legitimate choice (-> None). A value loaded from YAML
        # that is not among the shipped options still has to be displayed,
        # so it is appended rather than silently replaced by blank.
        options = list(field["options"])
        if value not in (None, "") and value not in options:
            options.append(str(value))
        opts = [{"label": "(none)", "value": ""}] + [{"label": o, "value": o} for o in options]
        return dbc.Select(id=cid, options=opts, value="" if value is None else str(value),
                          size="sm")
    if kind in _NUMERIC:
        return dbc.Input(id=cid, type="number", value=value, size="sm",
                         step=1 if kind in ("int", "opt_int") else "any",
                         debounce=True)
    # text-like: str, opt_str, tuple_float, opt_tuple_float
    return dbc.Input(id=cid, type="text",
                     value="" if value is None else str(value), size="sm",
                     debounce=True)


_HIDDEN = {"display": "none"}


def _label(field: dict):
    """The field's label; one with help text carries it as a hover title and
    a dotted underline that says there is something to hover for."""
    style = {"fontSize": "0.78rem", "color": theme.INK}
    tip = HELP.get(field["path"])
    if not tip:
        return html.Label(field["label"], style=style)
    return html.Label(field["label"], title=tip,
                      style={**style, "cursor": "help",
                             "textDecoration": "underline dotted #9aa5b4",
                             "textUnderlineOffset": "3px"})


def _field_row(field: dict, value: Any, shown: bool = True):
    """Label and control on one row."""
    return dbc.Row([
        dbc.Col(_label(field), width=6, className="d-flex align-items-center"),
        dbc.Col(_control(field, value), width=6),
    ], id={"type": "cfg-row", "path": field["path"]},
        className="g-1 mb-1", style=None if shown else _HIDDEN)


def render_form(values: dict[str, Any], open_sections=("link", "channel",
                "tx", "rx", "sim")):
    """Return an Accordion with one item per section, showing only what the
    configuration in ``values`` uses (``field_meta``)."""
    items = []
    for sid, title, fields in SECTIONS:
        rows = [_field_row(f, values.get(f["path"]), field_shown(f["path"], values))
                for f in fields]
        items.append(dbc.AccordionItem(
            rows, title=title, item_id=sid, id={"type": "cfg-sec", "sid": sid},
            style=None if section_shown(sid, values) else _HIDDEN))
    return dbc.Accordion(items, active_item=list(open_sections),
                         always_open=True, flush=True)


def visibility(values: dict[str, Any], row_ids: list[dict],
               sec_ids: list[dict]) -> tuple[list, list]:
    """Styles for the form's rows and sections, in the order of their ids:
    hidden where the configuration in ``values`` does not use them."""
    rows = [None if field_shown(i["path"], values) else _HIDDEN for i in row_ids]
    secs = [None if section_shown(i["sid"], values) else _HIDDEN for i in sec_ids]
    return rows, secs


def values_from_states(ids: list[dict], vals: list[Any]) -> dict[str, Any]:
    """Aligned ALL-pattern (ids, values) -> {path: value}."""
    return {i["path"]: v for i, v in zip(ids, vals) if isinstance(i, dict)}
