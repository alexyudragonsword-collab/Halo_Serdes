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
    """Pinned deliberately: the method set is the client's whole vocabulary.

    Adding one is a decision, and this test is where it gets made rather than
    noticed later. (Removing or renaming one is a breaking change and should
    bump ``API_VERSION``.)
    """
    assert set(api.methods()) == {
        "schema", "preset", "derive", "to_yaml", "from_yaml",
        "run_stat", "run_com", "study", "series", "result", "release",
        "start_time_run", "poll", "cancel", "import_touchstone"}


def test_schema_advertises_every_study_with_a_plot_spec():
    """A study the client cannot discover is a study nobody runs.

    Both halves are pinned: the names must match `_STUDIES` (plus the
    config-independent `fec`), and each must carry the panel spec that says
    which key is the x axis and which axes are logarithmic. A study with a
    label but no spec would render as a guess.
    """
    from halo_serdes_app import studies

    advertised = call("schema")["data"]["studies"]
    names = [s["name"] for s in advertised]
    assert set(names) == set(api._STUDIES) | {"fec"}
    assert len(names) == len(set(names))
    for s in advertised:
        assert s["title"] and s["blurb"], s["name"]
        # Which studies cost a time-domain run is the difference between 0.4 s
        # and minutes. A client that does not know cannot offer to reuse a run
        # the user already made, and starts a second one inside a call that
        # reports no progress and cannot be cancelled.
        assert s["needs_time"] == (s["name"] in api._TIME_STUDIES)
        assert s["plots"] == studies.STUDY_PLOTS[s["name"]]
        for panel in s["plots"]:
            assert panel["x"] and panel["y"], s["name"]


def _spec_matches_data(name, values):
    r = call("study", name=name, values=values)
    assert r["ok"], (name, r)
    d = r["data"]
    if d.get("note"):
        return False          # study declined this config
    assert d["plots"], f"{name}: ran but advertised no panels"
    for panel in d["plots"]:
        for key in [panel["x"]] + panel["y"]:
            assert key in d["data"], (
                f"{name}: spec names {key!r}, data has {sorted(d['data'])}")
    return True


def test_every_plot_spec_names_keys_the_study_actually_returns(values):
    """The spec is only useful if its keys exist in the data.

    A renamed array in a study would otherwise leave the panel silently empty
    — a chart that draws nothing looks the same as a link with nothing to
    show.
    """
    from halo_serdes_app import studies

    reached = {n for n in studies.STUDY_PLOTS if _spec_matches_data(n, values)}
    # Whatever this config could not reach is checked by the ADC test below.
    # Naming the gap rather than silently skipping it is the point: the
    # fixedpoint spec named `bits`/`ser` against a study returning
    # `wl`/`mismatch`, and a bare `continue` here is why nothing said so.
    assert reached, "no study produced data on the default config"


def test_fixedpoint_spec_is_checked_on_a_config_that_can_run_it():
    """The one study every other config declines.

    It needs an ADC run (`rx.arch = adc_dsp`) with a time-domain record, so on
    the default fixture it returns a note and contributes nothing to the check
    above. That exemption hid a wrong spec, so it gets its own config here.
    """
    from halo_serdes_app import studies

    vals = call("preset", name="PAM4 224G ADC (106 GBd)")["data"]["values"]
    vals["sim.n_symbols"] = "4000"          # enough to replay, quick enough to test
    assert _spec_matches_data("fixedpoint", vals), \
        "fixedpoint still declined a config built for it"
    # And the rest, on this config too — the specs must not be config-specific.
    for name in studies.STUDY_PLOTS:
        _spec_matches_data(name, vals)


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


def test_derive_flags_an_unreachable_channel_without_invalidating_the_config():
    """A missing ``.s4p`` is an availability fact, not a validation error.

    The Android build ships the YAML presets but not the 4.4 MB of channel
    files, so a client has to be able to tell which presets it can offer
    *before* running an engine. Discovering it by catching a FileNotFoundError
    from the engine reads to a user as a broken preset rather than as an absent
    file — which is exactly how the first on-device run misread itself.
    """
    from halo_serdes_app.config_bridge import config_to_values, load_preset

    vals = config_to_values(load_preset("NRZ 16G mixed-signal"))
    assert call("derive", values=vals)["data"]["channel"]["ok"] is True

    gone = {**vals, "channel.file": "data/channels/not_bundled.s4p"}
    d = call("derive", values=gone)["data"]
    assert d["valid"] is True                    # the config itself is fine
    assert d["channel"]["ok"] is False
    assert "not_bundled.s4p" in d["channel"]["message"]
    # ...and the flag must actually predict the failure it is warning about
    assert call("run_stat", values=gone)["ok"] is False


def test_bool_fields_must_not_be_sent_as_text():
    """The coercion is asymmetric, and a client has to know it.

    Every numeric kind accepts a string (``float(value)``), which is what lets
    a text-entry form send everything as text. ``bool`` does not: ``bool("false")``
    is True, so a switch serialised as text would silently invert and produce a
    config nobody asked for. The Android form keeps booleans typed; this pins the
    reason it has to.
    """
    from halo_serdes_app.config_bridge import coerce_in

    assert coerce_in({"kind": "bool"}, "false") is True     # the trap
    assert coerce_in({"kind": "bool"}, False) is False
    # ...while the kinds a form does send as text round-trip properly
    assert coerce_in({"kind": "float", "scale": 1e9}, "28") == 28e9
    assert coerce_in({"kind": "int"}, "32") == 32
    assert coerce_in({"kind": "opt_float"}, "") is None


def test_analytic_channels_need_no_file(values):
    d = call("derive", values={**values, "channel.kind": "analytic"})["data"]
    assert d["channel"] == {"ok": True, "message": ""}


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


def test_eye_heatmap_range_describes_the_data_it_ships():
    """The colour range must match the array, and the floor must be nameable.

    It previously declared a fixed -12..0. Neither end was real — a typical eye
    tops out near -2.7, so the top third of any ramp went unused, and about a
    third of the cells sit at the -18 clamp, below the stated minimum. A client
    colouring by that range drew a washed-out picture and implied the floor
    cells held a measured value, when they mean the opposite: no probability
    resolved on this grid.
    """
    r = call("preset", name="NRZ 28G analytic (COM/xtalk)")["data"]["values"]
    h = call("run_stat", values=r)["data"]["handle"]
    d = call("series", handle=h, key="stat_eye")["data"]

    assert d["floor"] == api.EYE_LOG_FLOOR
    flat = [v for row in d["z"] for v in row]
    assert min(flat) >= d["floor"]                       # nothing below the clamp
    assert d["zmax"] == max(flat)                        # top of the ramp is reachable
    above = [v for v in flat if v > d["floor"]]
    assert d["zmin"] == min(above)                       # bottom excludes the floor
    assert d["zmin"] > d["floor"]


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


# ------------------------------------------------- long-running jobs (M7) ---

def _await_job(job, timeout=120.0):
    """Poll to completion, returning the last poll payload."""
    import time as _t
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        p = call("poll", job=job)["data"]
        if p["state"] in ("done", "error", "cancelled"):
            return p
        _t.sleep(0.02)
    raise AssertionError(f"job {job} did not finish within {timeout}s")


def test_time_run_reports_stages_and_yields_a_handle(values):
    r = call("start_time_run", values=values, quality="fast")
    assert r["ok"], r
    assert r["data"]["n_symbols"] == api.QUALITY_SYMBOLS["fast"]

    p = _await_job(r["data"]["job"])
    assert p["state"] == "done", p
    assert p["stage"] == "done"
    assert p["elapsed_s"] > 0
    # the handle is a normal result handle: series/release work on it
    assert call("series", handle=p["handle"], key="y_slicer",
                max_points=32)["ok"]
    assert call("release", handle=p["handle"])["ok"]


def test_result_reports_the_error_count_behind_a_time_domain_ber(values):
    """A counted BER without its error count is not readable.

    At the fast tier a comfortable link produces zero errors, and `ber` is then
    0.0 — which reads as "perfect" when it means "below what 20 000 symbols can
    measure". `ber_is_upper_bound` and the raw counts are what let a client say
    which of the two it is. (This project has already drawn a wrong conclusion
    from a difference computed on four errors; see engineering-pitfalls.)
    """
    job = call("start_time_run", values=values, quality="fast")["data"]["job"]
    p = _await_job(job)
    r = call("result", handle=p["handle"])
    assert r["ok"], r
    sim = r["data"]["sim"]
    assert sim["n_requested"] == api.QUALITY_SYMBOLS["fast"]
    # The engine drops warm-up and trailing partials, so what it measured is
    # strictly less than what the tier asked for. Both are reported because a
    # UI that shows only the tier would overstate the run.
    assert 0 < sim["n_symbols"] < sim["n_requested"]
    assert sim["n_checked"] > 0
    assert sim["ber_is_upper_bound"] == (sim["n_errors"] == 0)
    # whatever `series` keys it advertises must actually be fetchable
    for key in r["data"]["series"]:
        assert call("series", handle=p["handle"], key=key,
                    max_points=16)["ok"], key
    call("release", handle=p["handle"])


def test_result_on_a_statistical_handle_carries_no_sim_half(values):
    h = call("run_stat", values=values)["data"]["handle"]
    d = call("result", handle=h)["data"]
    assert "stat" in d and "sim" not in d
    assert set(d["series"]) == {"bathtub", "stat_eye"}
    assert call("result", handle="nope")["ok"] is False
    call("release", handle=h)


def test_only_one_run_at_a_time(values):
    first = call("start_time_run", values=values, quality="fast")["data"]["job"]
    try:
        second = call("start_time_run", values=values, quality="fast")
        assert second["ok"] is False
        assert "already in progress" in second["error"]["message"]
    finally:
        call("cancel", job=first)
        _await_job(first)


def test_cancel_is_distinguishable_from_failure(values):
    """A cancelled run must not be reported as a broken one.

    ``run_link`` turns every exception into a record error so a UI never sees a
    crash — which swallowed the cancellation on the first attempt and reported
    it as ``error``. ``runner.Cancelled`` is now re-raised past that handler,
    and this pins both halves of the distinction.
    """
    job = call("start_time_run", values=values, quality="precise")["data"]["job"]
    call("cancel", job=job)
    assert _await_job(job)["state"] == "cancelled"

    bad = call("start_time_run", values={**values, "osr": "0"},
               quality="fast")["data"]["job"]
    p = _await_job(bad)
    assert p["state"] == "error"
    assert "osr" in p["job_error"]["message"]


def test_unknown_job_and_quality_are_data(values):
    assert call("poll", job="nope")["ok"] is False
    assert call("cancel", job="nope")["ok"] is False
    r = call("start_time_run", values=values, quality="turbo")
    assert r["ok"] is False and "turbo" in r["error"]["message"]


# --------------------------------------------- touchstone import (M8) ---

def test_touchstone_import_reports_the_file_span_not_just_a_loss(values):
    """The span is the number that keeps someone honest.

    A measured `.s4p` routinely stops below the Nyquist it gets pointed at, and
    the channel model extrapolates past it without complaint — producing a
    plot and a BER that look like data. ``extrapolated`` is what tells a client
    the difference.
    """
    import glob

    path = sorted(glob.glob("data/channels/*.s4p"))[0]
    d = call("import_touchstone", path=path, values=values)["data"]
    assert d["file_f_max_ghz"] > 0
    assert d["extrapolated"] is (d["nyquist_ghz"] > d["file_f_max_ghz"])

    # the same file against a Nyquist it does not cover
    fast = call("preset", name="PAM4 224G ADC (106 GBd)")["data"]["values"]
    d2 = call("import_touchstone", path=path, values=fast)["data"]
    assert d2["extrapolated"] is True
    # -inf dB must arrive as null, never as a value or a JSON error
    assert d2["il_db_at_nyquist"] is None


def test_bad_touchstone_paths_are_data(values, tmp_path):
    assert call("import_touchstone", values=values)["ok"] is False
    assert call("import_touchstone", path="/no/such.s4p", values=values)["ok"] is False
    junk = tmp_path / "junk.s4p"
    junk.write_text("not a touchstone")
    assert call("import_touchstone", path=str(junk), values=values)["ok"] is False


# ------------------------------------------------------- study plots (M9) ---

def test_every_study_plot_names_keys_the_study_actually_returns(values):
    """Axis metadata has to match the data it describes.

    The studies return a flat ``{name: array}`` with nothing marking the x
    axis, so without this each client would guess — and two clients would
    guess differently. ``STUDY_PLOTS`` is the single answer; this checks it
    stays true as the studies change, rather than pointing at a key that was
    renamed.
    """
    from halo_serdes_app import studies

    handle = call("run_stat", values=values)["data"]["handle"]
    checked = 0
    for name in sorted(studies.STUDY_PLOTS):
        r = call("study", name=name, values=values, handle=handle)
        assert r["ok"], r
        d = r["data"]
        if d.get("note"):
            continue            # unsupported for this config; nothing to plot
        assert d["plots"], f"{name} produced data but no plot spec"
        for panel in d["plots"]:
            keys = set(d["data"])
            assert panel["x"] in keys, f"{name}: x key {panel['x']} not in {keys}"
            n = len(d["data"][panel["x"]])
            for y in panel["y"]:
                assert y in keys, f"{name}: y key {y} not in {keys}"
                assert len(d["data"][y]) == n, f"{name}: {y} length != x length"
            assert panel["x_label"] and panel["y_label"]
            checked += 1
    assert checked >= 5, f"only {checked} panels exercised"


def test_a_log_axis_is_only_declared_where_the_data_allows_it(values):
    """A log axis needs strictly positive data, or the chart draws nothing.

    The client takes ``y_log`` at face value; if a study can emit a zero on an
    axis declared logarithmic, that is a spec bug here rather than a rendering
    bug there.
    """
    from halo_serdes_app import studies

    handle = call("run_stat", values=values)["data"]["handle"]
    for name in sorted(studies.STUDY_PLOTS):
        d = call("study", name=name, values=values, handle=handle)["data"]
        if d.get("note"):
            continue
        for panel in d["plots"]:
            axes = ([panel["x"]] if panel.get("x_log") else []) + \
                   (panel["y"] if panel.get("y_log") else [])
            for key in axes:
                vals = [v for v in d["data"][key] if v is not None]
                assert vals and all(v > 0 for v in vals), \
                    f"{name}.{key} is declared logarithmic but contains {min(vals)}"
