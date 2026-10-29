"""The desktop form's view of the shared form metadata.

What a form says about a field beyond its label -- help text, the common
fields, the link stages and the show-when rules -- lives in
``halo_serdes_app.form_meta``, which the phone app reads too (through the
``schema`` and ``derive`` calls). This module re-exports it under the names
the desktop form uses, and adds what only the desktop draws: the one-line
summary under each stage in the chain, and the field that is not in the form
(``rx.arch`` is the top bar's switch).
"""

from __future__ import annotations

from typing import Any

from halo_serdes_app import form_meta as _shared
from halo_serdes_app.form_meta import COMMON, HELP, STAGES  # noqa: F401

Values = dict[str, Any]

#: The rules by name (data, see ``form_meta``); kept under the names the
#: desktop used when they were callables here.
FIELD_SHOWN = _shared.SHOW_IF
SECTION_SHOWN = _shared.SECTION_SHOW_IF
STAGE_SHOWN = _shared.STAGE_SHOW_IF
STAGE_OF_SECTION = {sec: sid for sid, _, secs in STAGES for sec in secs}

#: Paths outside every stage's form (shown elsewhere on the page).
NOT_IN_FORM = {"rx.arch"}

field_shown = _shared.field_visible
section_shown = _shared.section_visible
stage_shown = _shared.stage_visible


def _num(v, fmt="{:g}") -> str:
    try:
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return "?"


def stage_summary(sid: str, v: Values) -> str:
    """One line under a stage in the chain: what it is set to now."""
    if sid == "link":
        return (f"{str(v.get('modulation', '')).upper()} {_num(v.get('symbol_rate'))} GBd · "
                f"{v.get('sim.engine', '')} · {_num(v.get('sim.n_symbols'), '{:,.0f}')} sym")
    if sid == "tx":
        taps = [t for t in str(v.get("tx.fir_taps") or "").split(",") if t.strip()]
        return f"FIR {len(taps)} taps · {_num(v.get('tx.swing'))} V · {v.get('tx.clock.kind')}"
    if sid == "channel":
        if v.get("channel.kind") == "touchstone":
            base = str(v.get("channel.file") or "no file").replace("\\", "/").split("/")[-1]
        else:
            base = f"RLGC {_num(v.get('channel.length_m'))} m"
        if v.get("topology.optical.kind") not in (None, "", "none"):
            base += f" · {v.get('topology.optical.kind')}"
        n_x = sum(int(_num(v.get(p), "{:.0f}")) for p in ("crosstalk.n_fext", "crosstalk.n_next")
                  if _num(v.get(p)) != "?")
        return base + (f" · {n_x} aggressors" if n_x else "")
    if sid == "fe":
        ctle = (f"CTLE {_num(v.get('rx.ctle.peak_db'))} dB" if v.get("rx.ctle.enable")
                else "no CTLE")
        return f"{ctle} · noise {_num(v.get('rx.noise_rms'))} V"
    if sid == "adc":
        enob = v.get("rx.adc.enob")
        enob = "ideal" if enob in (None, "") else _num(enob)
        return (f"{_num(v.get('rx.adc.n_bits'))} b · ENOB {enob} · "
                f"{_num(v.get('rx.adc.n_lanes'))} lanes")
    if sid == "eq":
        out = (f"FFE {_num(v.get('rx.ffe.n_pre'))}+{_num(v.get('rx.ffe.n_post'))} · "
               f"DFE {_num(v.get('rx.dfe.n_taps'))}")
        if v.get("rx.mlsd.kind") not in (None, "", "none"):
            out += f" · {v.get('rx.mlsd.kind')}"
        return out
    if sid == "cdr":
        return (f"{v.get('rx.cdr.kind', '')} · kp 2^-{_num(v.get('rx.cdr.kp_shift'))} · "
                f"ki 2^-{_num(v.get('rx.cdr.ki_shift'))}")
    return ""
