"""Studies run as background jobs (ROADMAP P2 #41).

A study took 0.5-20 s inside the request that pressed its Run button, with no
progress and no cancel. It now runs through ``api.start_task`` on the job
thread runs use: the top bar shows it and its Cancel stops it at the study's
next checkpoint (between engine runs), caching nothing; a run and a study do
not overlap.
"""

from __future__ import annotations

import threading
import time

import pytest

from halo_serdes_app import api, studies

pytest.importorskip("dash")
pytest.importorskip("yaml")

from test_gui_error_surfaces import _text  # noqa: E402
from test_gui_panels import run_through_job  # noqa: E402

from halo_serdes_gui import app as gui_app  # noqa: E402
from halo_serdes_gui import config_bridge as cb  # noqa: E402
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


def _wait(job, timeout=600):
    deadline = time.monotonic() + timeout
    while job.active and time.monotonic() < deadline:
        time.sleep(0.02)


def _looping_study(started: threading.Event, steps: list):
    """A study that would run for minutes, passing a checkpoint each step."""
    def fn():
        started.set()
        for i in range(10_000):
            studies.checkpoint()
            steps.append(i)
            time.sleep(0.01)
        return "finished"
    return fn


def test_a_task_keeps_its_result_and_is_one_job_with_the_runs():
    job = api.start_task(lambda: {"answer": 42}, stage="test study")
    _wait(job)
    assert job.state == "done" and job.result == {"answer": 42} and job.kind == "task"
    started, steps = threading.Event(), []
    job = api.start_task(_looping_study(started, steps), stage="slow study")
    started.wait(5)
    with pytest.raises(api.Busy):
        api.start_job({}, engines=("stat",))
    with pytest.raises(api.Busy):
        api.start_task(lambda: None, stage="another")
    api.cancel_job(job.id)
    _wait(job)
    assert job.state == "cancelled" and job.result is None and len(steps) < 10_000
    failing = api.start_task(lambda: 1 / 0, stage="broken")
    _wait(failing)
    assert failing.state == "error" and failing.error["kind"] == "ZeroDivisionError"


def test_checkpoint_outside_a_task_does_nothing():
    studies.checkpoint()
    with studies.cancellable(lambda: None):
        studies.checkpoint()


def _run(callbacks):
    ids = [{"type": "cfg", "path": p} for p in cb.ALL_PATHS]
    v = {**cb.config_to_values(cb.load_preset(NRZ)), "sim.n_symbols": 20000}
    _, (rid, *_rest) = run_through_job(callbacks, [v.get(i["path"]) for i in ids], ids,
                                       "sweeps", preset=NRZ)
    return rid


def test_a_cancelled_study_shows_its_gate_again(callbacks, monkeypatch):
    rid = _run(callbacks)
    started, steps = threading.Event(), []
    panel = gui_app._PANEL_BY_ID["sweeps"]
    monkeypatch.setattr(panel, "render", lambda rec: _looping_study(started, steps)())
    ref, *_ = callbacks["_study"](1, "sweeps", rid)
    started.wait(5)
    # the top bar follows it; Run is refused while it goes
    status = callbacks["_poll"](1, ref)[-1]
    assert "Sweeps / Reach study" in _text(status)
    refused = callbacks["_start"](1, [], [], NRZ)[-1]
    assert "A study is running" in _text(refused)
    callbacks["_cancel"](1, ref)
    _wait(api.get_job(ref["id"]))
    done, _off, _run_on, _hidden, note = callbacks["_poll"](1, ref)
    assert done == ref["id"] and "Study cancelled" in _text(note)
    _, content, *_ = callbacks["_finish"](done, ref, [], "sweeps")
    assert "Run Sweeps / Reach" in _text(content)          # the gate, nothing cached
    assert (rid, "sweeps") not in gui_app._STUDY_CACHE


def test_a_study_finishing_off_screen_waits_in_the_cache(callbacks, monkeypatch):
    rid = _run(callbacks)
    panel = gui_app._PANEL_BY_ID["jtol"]
    monkeypatch.setattr(panel, "render", lambda rec: "jtol result")
    ref, *_ = callbacks["_study"](1, "jtol", rid)
    _wait(api.get_job(ref["id"]))
    done, *_ = callbacks["_poll"](1, ref)
    # the user went to another view meanwhile: nothing is drawn over it
    assert callbacks["_finish"](done, ref, [], "single") == (gui_app.no_update,) * 5
    assert callbacks["_switch_tab"]("jtol", rid) == "jtol result"


def test_a_study_while_a_run_goes_is_refused(callbacks, monkeypatch):
    rid = _run(callbacks)
    started, steps = threading.Event(), []
    job = api.start_task(_looping_study(started, steps), stage="holding the job slot")
    started.wait(5)
    try:
        *_, status, study_on = callbacks["_study"](1, "sweeps", rid)
        assert "A run is in progress" in _text(status) and study_on is False
    finally:
        api.cancel_job(job.id)
        _wait(job)


def test_jtol_split_per_frequency_reads_what_one_call_did():
    """The JTOL study now searches one frequency per call (a cancel point
    between them); each search was already independent."""
    from halo_serdes.analysis import jitter_tolerance
    from halo_serdes.channel import ChannelModel

    cfg = cb.load_preset(NRZ)
    freqs = [5e6, 2e8]
    kw = dict(ber_threshold=1e-3, amp_lo=0.05, amp_hi=4.0, iters=2, n_symbols=4000)
    together = jitter_tolerance(cfg, freqs, **kw).tol_ui
    channel = ChannelModel.from_config(cfg)
    apart = [jitter_tolerance(cfg, [f], channel=channel, **kw).tol_ui[0] for f in freqs]
    assert list(together) == apart
