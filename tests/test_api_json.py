"""The JSON facade contract — what a non-Python UI codes against.

These tests exist because the Android client cannot be tested here: they pin
the wire format so a change that would break it fails in CI instead of on a
device. Everything asserted is a property the client depends on — the envelope
shape, JSON-decodability (no NaN/Infinity leaking through), handles instead of
arrays, and failures arriving as data rather than exceptions.
"""

import json

import pytest

from halo_serdes_app import api


def call(method, **payload):
    """Invoke the facade exactly as Kotlin does: strings in, strings out."""
    raw = api.call(method, json.dumps(payload))
    assert isinstance(raw, str)
    return json.loads(raw)          # strict: rejects NaN/Infinity


@pytest.fixture(scope="module")
def values():
    r = call("preset", name="NRZ 28G analytic (COM/xtalk)")
    assert r["ok"], r
    return r["data"]["values"]


# ------------------------------------------------------------- envelope ---

def test_every_method_is_reachable_and_shaped():
    assert set(api.methods()) == {
        "schema", "preset", "derive", "to_yaml", "from_yaml",
        "run_stat", "run_com", "study", "series", "release"}


def test_unknown_method_is_data_not_an_exception():
    r = call("no_such_method")
    assert r["ok"] is False
    assert "no_such_method" in r["error"]["message"]
    assert r["v"] == api.API_VERSION


def test_malformed_payload_is_data_not_an_exception():
    r = json.loads(api.call("derive", "{not json"))
    assert r["ok"] is False and r["error"]["message"]


# --------------------------------------------------------------- config ---

def test_schema_drives_a_generated_form(values):
    d = call("schema")["data"]
    assert d["presets"] and d["api_version"] == api.API_VERSION
    kinds = set()
    paths = set()
    for _sid, title, fields in d["sections"]:
        assert title
        for f in fields:
            assert {"path", "label", "kind"} <= set(f)
            kinds.add(f["kind"])
            paths.add(f["path"])
    # a client must handle exactly these widget kinds
    assert kinds <= {"float", "int", "bool", "enum", "str", "opt_str",
                     "opt_float", "opt_int", "tuple_float", "opt_tuple_float"}
    # every field the schema advertises is present in a preset's values
    assert paths == set(values)


def test_enum_fields_carry_their_options():
    for _sid, _title, fields in call("schema")["data"]["sections"]:
        for f in fields:
            if f["kind"] == "enum":
                assert f.get("options"), f


@pytest.mark.parametrize("name", api.preset_names())
def test_every_preset_round_trips_through_yaml(name):
    vals = call("preset", name=name)["data"]["values"]
    text = call("to_yaml", values=vals)["data"]["text"]
    back = call("from_yaml", text=text)["data"]["values"]
    assert back == vals


def test_derive_reports_envelope_and_derived_quantities(values):
    d = call("derive", values=values)["data"]
    assert d["valid"] and d["field_errors"] == {}
    assert d["envelope"]["level"] in {"ok", "warn", "crit"}
    assert {"UI", "dt", "Nyquist"} <= set(d["derived"])


def test_derive_pins_a_bad_field_to_its_path(values):
    """The client marks the offending input, so the error must name the path."""
    d = call("derive", values={**values, "osr": "0"})["data"]
    assert d["valid"] is False
    assert "osr" in d["field_errors"]
    assert "osr" in d["field_errors"]["osr"]


# -------------------------------------------------------------- compute ---

def test_run_stat_returns_scalars_and_a_handle(values):
    r = call("run_stat", values=values)
    assert r["ok"], r
    d = r["data"]
    assert isinstance(d["handle"], str) and d["handle"]
    assert 0.0 <= d["ber"] <= 1.0 and 0.0 <= d["ser"] <= 1.0
    assert len(d["bathtub"]["x"]) == len(d["bathtub"]["y"])
    assert r["ms"] >= 0


def test_no_numpy_array_crosses_the_boundary(values):
    """Everything must already be plain JSON — that is the whole constraint."""
    r = call("run_stat", values=values)
    json.dumps(r, allow_nan=False)      # would raise on numpy or NaN


def test_eye_heatmap_is_decimated_before_transport(values):
    h = call("run_stat", values=values)["data"]["handle"]
    d = call("series", handle=h, key="stat_eye")["data"]
    assert d["kind"] == "heatmap"
    assert d["rows"] <= api.MAX_HEATMAP_ROWS
    assert d["cols"] <= api.MAX_HEATMAP_COLS
    assert len(d["z"]) == d["rows"] and len(d["z"][0]) == d["cols"]
    # log scale, clamped — a raw PDF would be all zeros to a chart
    assert all(v <= 0.0 for row in d["z"] for v in row)


def test_run_com_is_all_scalars(values):
    d = call("run_com", values=values)["data"]
    for k in ("com_db", "a_signal", "a_noise", "fom_db", "fom_isi",
              "fom_xtalk", "fom_noise", "fom_jitter"):
        assert isinstance(d[k], (int, float)), (k, d[k])
    assert d["summary"]


@pytest.mark.parametrize("name", ["fec", "reach", "com", "crosstalk", "multilane"])
def test_studies_return_flat_series(values, name):
    r = call("study", name=name, values=values)
    assert r["ok"], r
    d = r["data"]
    assert d["name"] == name
    if d.get("note"):                    # unsupported config: information, not failure
        return
    for key, series in d["data"].items():
        assert isinstance(series, (list, float, int, type(None))), (key, series)


def test_unsupported_study_config_is_a_note_not_an_error(values):
    """A reach sweep needs an analytic channel; a touchstone config must come
    back as an explanatory note so the UI shows a card, not a red error."""
    r = call("study", name="reach",
             values={**values, "channel.kind": "touchstone",
                     "channel.file": "data/channels/peters_01_0605_T20_thru.s4p"})
    assert r["ok"], r
    assert r["data"].get("note")


def test_unknown_study_and_handle_fail_as_data(values):
    assert call("study", name="nope", values=values)["ok"] is False
    assert call("series", handle="deadbeef", key="bathtub")["ok"] is False


def test_engine_failure_is_reported_as_a_config_error(values):
    r = call("run_stat", values={**values, "osr": "-4"})
    assert r["ok"] is False
    assert r["error"]["kind"] == "config"
    assert "osr" in r["error"]["message"]


def test_release_frees_the_handle(values):
    h = call("run_stat", values=values)["data"]["handle"]
    assert call("release", handle=h)["data"]["released"] == h
    assert call("series", handle=h, key="bathtub")["ok"] is False


# ------------------------------------------------------------ decimation ---

def test_decimate_preserves_extremes():
    import numpy as np

    y = np.zeros(10_000)
    y[1234] = 5.0            # a lone spike must survive thinning
    y[8765] = -3.0
    out = api._decimate(y, 256)
    assert out.size <= 256
    assert out.max() == pytest.approx(5.0)
    assert out.min() == pytest.approx(-3.0)


def test_heatmap_reduction_uses_max_not_mean():
    import numpy as np

    z = np.zeros((1024, 64))
    z[500, 30] = 1.0         # a rare-but-decisive skirt sample
    out = api._reduce_heatmap(z, 128, 32)
    assert out.shape == (128, 32)
    assert out.max() == pytest.approx(1.0)   # a mean would have buried it
