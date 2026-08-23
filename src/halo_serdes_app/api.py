"""JSON facade — the single entry point for a non-Python UI (Android/Chaquopy).

Everything crosses the boundary as a JSON **string**: Chaquopy marshals
``str <-> String`` for free, while ``dict <-> Map`` needs explicit ``PyObject``
walking. One string-in/string-out signature therefore means one JNI call site,
one place to log or replay calls, and — the reason it lives in this package
rather than in the Android tree — a contract the existing pytest suite can
exercise directly with no device in sight.

Two rules make it work:

* **NumPy never crosses the boundary.** Results stay in the process-side
  registry (``runner._RESULTS``, already used this way by the Dash app) and the
  caller gets a short ``handle``; arrays are fetched by name through
  :func:`series`, decimated first.
* **Errors are data, never exceptions.** A Python exception crossing JNI
  becomes an opaque ``PyException`` on the Kotlin side, so :func:`call` catches
  everything and returns ``{"ok": false, "error": {...}}``.

Response envelope::

    {"ok": true,  "v": 1, "data": {...}, "warnings": [...], "ms": 106}
    {"ok": false, "v": 1, "error": {"kind": ..., "message": ..., "field": ...}}
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from . import runner, studies
from .config_bridge import (
    SECTIONS,
    build_config,
    config_to_values,
    config_to_yaml,
    derived,
    envelope_status,
    load_preset,
    preset_names,
    yaml_to_config,
)

#: Bumped when the response shape changes in a way a client must notice.
API_VERSION = 1

#: Hard ceiling on points per 1-D series handed to a UI. A phone chart cannot
#: resolve more, and JSON-parsing more is what makes a UI feel broken.
MAX_SERIES_POINTS = 2048

#: Eye/PDF heatmaps are reduced to at most this grid before transport.
MAX_HEATMAP_ROWS = 256
MAX_HEATMAP_COLS = 128

#: Symbol counts behind the quality tiers, defined here so every UI offers the
#: same three and no client invents its own.
QUALITY_SYMBOLS = {"fast": 20_000, "standard": 100_000, "precise": 500_000}

#: Floor for series a plot spec declares logarithmic. The studies already
#: bottom out here (reach and crosstalk emit 1e-300), but the concatenated-FEC
#: projection underflows to exact zero — which a log axis cannot draw at all.
#: Clamping belongs where the log axis is promised, not in the study, whose
#: zero is an honest "below double precision".
LOG_AXIS_FLOOR = 1e-300

#: log10 clamp for eye densities. Below this the statistical engine has not
#: resolved a probability at all, so it marks absence rather than a value.
EYE_LOG_FLOOR = -18.0


# --------------------------------------------------------------- helpers ---

def _decimate(y: np.ndarray, max_pts: int) -> np.ndarray:
    """Thin a 1-D series to ``max_pts`` preserving extremes.

    Plain striding drops the spikes that matter here (a CDR phase excursion, a
    tap that rang before settling), so each output bucket contributes its min
    and its max in index order — the envelope survives at any zoom level.
    """
    y = np.asarray(y, dtype=np.float64).ravel()
    if y.size <= max_pts:
        return y
    n_buckets = max(max_pts // 2, 1)
    edges = np.linspace(0, y.size, n_buckets + 1, dtype=np.int64)
    out = np.empty(n_buckets * 2, dtype=np.float64)
    for i in range(n_buckets):
        seg = y[edges[i]:edges[i + 1]]
        if seg.size == 0:
            out[2 * i] = out[2 * i + 1] = np.nan
            continue
        lo, hi = seg.min(), seg.max()
        # keep chronological order within the bucket
        out[2 * i], out[2 * i + 1] = (lo, hi) if seg.argmin() <= seg.argmax() else (hi, lo)
    return out


def _reduce_heatmap(z: np.ndarray, max_rows: int, max_cols: int) -> np.ndarray:
    """Block-reduce a 2-D map by **max**, not mean.

    An eye PDF carries its information in the low-probability skirts that set
    BER; averaging washes them out, whereas a block max preserves the tail
    envelope. This is the same reason the Dash figures plot log10 of the PDF.
    """
    z = np.asarray(z, dtype=np.float64)
    rows, cols = z.shape
    r_step = max(1, int(np.ceil(rows / max_rows)))
    c_step = max(1, int(np.ceil(cols / max_cols)))
    if r_step == 1 and c_step == 1:
        return z
    r_trim, c_trim = (rows // r_step) * r_step, (cols // c_step) * c_step
    blocks = z[:r_trim, :c_trim].reshape(
        r_trim // r_step, r_step, c_trim // c_step, c_step)
    return blocks.max(axis=(1, 3))


def _jsonable(obj: Any) -> Any:
    """Convert numpy/tuple/dataclass-ish values into JSON-safe Python."""
    if isinstance(obj, np.ndarray):
        return [_jsonable(v) for v in obj.tolist()]
    if isinstance(obj, (np.floating, np.integer)):
        obj = obj.item()
    if isinstance(obj, float):
        # JSON has no NaN/Infinity; a UI would rather see null than choke
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return str(obj)          # engine objects (Adc model, Waveform) -> repr


def _series(x, y, max_pts: int = MAX_SERIES_POINTS) -> dict:
    """A plot-ready {x, y} pair, decimated together so they stay aligned."""
    y_arr = np.asarray(y, dtype=np.float64).ravel()
    x_arr = (np.arange(y_arr.size, dtype=np.float64) if x is None
             else np.asarray(x, dtype=np.float64).ravel())
    if y_arr.size > max_pts:
        idx = np.linspace(0, y_arr.size - 1, max_pts).astype(np.int64)
        x_arr, y_arr = x_arr[idx], y_arr[idx]
    return {"x": _jsonable(x_arr), "y": _jsonable(y_arr)}


# ------------------------------------------------------- config methods ---

def _m_schema(_payload: dict) -> dict:
    """The form spec that drives an auto-generated settings UI.

    ``SECTIONS`` is already pure JSON data (path/label/kind/scale/options), so
    it ships verbatim — the client builds every widget from it and no field
    definition is duplicated outside Python.
    """
    from .config_bridge import CONFIGS_DIR

    return {"sections": _jsonable(SECTIONS),
            "presets": preset_names(),
            # Where the presets were found. A one-entry `presets` list means
            # configs/ was not shipped; reporting the path it looked at turns
            # that from a mystery into a packaging bug you can see.
            "configs_dir": str(CONFIGS_DIR),
            "api_version": API_VERSION}


def _m_preset(payload: dict) -> dict:
    name = payload.get("name") or preset_names()[0]
    return {"name": name, "values": _jsonable(config_to_values(load_preset(name)))}


def _m_derive(payload: dict) -> dict:
    """Validate + derive without running anything (sub-millisecond).

    Field errors come back keyed by dotted path where the message names one, so
    the client can mark the offending input rather than showing a dialog.
    """
    values = payload.get("values") or {}
    try:
        cfg = build_config(values)
    except Exception as exc:
        msg = str(exc)
        field = next((p for p in (values or {}) if p and p in msg), None)
        return {"valid": False, "field_errors": {field or "_form": msg}}
    level, message = envelope_status(cfg)
    return {"valid": True, "field_errors": {},
            "derived": _jsonable(derived(cfg)),
            "channel": _channel_status(cfg),
            "envelope": {"level": level, "message": message}}


def _channel_status(cfg) -> dict:
    """Whether this config's channel data is actually reachable, before running.

    A touchstone preset names a repo-relative ``.s4p``; ``build_config`` has
    already rewritten it to an absolute path if one was found. When none was
    — an Android build ships the YAML presets but not the 4.4 MB of channel
    files — the config is still perfectly valid, so ``valid`` stays true and
    this reports separately. Without it a client can only discover the problem
    by running the engine and catching the failure, which is how it reads to a
    user: as a broken preset rather than as an absent file.
    """
    if cfg.channel.kind != "touchstone":
        return {"ok": True, "message": ""}
    if not cfg.channel.file:
        return {"ok": False, "message": "channel.file is unset"}
    if Path(cfg.channel.file).exists():
        return {"ok": True, "message": ""}
    return {"ok": False,
            "message": f"touchstone file not available here: {cfg.channel.file}"}


def _m_to_yaml(payload: dict) -> dict:
    return {"text": config_to_yaml(build_config(payload.get("values") or {}))}


def _m_from_yaml(payload: dict) -> dict:
    return {"values": _jsonable(config_to_values(yaml_to_config(payload["text"])))}


# ------------------------------------------------------ compute methods ---

def _m_run_stat(payload: dict) -> dict:
    """Statistical engine — the interactive path (~0.1 s desktop, ~0.4 s ARM)."""
    rec = runner.run_from_values(payload.get("values") or {}, engines=("stat",))
    if not rec.ok:
        raise _RecordError(rec)
    s = rec.stat
    return {"handle": rec.id,
            "ber": _jsonable(s.ber), "ser": _jsonable(s.ser),
            "best_phi": int(s.best_phi),
            "bathtub": _series(s.phi_ui, np.maximum(s.ber_phi, 1e-30)),
            "elapsed_s": rec.elapsed_s,
            "warnings": rec.warnings}


def _m_run_com(payload: dict) -> dict:
    from halo_serdes.analysis.com import compute_com
    from halo_serdes.channel import ChannelModel

    cfg = build_config(payload.get("values") or {})
    r = compute_com(ChannelModel.from_config(cfg), cfg)
    return {"com_db": _jsonable(r.com_db), "a_signal": _jsonable(r.a_signal),
            "a_noise": _jsonable(r.a_noise), "fom_db": _jsonable(r.fom_db),
            "fom_isi": _jsonable(r.fom_isi), "fom_xtalk": _jsonable(r.fom_xtalk),
            "fom_noise": _jsonable(r.fom_noise),
            "fom_jitter": _jsonable(r.fom_jitter),
            "detail": _jsonable(r.detail),
            "summary": r.summary()}


#: studies.* take a record; fec_projection is config-independent.
_STUDIES = {
    "reach": studies.reach_study, "crosstalk": studies.crosstalk_study,
    "multilane": studies.multilane_study, "com": studies.com_study,
    "fixedpoint": studies.fixedpoint_study, "jtol": studies.jtol_study,
}


def _m_study(payload: dict) -> dict:
    """Run one sweep. All of them return flat dicts of <=40-point arrays.

    The studies' own ``{"error": ...}`` convention marks *unsupported
    configurations* (e.g. a reach sweep needs an analytic channel), which is
    information, not a failure — it is relayed as ``note`` so a client shows an
    explanatory card rather than a red error.
    """
    name = payload.get("name")
    if name == "fec":
        plots = studies.STUDY_PLOTS["fec"]
        return {"name": name, "plots": plots,
                "data": _clamp_log_axes(_jsonable(studies.fec_projection()), plots)}
    fn = _STUDIES.get(name)
    if fn is None:
        raise ValueError(f"unknown study {name!r}; "
                         f"expected one of {sorted(_STUDIES) + ['fec']}")
    handle = payload.get("handle")
    rec = runner.get(handle) if handle else None
    if rec is None:
        engines = ("time",) if name == "fixedpoint" else ("stat",)
        rec = runner.run_from_values(payload.get("values") or {}, engines=engines)
        if not rec.ok:
            raise _RecordError(rec)
    out = fn(rec)
    if "error" in out:
        return {"name": name, "handle": rec.id, "note": out["error"],
                "data": {}, "plots": []}
    plots = studies.STUDY_PLOTS.get(name, [])
    return {"name": name, "handle": rec.id,
            "data": _clamp_log_axes(_jsonable(out), plots), "plots": plots}


def _clamp_log_axes(data: dict, plots: list[dict]) -> dict:
    """Lift series on declared-log axes off zero, so the axis is drawable."""
    keys = {k for p in plots for k in ([p["x"]] if p.get("x_log") else [])
            + (p["y"] if p.get("y_log") else [])}
    for k in keys:
        seq = data.get(k)
        if isinstance(seq, list):
            data[k] = [None if v is None else max(float(v), LOG_AXIS_FLOOR)
                       for v in seq]
    return data


def _m_series(payload: dict) -> dict:
    """Fetch one named array from a stored result, decimated for transport."""
    rec = runner.get(payload.get("handle"))
    if rec is None:
        raise KeyError(f"unknown handle {payload.get('handle')!r}")
    key = payload.get("key")
    max_pts = int(payload.get("max_points") or MAX_SERIES_POINTS)

    if key == "stat_eye":
        if rec.stat is None:
            raise ValueError("no statistical result on this handle")
        z = _reduce_heatmap(rec.stat.eye_pdf, MAX_HEATMAP_ROWS, MAX_HEATMAP_COLS)
        # log scale: the informative range is the low-probability skirt
        z = np.log10(np.maximum(z, 10.0 ** EYE_LOG_FLOOR))
        # Report the range the data actually occupies, plus the clamp floor.
        #
        # This used to declare a fixed -12..0. Neither end was real: a typical
        # eye tops out near -2.7 (so the top third of any colour ramp went
        # unused) and a third of the cells sit at the -18 clamp, well below the
        # stated minimum. A client colouring by the declared range produced a
        # washed-out picture that also implied the floor cells held a
        # measured value.
        #
        # Cells at `floor` are not a small probability — they are "no
        # probability resolved on this grid", which a renderer should show as
        # absence rather than as the darkest colour in the scale.
        above = z[z > EYE_LOG_FLOOR]
        return {"kind": "heatmap", "z": _jsonable(z),
                "floor": EYE_LOG_FLOOR,
                "zmin": float(above.min()) if above.size else EYE_LOG_FLOOR,
                "zmax": float(z.max()),
                "rows": int(z.shape[0]), "cols": int(z.shape[1])}
    if key == "bathtub":
        if rec.stat is None:
            raise ValueError("no statistical result on this handle")
        return {"kind": "series",
                **_series(rec.stat.phi_ui, np.maximum(rec.stat.ber_phi, 1e-30), max_pts)}
    if rec.sim is not None and key in ("phase_track", "pd_hist", "y_slicer"):
        arr = (rec.sim.y_slicer if key == "y_slicer"
               else rec.sim.extras.get(key))
        if arr is None:
            raise ValueError(f"{key!r} not captured for this run")
        return {"kind": "series", "x": None,
                "y": _jsonable(_decimate(np.asarray(arr), max_pts))}
    raise ValueError(f"unknown series key {key!r}")


def _m_release(payload: dict) -> dict:
    rid = payload.get("handle")
    runner._RESULTS.pop(rid, None)
    if rid in runner._ORDER:
        runner._ORDER.remove(rid)
    return {"released": rid}


# --------------------------------------------------- long-running jobs ---

@dataclasses.dataclass
class _Job:
    id: str
    state: str = "queued"           # queued | running | done | error | cancelled
    stage: str = ""
    handle: str | None = None
    error: dict | None = None
    started: float = 0.0
    elapsed_s: float = 0.0
    cancel: threading.Event = dataclasses.field(default_factory=threading.Event)


_JOBS: dict[str, _Job] = {}
_JOBS_LOCK = threading.Lock()


def _m_start_time_run(payload: dict) -> dict:
    """Start a time-domain run on a background thread and return its job id.

    One at a time. Two concurrent runs would share one interpreter and thrash
    the same registry for no gain — numpy releases the GIL, so a single worker
    already leaves the UI thread responsive.
    """
    with _JOBS_LOCK:
        busy = [j for j in _JOBS.values() if j.state in ("queued", "running")]
        if busy:
            raise RuntimeError(
                f"a run is already in progress (job {busy[0].id}); "
                "cancel it or wait for it to finish")

    quality = str(payload.get("quality") or "fast")
    if quality not in QUALITY_SYMBOLS:
        raise ValueError(f"unknown quality {quality!r}; "
                         f"expected one of {sorted(QUALITY_SYMBOLS)}")
    values = dict(payload.get("values") or {})
    values["sim.n_symbols"] = str(QUALITY_SYMBOLS[quality])

    job = _Job(id=uuid.uuid4().hex[:12], started=time.perf_counter())
    with _JOBS_LOCK:
        _JOBS[job.id] = job

    threading.Thread(target=_run_job, args=(job, values), daemon=True,
                     name=f"halo-job-{job.id}").start()
    return {"job": job.id, "quality": quality,
            "n_symbols": QUALITY_SYMBOLS[quality]}


def _run_job(job: _Job, values: dict) -> None:
    def progress(stage: str) -> None:
        if job.cancel.is_set():
            raise runner.Cancelled()
        job.stage = stage

    job.state = "running"
    try:
        rec = runner.run_from_values(values, engines=("time",),
                                     progress=progress)
        if rec.ok:
            job.handle, job.state = rec.id, "done"
        else:
            job.state = "error"
            job.error = {"kind": "engine", "message": rec.error,
                         "field": None, "traceback": rec.tb}
    except runner.Cancelled:
        job.state = "cancelled"
    except Exception as exc:
        job.state = "error"
        job.error = {"kind": type(exc).__name__, "message": str(exc),
                     "field": None, "traceback": traceback.format_exc()}
    finally:
        job.elapsed_s = time.perf_counter() - job.started


def _m_poll(payload: dict) -> dict:
    job = _JOBS.get(payload.get("job"))
    if job is None:
        raise KeyError(f"unknown job {payload.get('job')!r}")
    return {"job": job.id, "state": job.state, "stage": job.stage,
            "handle": job.handle, "elapsed_s": round(job.elapsed_s, 3),
            "cancel_pending": job.cancel.is_set() and job.state == "running",
            "job_error": job.error}


def _m_cancel(payload: dict) -> dict:
    """Request cancellation. Takes effect at the next stage boundary.

    The receiver kernel is one uninterruptible call, so a cancel raised while
    it is running is not honoured until it returns — which for a long run is
    most of the wait. Say so rather than showing a button that appears to stop
    the work: ``poll`` reports ``cancel_pending`` until the unwind actually
    happens.
    """
    job = _JOBS.get(payload.get("job"))
    if job is None:
        raise KeyError(f"unknown job {payload.get('job')!r}")
    job.cancel.set()
    return {"job": job.id, "state": job.state,
            "note": "takes effect at the next stage boundary; the receiver "
                    "kernel cannot be interrupted mid-run"}


# ------------------------------------------------------ touchstone import ---

def _m_import_touchstone(payload: dict) -> dict:
    """Describe a Touchstone file so a client can confirm before adopting it.

    Read through the same loader the engine uses, so anything this accepts the
    engine accepts.

    The number that matters most is not the insertion loss — it is the file's
    own frequency span. Measured `.s4p` files routinely stop well below the
    Nyquist of the config they get pointed at (the bundled peters set ends near
    15 GHz), and beyond it the channel model extrapolates conservatively. That
    still produces a plot and a BER, so nothing on screen would otherwise say
    the answer came from extrapolation rather than from data. `extrapolated`
    does.
    """
    from halo_serdes.channel import ChannelModel, import_diff_network

    path = str(payload.get("path") or "")
    if not path:
        raise ValueError("path is required")
    if not Path(path).is_file():
        raise FileNotFoundError(f"no such file: {path}")

    cfg = build_config(payload.get("values") or {})

    # The raw network first: once ChannelModel has interpolated onto its own
    # grid the file's real span is gone.
    sdd = import_diff_network(path, renumber=cfg.channel.renumber,
                              lane=cfg.channel.lane)
    file_f_max = float(np.max(sdd.f))
    nyq = float(cfg.f_nyquist)

    f_max = cfg.channel.f_max or (4.0 * nyq)
    ch = ChannelModel.from_touchstone(
        path, f_max=f_max, n_freq=cfg.channel.n_freq,
        zs_diff=cfg.channel.zs_diff, zl_diff=cfg.channel.zl_diff,
        renumber=cfg.channel.renumber, lane=cfg.channel.lane)

    il = ch.insertion_loss_db()
    idx = int(np.argmin(np.abs(ch.f - nyq)))
    return _jsonable({
        "file": path,
        "name": ch.name,
        "file_f_max_ghz": file_f_max / 1e9,
        "n_freq": int(ch.f.size),
        "nyquist_ghz": nyq / 1e9,
        "il_db_at_nyquist": float(il[idx]),
        # True when the config asks the model about frequencies the file does
        # not contain, so the client can say where the number came from.
        "extrapolated": bool(nyq > file_f_max),
    })


# --------------------------------------------------------------- dispatch ---

class _RecordError(Exception):
    """A failed RunRecord carried as an exception so :func:`call` can shape it."""

    def __init__(self, rec):
        super().__init__(rec.error or "run failed")
        self.rec = rec


_METHODS = {
    "schema": _m_schema, "preset": _m_preset, "derive": _m_derive,
    "to_yaml": _m_to_yaml, "from_yaml": _m_from_yaml,
    "run_stat": _m_run_stat, "run_com": _m_run_com, "study": _m_study,
    "series": _m_series, "release": _m_release,
    "start_time_run": _m_start_time_run, "poll": _m_poll, "cancel": _m_cancel,
    "import_touchstone": _m_import_touchstone,
}


def methods() -> list[str]:
    """Names accepted by :func:`call`."""
    return sorted(_METHODS)


def call(method: str, payload_json: str = "{}") -> str:
    """Dispatch one request. Never raises — failures come back as data."""
    t0 = time.perf_counter()
    try:
        payload = json.loads(payload_json) if payload_json else {}
        fn = _METHODS.get(method)
        if fn is None:
            raise ValueError(f"unknown method {method!r}; expected one of {methods()}")
        data = fn(payload)
        warns = data.pop("warnings", []) if isinstance(data, dict) else []
        return json.dumps({"ok": True, "v": API_VERSION, "data": data,
                           "warnings": warns,
                           "ms": round((time.perf_counter() - t0) * 1e3, 1)},
                          allow_nan=False)
    except _RecordError as exc:
        rec = exc.rec
        kind = "config" if (rec.error or "").startswith("config error:") else "engine"
        return json.dumps({"ok": False, "v": API_VERSION,
                           "error": {"kind": kind, "message": rec.error,
                                     "field": None, "traceback": rec.tb}})
    except Exception as exc:
        return json.dumps({"ok": False, "v": API_VERSION,
                           "error": {"kind": type(exc).__name__,
                                     "message": str(exc), "field": None,
                                     "traceback": traceback.format_exc()}})
