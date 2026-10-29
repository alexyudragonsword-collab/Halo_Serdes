"""The GUI's phase-1 run model, checked through its callbacks.

Run used to be one synchronous callback: the page froze for the length of
the run, with no progress and no way out. It now starts a background job --
the same ``api.start_job`` the phone reaches through ``start_time_run`` -- and
an interval polls it. Each finished run joins a history strip; a run can be
put back on screen, pinned past the result registry's cap, or have its
configuration loaded back into the form.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("dash")
pytest.importorskip("yaml")

from test_gui_error_surfaces import _text  # noqa: E402
from test_gui_panels import run_through_job  # noqa: E402

from halo_serdes_app import api, runner  # noqa: E402
from halo_serdes_gui import config_bridge as cb  # noqa: E402
from halo_serdes_gui import history as hist  # noqa: E402
from halo_serdes_gui.app import build_app  # noqa: E402

NRZ = "NRZ 28G analytic (COM/xtalk)"


@pytest.fixture(scope="module")
def callbacks():
    out = {}
    for spec in build_app().callback_map.values():
        fn = getattr(spec.get("callback"), "__wrapped__", None)
        if fn is not None:
            out[fn.__name__] = fn
    return out


def _form(**over):
    vals = {**cb.config_to_values(cb.load_preset(NRZ)), "sim.n_symbols": 20000, **over}
    ids = [{"type": "cfg", "path": p} for p in cb.ALL_PATHS]
    return ids, [vals.get(i["path"]) for i in ids]


def _wait(job, timeout=600):
    deadline = time.monotonic() + timeout
    while job.active and time.monotonic() < deadline:
        time.sleep(0.05)
    return job


# ------------------------------------------------------------ job runner ---

def test_one_job_runner_for_both_clients():
    """``start_job`` runs what it is given; a second start while one runs is
    refused with the running job's id, and the phone's wire is unchanged
    (a RuntimeError naming it)."""
    vals = cb.config_to_values(cb.load_preset(NRZ))
    job = api.start_job({**vals, "sim.n_symbols": 2_000_000})
    try:
        with pytest.raises(api.Busy) as exc:
            api.start_job(vals)
        assert exc.value.job == job.id and api.active_job() is job
        import json
        wire = json.loads(api.call("start_time_run", json.dumps({"values": vals})))
        assert wire["error"]["kind"] == "RuntimeError"
        assert "already in progress" in wire["error"]["message"]
    finally:
        api.cancel_job(job.id)
        _wait(job)
    assert job.state == "cancelled" and api.active_job() is None


def test_a_failed_run_keeps_its_record_but_not_a_handle():
    """The phone reads ``handle`` only on success; the desktop shows a failed
    run's error from its ``record``."""
    vals = {**cb.config_to_values(cb.load_preset(NRZ)), "symbol_rate": -1}
    job = _wait(api.start_job(vals))
    assert job.state == "error" and job.handle is None
    rec = runner.get(job.record)
    assert rec is not None and "symbol_rate must be > 0" in rec.error


def test_pinned_records_outlive_the_registry_cap():
    from halo_serdes.config import LinkConfig
    first = runner.RunRecord(id="pinned-one", cfg=LinkConfig(), engines=())
    runner._register(first)
    runner.pin(first.id)
    try:
        for i in range(runner._MAX_KEEP + 5):
            runner._register(runner.RunRecord(id=f"filler-{i}", cfg=LinkConfig(),
                                              engines=()))
        assert runner.get(first.id) is first
        assert runner.get("filler-0") is None          # unpinned ones still go
    finally:
        runner.pin(first.id, False)


# ------------------------------------------------------------- the page ---

def test_run_shows_progress_then_the_result_and_a_history_entry(callbacks):
    ids, vals = _form(**{"rx.ctle.peak_db": 9.0})
    ref, out = run_through_job(callbacks, vals, ids, "single", preset=NRZ)
    rid, content, ran_with, _warns, history, poll_off, run_off, cancel_style, status = out
    assert poll_off is True and run_off is False and cancel_style == {"display": "none"}
    assert status is None and "pre-FEC BER" in _text(content)
    assert ran_with["rx.ctle.peak_db"] == 9.0
    (e,) = history
    assert e["rid"] == rid and e["seq"] == 1 and e["label"] == f"{NRZ} · 2 changed"   # + sim.n_symbols
    assert e["summary"].startswith("BER ")
    strip = _text(callbacks["_strip"](history, rid))
    assert f"#1 {NRZ} · 2 changed" in strip and "Load this run's config" in strip


def test_the_progress_bar_names_the_stage_and_cancel_stops_the_run(callbacks):
    ids, vals = _form(**{"sim.n_symbols": 3_000_000})
    ref, *_ = callbacks["_start"](1, vals, ids, NRZ)
    job = api.get_job(ref["id"])
    deadline = time.monotonic() + 120
    while job.fraction is None and job.active and time.monotonic() < deadline:
        time.sleep(0.05)
    running = callbacks["_poll"](1, ref)
    from dash import no_update
    assert all(o is no_update for o in running[:4])     # nothing handed on mid-run
    assert "time-domain engine" in _text(running[-1])
    assert "Cancelling" in _text(callbacks["_cancel"](1, ref))
    _wait(job)
    out = callbacks["_poll"](1, ref)
    assert out[0] is no_update                          # nothing to render: the
    assert "Run cancelled" in _text(out[-1])            # previous result stays
    assert out[1] is True and out[2] is False           # polling off, Run back on


def test_a_second_run_follows_the_one_already_going(callbacks):
    """A reload or a second browser tab presses Run while a job runs: it is
    followed, not refused."""
    ids, vals = _form(**{"sim.n_symbols": 3_000_000})
    first, *_ = callbacks["_start"](1, vals, ids, NRZ)
    try:
        second, *_ = callbacks["_start"](1, vals, ids, NRZ)
        assert second["id"] == first["id"]
        # ... but not one that is being cancelled: that would answer a press
        # of Run with "cancelled"
        api.cancel_job(first["id"])
        third, poll_off, run_off, _, status = callbacks["_start"](1, vals, ids, NRZ)
        if api.get_job(first["id"]).active:
            from dash import no_update
            assert third is no_update and poll_off is True and run_off is False
            assert "still stopping" in _text(status)
    finally:
        _wait(api.cancel_job(first["id"]))


def test_history_view_pin_and_load(callbacks, monkeypatch):
    ids, vals = _form()
    _, out_a = run_through_job(callbacks, vals, ids, "channel", preset=NRZ)
    rid_a, history = out_a[0], out_a[4]
    ids, vals_b = _form(**{"channel.length_m": 0.32})
    _, out_b = run_through_job(callbacks, vals_b, ids, "channel", preset=NRZ,
                               history=history)
    rid_b, history = out_b[0], out_b[4]
    assert [e["seq"] for e in history] == [2, 1]

    # put run A back on screen: its tab, its values for the stale banner
    monkeypatch.setattr("halo_serdes_gui.app.ctx",
                        type("C", (), {"triggered_id": {"type": "hist-view", "rid": rid_a}}))
    shown, content, values, _ = callbacks["_view"]([None, 1], history, "channel")
    assert shown == rid_a and values["channel.length_m"] == 0.28
    assert "Loss @ Nyquist" in _text(content)

    # pin it: in the strip and in the registry
    monkeypatch.setattr("halo_serdes_gui.app.ctx",
                        type("C", (), {"triggered_id": {"type": "hist-pin", "rid": rid_a}}))
    history = callbacks["_pin"]([None, 1], history)
    try:
        assert hist.find(history, rid_a)["pinned"] and rid_a in runner._PINNED
        assert "★" in _text(callbacks["_strip"](history, rid_a))
    finally:
        runner.pin(rid_a, False)

    # load run B's configuration back into the form
    loaded = dict(zip(cb.ALL_PATHS, callbacks["_load_run"](1, rid_b, history, ids)))
    assert loaded["channel.length_m"] == 0.32


def test_the_label_counts_real_changes_not_widget_spellings():
    """The browser hands back "" for a blank optional and text for a tuple;
    an untouched preset read 6 changed before both sides were coerced."""
    vals = cb.config_to_values(cb.load_preset(NRZ))
    as_widgets = {p: ("" if v is None else str(v) if isinstance(v, (list, tuple)) else v)
                  for p, v in vals.items()}
    assert hist.label(as_widgets, NRZ) == NRZ
    assert hist.label({**as_widgets, "rx.ctle.peak_db": 9.0}, NRZ) == f"{NRZ} · 1 changed"


def test_polling_never_touches_the_tab_area():
    """The tab area sits under a spinner that shows while any callback writing
    to it is in flight; a poll every 400 ms writing there kept the page
    covered for the whole run."""
    app = build_app()
    for key, spec in app.callback_map.items():
        name = getattr(spec.get("callback"), "__wrapped__", None)
        if name is not None and name.__name__ == "_poll":
            assert "tab-content" not in key, key


def test_unpinned_runs_past_the_cap_drop_off_the_strip():
    history = []
    for i in range(hist.MAX_UNPINNED + 3):
        e = {"rid": f"r{i}", "seq": i + 1, "pinned": i == 0}
        history = hist.add(history, e)
    assert len(history) == hist.MAX_UNPINNED + 1
    assert history[-1]["rid"] == "r0"             # the pinned one stays
