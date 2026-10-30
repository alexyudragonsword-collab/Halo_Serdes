"""The form metadata both clients share (``halo_serdes_app.form_meta``).

Help text, common fields, link stages and show-when rules used to live in the
desktop GUI only (``halo_serdes_gui/field_meta.py``, rules as Python
lambdas); the phone drew every field of every section. They are now data in
the shared schema, and ``derive`` evaluates the rules for either client, so
there is one evaluator. These tests hold that data to the config schema and
the two clients to one answer.
"""

from __future__ import annotations

import json

import pytest

from halo_serdes_app import api, form_meta
from halo_serdes_app.config_bridge import (
    ALL_PATHS, FIELD_BY_PATH, SECTIONS, config_to_values, load_preset, preset_names,
)

NRZ = "NRZ 28G analytic (COM/xtalk)"
ADC = "PAM4 224G ADC (106 GBd)"
LPO = "PAM4 100G/λ LPO (VCSEL + OM4)"


def _call(method, **payload):
    out = json.loads(api.call(method, json.dumps(payload)))
    assert out["ok"], out
    return out["data"]


def _paths(rule):
    if not rule:
        return set()
    if "all" in rule or "any" in rule:
        return set().union(*(_paths(r) for r in rule.get("all", rule.get("any"))))
    return {rule["path"]}


def test_the_metadata_names_real_fields_and_covers_every_section_once():
    for path in list(form_meta.HELP) + list(form_meta.COMMON) + list(form_meta.SHOW_IF):
        assert path in FIELD_BY_PATH, path
    rules = ([form_meta.field_rule(p) for p in ALL_PATHS]
             + list(form_meta.SECTION_SHOW_IF.values())
             + list(form_meta.STAGE_SHOW_IF.values()))
    for rule in rules:
        assert _paths(rule) <= set(ALL_PATHS), rule
    covered = [sec for _, _, secs in form_meta.STAGES for sec in secs]
    assert sorted(covered) == sorted(sid for sid, _, _ in SECTIONS)


def test_the_schema_carries_it():
    data = _call("schema")
    fields = [f for _, _, fs in data["sections"] for f in fs]
    assert all(isinstance(f["help"], str) and isinstance(f["common"], bool) for f in fields)
    assert {f["stage"] for f in fields} == {sid for sid, _, _ in form_meta.STAGES}
    by = {f["path"]: f for f in fields}
    assert by["channel.r_skin"]["help"] and by["channel.r_skin"]["common"]
    assert by["channel.r_skin"]["show_if"] == {"path": "channel.kind", "in": ["analytic"]}
    assert "show_if" not in by["modulation"]
    assert [s["id"] for s in data["stages"]] == [sid for sid, _, _ in form_meta.STAGES]
    assert data["section_show_if"]["adc"] == form_meta.ADC


def test_rules_survive_json_and_read_phone_values():
    """A rule is the same rule after the JSON trip, and a phone's values
    (strings, "" for blank) give the answer the desktop's do."""
    for path in ALL_PATHS:
        rule = form_meta.field_rule(path)
        assert json.loads(json.dumps(rule)) == rule
    desktop = config_to_values(load_preset(LPO))
    phone = {p: ("" if v is None else v if isinstance(v, bool) else str(v))
             for p, v in desktop.items()}
    assert form_meta.hidden(desktop, SECTIONS) == form_meta.hidden(phone, SECTIONS)
    assert form_meta.holds(form_meta.OPTICAL, {"topology.optical.kind": ""}) is False
    rule = form_meta.SECTION_SHOW_IF["pr"]
    assert form_meta.holds(rule, {"rx.arch": "mixed_signal", "pr.target": "1.0, 0.5"})
    assert not form_meta.holds(rule, {"rx.arch": "mixed_signal", "pr.target": "1.0"})
    assert form_meta.holds(rule, {"rx.arch": "mixed_signal", "pr.target": [1.0, 0.5]})
    with pytest.raises(ValueError):
        form_meta.holds({"path": "x", "between": [0, 1]}, {})


@pytest.mark.parametrize("name", [NRZ, ADC, LPO])
def test_derive_says_what_the_form_hides(name):
    values = config_to_values(load_preset(name))
    hidden = _call("derive", values=values)["hidden"]
    adc = values["rx.arch"] == "adc_dsp"
    assert ("adc" in hidden["sections"]) is not adc
    assert ("adc" in hidden["stages"]) is not adc
    optics = values["topology.optical.kind"] not in ("none", None, "")
    assert ("topology.optical.oma_dbm" in hidden["fields"]) is not optics
    assert "topology.optical.kind" not in hidden["fields"]
    # and an invalid form is still a form on screen: hidden comes back too
    bad = _call("derive", values={**values, "osr": 0})
    assert bad["valid"] is False and bad["hidden"] == hidden


def test_the_desktop_reads_the_same_rules():
    pytest.importorskip("dash")
    from halo_serdes_gui import field_meta
    for name in preset_names()[1:]:
        values = config_to_values(load_preset(name))
        hidden = set(form_meta.hidden(values, SECTIONS)["fields"])
        assert hidden == {p for p in ALL_PATHS if not field_meta.field_shown(p, values)}
