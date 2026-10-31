"""Field bounds as data: what ``__post_init__`` rejects, what a form marks.

The simple per-field bounds live in ``schema.FIELD_BOUNDS`` and the checks
read them (ROADMAP P2 #41), so the form's range and the configuration's
rejection cannot drift apart: every entry is enforced at its edge, every
numeric form field it covers carries the range, and ``derive`` names the
form's own path for a value outside it."""

import dataclasses
import json

import pytest

from halo_serdes.config import schema
from halo_serdes.config.schema import FIELD_BOUNDS, bound_text, field_bound, within_bound
from halo_serdes_app import api
from halo_serdes_app.config_bridge import (
    FIELD_BY_PATH, SECTIONS, config_to_values, load_preset, preset_names, range_errors,
)


def _instance(cls_name: str):
    """A valid instance to vary one field of; the optical blocks are only
    checked when there is an optical path, so take the LPO preset's."""
    if cls_name == "OpticalConfig":
        lpo = next(n for n in preset_names() if "LPO" in n)
        return load_preset(lpo).topology.optical
    if cls_name == "LinkConfig":
        return schema.LinkConfig()
    return getattr(schema, cls_name)()


#: the other field a bound's own check needs set (rules across two fields stay in code)
_CONTEXT = {"MlsdConfig.state_memory": {"kind": "viterbi"},
            "TxConfig.drv_compression": {"drv_nl": "curve"}}


def _edges(bound):
    """(a value just outside, the edge or just inside) for each finite end."""
    _, lo, hi, lo_open, hi_open, _ = bound
    out = []
    eps = 1e-6
    if lo is not None:
        out.append((lo - 1 if not lo_open else lo, lo + eps if lo_open else lo))
    if hi is not None:
        out.append((hi + 1 if not hi_open else hi, hi - eps if hi_open else hi))
    return out


@pytest.mark.parametrize("key", sorted(FIELD_BOUNDS))
def test_every_bound_is_what_the_check_enforces(key):
    cls_name, name = key.split(".")
    base = dataclasses.replace(_instance(cls_name), **_CONTEXT.get(key, {}))
    bound = FIELD_BOUNDS[key]
    is_int = isinstance(getattr(base, name), int) or name.endswith(("bits", "_symbols", "n_freq"))
    for bad, good in _edges(bound):
        if is_int and (bad != int(bad) or good != int(good)):
            continue
        with pytest.raises(ValueError, match=f"must be {bound_text(bound)}".replace("(", r"\(")
                           .replace(")", r"\)").replace("[", r"\[").replace("]", r"\]")):
            dataclasses.replace(base, **{name: bad})
        if not (cls_name == "TxConfig" and name == "fir_n_pre" and good > 0):
            dataclasses.replace(base, **{name: good})          # the edge itself is allowed


def test_the_form_carries_the_ranges_the_config_enforces():
    """Every numeric form field a bound covers has the range, in the form's
    units (symbol rate in GBd: '> 0', not '> 0 Baud')."""
    covered = 0
    for _, _, fields in SECTIONS:
        for f in fields:
            b = field_bound(f["path"])
            if f["kind"] in ("float", "int", "opt_float", "opt_int") and b is not None:
                assert f["range"]["text"], f["path"]
                covered += 1
            else:
                assert "range" not in f, f["path"]
    assert covered > 60
    assert FIELD_BY_PATH["symbol_rate"]["range"]["text"] == "> 0"
    assert FIELD_BY_PATH["rx.adc.cal.fg_ref"]["range"] == {
        "min": 0.0, "max": 1.0, "min_open": True, "max_open": True, "text": "in (0, 1)"}


@pytest.mark.parametrize("name", preset_names())
def test_no_preset_is_out_of_range(name):
    assert range_errors(config_to_values(load_preset(name))) == {}


def test_derive_names_the_form_path_of_a_value_out_of_range():
    """The config's own message names the dataclass field ("adc.n_bits");
    derive marks the form's path, so both clients highlight the input."""
    values = config_to_values(load_preset(next(n for n in preset_names() if "224G" in n)))
    values.update({"tx.swing": -1.0, "rx.adc.n_bits": 0, "symbol_rate": 0})
    data = json.loads(api.call("derive", json.dumps({"values": values})))["data"]
    assert data["valid"] is False
    assert set(data["field_errors"]) == {"tx.swing", "rx.adc.n_bits", "symbol_rate"}
    assert data["field_errors"]["tx.swing"].endswith("must be > 0")
    assert "hidden" in data


def test_a_hidden_field_is_not_marked():
    """The optical blocks are not checked without an optical path, and a
    form does not mark what it hides."""
    values = config_to_values(load_preset(next(n for n in preset_names() if "16G" in n)))
    path = "topology.optical.rin_db_hz"
    assert path in FIELD_BY_PATH
    values[path] = 3.0
    assert path not in range_errors(values)


def test_within_bound_reads_open_and_closed_ends():
    b = ("x", 0, 1, True, False, "")
    assert not within_bound(b, 0) and within_bound(b, 1) and within_bound(b, None)
    assert bound_text(b) == "in (0, 1]"
    assert bound_text(("x", None, 0, False, True, " dB/Hz")) == "< 0 dB/Hz"


def test_the_desktop_form_marks_a_row_out_of_range():
    """The desktop GUI: closed bounds as the input's min / max, the range in
    the label's hover text, and the row marked with the range under it."""
    pytest.importorskip("dash")
    from halo_serdes_gui.app import build_app
    from halo_serdes_gui.config_form import ROW_BAD, ROW_CLASS, _control, _label

    ctl = _control(FIELD_BY_PATH["rx.adc.n_bits"], 8)
    assert ctl.min == 1 and getattr(ctl, "max", None) is None
    assert getattr(_control(FIELD_BY_PATH["tx.swing"], 1.0), "min", None) is None   # > 0: open
    assert "Range: > 0" in _label(FIELD_BY_PATH["tx.swing"]).title
    fns = {getattr(s.get("callback"), "__wrapped__", None).__name__: s["callback"].__wrapped__
           for s in build_app().callback_map.values()
           if getattr(s.get("callback"), "__wrapped__", None) is not None}
    values = config_to_values(load_preset(next(n for n in preset_names() if "16G" in n)))
    values["tx.swing"] = 0.0
    ids = [{"type": "cfg", "path": p} for p in values]
    rows = [{"type": "cfg-row", "path": p} for p in values]
    classes, hints = fns["_ranges"](list(values.values()), ids, rows)
    at = {r["path"]: (c, h) for r, c, h in zip(rows, classes, hints)}
    assert at["tx.swing"][0] == ROW_BAD and at["tx.swing"][1].endswith("must be > 0")
    assert at["tx.noise_rms"] == (ROW_CLASS, "")
