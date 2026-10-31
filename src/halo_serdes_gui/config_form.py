"""Dynamic configuration form generated from ``config_bridge.SECTIONS``.

Every input carries a pattern-matching id ``{"type": "cfg", "path": <dotted>}``
so a single callback can read the whole form back with
``State({"type": "cfg", "path": ALL}, ...)`` and reconstruct a ``LinkConfig``.

The form is laid out by link stage (``field_meta.STAGES``): every stage's
fields are in the page, one stage's are shown, and within it the common ones
unless "Advanced" is open. Hiding never removes an input, so the ALL-pattern
read-back always sees the whole configuration.
"""

from __future__ import annotations

from typing import Any

import dash_bootstrap_components as dbc
from dash import html

from . import theme
from .config_bridge import SECTIONS
from .field_meta import (
    COMMON, HELP, NOT_IN_FORM, STAGES, field_shown, section_shown,
)

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
        # closed bounds as the browser's own min / max; the open ones (> 0)
        # only through the row's mark (``range_marks``)
        rng = field.get("range") or {}
        lim = {k: rng[k] for k in ("min", "max")
               if rng.get(k) is not None and not rng.get(f"{k}_open")}
        return dbc.Input(id=cid, type="number", value=value, size="sm",
                         step=1 if kind in ("int", "opt_int") else "any",
                         debounce=True, **lim)
    # text-like: str, opt_str, tuple_float, opt_tuple_float
    return dbc.Input(id=cid, type="text",
                     value="" if value is None else str(value), size="sm",
                     debounce=True)


_HIDDEN = {"display": "none"}


def _label(field: dict):
    """The field's label; one with help text carries it as a hover title and
    a dotted underline that says there is something to hover for."""
    style = {"fontSize": "0.78rem", "color": theme.INK}
    tip = HELP.get(field["path"], "")
    if field.get("range"):
        tip = (tip + "\n" if tip else "") + f"Range: {field['range']['text']}"
    if not tip:
        return html.Label(field["label"], style=style)
    return html.Label(field["label"], title=tip,
                      style={**style, "cursor": "help",
                             "textDecoration": "underline dotted #9aa5b4",
                             "textUnderlineOffset": "3px"})


ROW_CLASS = "g-1 mb-1"
ROW_BAD = ROW_CLASS + " cfg-out-of-range"


def _field_row(field: dict, value: Any, shown: bool = True):
    """Label and control on one row; the control's hint under it, filled
    when the value is outside the field's range (``range_marks``)."""
    return dbc.Row([
        dbc.Col(_label(field), width=6, className="d-flex align-items-center"),
        dbc.Col([_control(field, value),
                 html.Div(id={"type": "cfg-hint", "path": field["path"]},
                          className="cfg-hint")], width=6),
    ], id={"type": "cfg-row", "path": field["path"]},
        className=ROW_CLASS, style=None if shown else _HIDDEN)


_SECTION_TITLE = {sid: title for sid, title, _ in SECTIONS}
_SECTION_FIELDS = {sid: [f["path"] for f in fields] for sid, _, fields in SECTIONS}


def _row_shown(path: str, values: dict[str, Any], advanced: bool) -> bool:
    return field_shown(path, values) and (advanced or path in COMMON)


def _section_shown(sid: str, values: dict[str, Any], advanced: bool) -> bool:
    """A section shows when the configuration uses it and at least one of its
    rows is on screen (a stage's all-advanced section waits for Advanced)."""
    return section_shown(sid, values) and any(
        _row_shown(p, values, advanced) for p in _SECTION_FIELDS[sid] if p not in NOT_IN_FORM)


def advanced_count(stage: str, values: dict[str, Any]) -> int:
    """How many fields of ``stage`` the configuration uses beyond the common ones."""
    secs = next(s for sid, _, s in STAGES if sid == stage)
    return sum(1 for sec in secs if section_shown(sec, values)
               for p in _SECTION_FIELDS[sec]
               if p not in NOT_IN_FORM and p not in COMMON and field_shown(p, values))


def render_form(values: dict[str, Any], stage: str = "link", advanced: bool = False):
    """Every stage's fields, grouped by section; only ``stage`` shown, and in
    it the fields the configuration uses (``field_meta``)."""
    by_sec = {sid: fields for sid, _, fields in SECTIONS}
    blocks = []
    for sid, _title, secs in STAGES:
        parts = []
        for sec in secs:
            rows = [_field_row(f, values.get(f["path"]),
                               _row_shown(f["path"], values, advanced))
                    for f in by_sec[sec] if f["path"] not in NOT_IN_FORM]
            parts.append(html.Div([theme.section_title(_SECTION_TITLE[sec]), *rows],
                                  id={"type": "cfg-sec", "sid": sec},
                                  style=None if _section_shown(sec, values, advanced)
                                  else _HIDDEN))
        blocks.append(html.Div(parts, id={"type": "cfg-stage", "sid": sid},
                               style=None if sid == stage else _HIDDEN))
    return html.Div(blocks)


def visibility(values: dict[str, Any], row_ids: list[dict], sec_ids: list[dict],
               stage_ids: list[dict] | None = None, stage: str = "link",
               advanced: bool = False) -> tuple[list, list, list]:
    """Styles for the form's rows, sections and stage blocks, in the order of
    their ids: the chosen stage, and in it what the configuration uses."""
    rows = [None if _row_shown(i["path"], values, advanced) else _HIDDEN for i in row_ids]
    secs = [None if _section_shown(i["sid"], values, advanced) else _HIDDEN
            for i in sec_ids]
    stages = [None if i["sid"] == stage else _HIDDEN for i in stage_ids or ()]
    return rows, secs, stages


def arch_control(value: Any):
    """``rx.arch`` as the top bar's two-way switch: the same pattern id as every
    form input, so the read-back finds it wherever it sits."""
    return dbc.RadioItems(
        id=_cid("rx.arch"), value=value or "mixed_signal", inline=True,
        options=[{"label": "Mixed-signal", "value": "mixed_signal"},
                 {"label": "ADC + DSP", "value": "adc_dsp"}],
        className="btn-group", inputClassName="btn-check",
        labelClassName="btn btn-outline-primary btn-sm",
        labelCheckedClassName="active")


def values_from_states(ids: list[dict], vals: list[Any]) -> dict[str, Any]:
    """Aligned ALL-pattern (ids, values) -> {path: value}."""
    return {i["path"]: v for i, v in zip(ids, vals) if isinstance(i, dict)}
