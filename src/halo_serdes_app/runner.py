"""Engine execution + an in-process result registry.

The GUI runs the (blocking, numba-jitted) engines inside the request thread
behind a ``dcc.Loading`` spinner and stashes the rich result objects
(numpy-heavy, non-JSON) in a server-side registry keyed by a short run id.
Only that id travels through Dash stores. Engine warnings (mixed-signal
envelope) and optional-dependency errors are captured, not raised.
"""

from __future__ import annotations

from collections.abc import Callable

import time
import traceback
import uuid
import warnings
from dataclasses import dataclass, field
from typing import Any

from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.engine import run_time_link
from halo_serdes.engine.static_link import run_static_link
from halo_serdes.engine.statistical import run_statistical

from .config_bridge import build_config

_RESULTS: dict[str, "RunRecord"] = {}
_ORDER: list[str] = []
_MAX_KEEP = 24

# third-party warning noise (scikit-rf etc.) that isn't actionable for a user
_WARN_DENY = ("Frequency unit not passed", "does not correspond to a valid",
              "divide by zero", "invalid value encountered")


def _keep_warning(msg: str) -> bool:
    """Whether a captured warning reaches the user (``_WARN_DENY`` lists the
    messages that are dropped)."""
    return not any(s in msg for s in _WARN_DENY)


@dataclass
class RunRecord:
    """One run as the GUI and the phone API keep it: config, engines, results,
    and the error or warnings, under an id."""
    id: str
    cfg: LinkConfig
    engines: tuple[str, ...]
    sim: Any = None            # SimResult (time or static)
    stat: Any = None           # StatResult
    # Why `stat` is absent, when something tried to produce it and failed.
    # Without this the Dual-Engine view could only render empty charts: a
    # statistical engine that raised looked exactly like one that was never
    # asked to run, which reads as "the cross-check says nothing" rather than
    # "the cross-check is broken". Invariant #3 is the whole point of that
    # view, so its failure has to be visible.
    stat_error: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    tb: str | None = None
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        """True unless the run (or its config) failed."""
        return self.error is None


class Cancelled(Exception):
    """Raised out of a ``progress`` callback to abandon a run.

    It lives here rather than in the caller because this is the layer that has
    to know *not* to swallow it: ``run_link`` turns every other exception into
    a record error so a UI never sees a crash, and a cancellation caught that
    way would be reported as a failed run.
    """


def engines_for(cfg: LinkConfig) -> tuple[str, ...]:
    """Map ``sim.engine`` to the concrete engines to run."""
    return {"time": ("time",), "stat": ("stat",),
            "both": ("time", "stat")}.get(cfg.sim.engine, ("time",))


def run_link(cfg: LinkConfig, engines: tuple[str, ...] | None = None,
             collect_eye: bool = True, collect_jitter: bool = False,
             channel: ChannelModel | None = None,
             progress: "Callable[..., None] | None" = None,
             **engine_kw) -> RunRecord:
    """Run the requested engines on ``cfg`` and register the result.

    ``progress(stage, fraction=None)`` is called at each stage boundary
    (building the channel, each engine, done) and, inside the time-domain
    engine, after every receiver chunk of ``sim.chunk_symbols`` symbols with
    the fraction of the run done. (This used to stop at stage boundaries: the
    receiver was one kernel call. Since 2026-10-05 the kernels carry their
    loop state across chunks, bit for bit, so the time engine reports from
    inside the loop -- the earlier "not worth reopening" no longer holds.)

    A callback may raise :class:`Cancelled` to abandon the run; that one
    exception is re-raised rather than recorded, so the caller can tell a
    cancellation from a failure. It takes effect at the next chunk.
    """
    engines = engines or engines_for(cfg)
    rid = uuid.uuid4().hex[:12]
    rec = RunRecord(id=rid, cfg=cfg, engines=tuple(engines))
    t0 = time.perf_counter()
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if channel is None:
                if progress:
                    progress("channel")
                channel = ChannelModel.from_config(cfg)
            if "time" in engines:
                if progress:
                    progress("time-domain engine")
                kernel_progress = None
                if progress:
                    def kernel_progress(done, total):
                        """Receiver-loop progress, as a fraction of the time engine stage."""
                        progress("time-domain engine", done / max(total, 1))
                rec.sim = run_time_link(cfg, channel=channel,
                                        collect_eye=collect_eye,
                                        collect_jitter=collect_jitter,
                                        progress=kernel_progress,
                                        **engine_kw)
            elif "static" in engines:
                if progress:
                    progress("static engine")
                rec.sim = run_static_link(cfg, channel=channel,
                                          collect_eye=collect_eye)
            if "stat" in engines:
                if progress:
                    progress("statistical engine")
                taps, pre = stat_equaliser(cfg, channel, rec.sim)
                rec.stat = run_statistical(cfg, channel=channel, ffe_taps=taps, ffe_pre=pre)
            if progress:
                progress("done")
            rec.warnings = [str(w.message) for w in caught
                            if _keep_warning(str(w.message))]
    except Cancelled:
        # Deliberately not recorded as an error: the run was abandoned, not
        # broken, and the caller distinguishes the two.
        raise
    except Exception as exc:  # surfaced in the UI, never crashes the app
        rec.error = f"{type(exc).__name__}: {exc}"
        rec.tb = traceback.format_exc()
    rec.elapsed_s = time.perf_counter() - t0
    _register(rec)
    return rec


def stat_equaliser(cfg: LinkConfig, channel, sim=None):
    """(FFE taps, precursors) the statistical engine should assume.

    The statistical engine takes the equaliser as given and applies none of
    its own. On an ADC receiver, calling it bare scored the unequalised
    channel: the 106 GBd preset read StatEye SER 0.32 beside a time-domain
    3e-4, and the Dual-Engine view, the Single Run card and the crosstalk
    baseline all showed that number. The time run's converged FFE is the one
    to cross-check against; without a time run, the MMSE FFE the time engine
    would start from (``engine.cascade.initial_ffe_taps``). Mixed-signal
    receivers have no FFE: (None, 0).
    """
    if cfg.rx.arch != "adc_dsp":
        return None, 0
    taps = getattr(sim, "ffe_taps", None)
    if taps is None or len(taps) < 2:
        from halo_serdes.engine.cascade import initial_ffe_taps
        taps = initial_ffe_taps(cfg, channel)
    return taps, cfg.rx.ffe.n_pre


def run_from_values(values: dict, **kw) -> RunRecord:
    """Build a config from form values and run it. Config-build errors (bad
    field) are captured into the record like engine errors."""
    try:
        cfg = build_config(values)
    except Exception as exc:
        rid = uuid.uuid4().hex[:12]
        rec = RunRecord(id=rid, cfg=LinkConfig(), engines=(),
                        error=f"config error: {exc}",
                        tb=traceback.format_exc())
        _register(rec)
        return rec
    return run_link(cfg, **kw)


def get(rid: str | None) -> RunRecord | None:
    """The stored record for ``rid``, or None (unknown or evicted)."""
    return _RESULTS.get(rid) if rid else None


def _register(rec: RunRecord) -> None:
    """Store a record, evicting the oldest past the cap."""
    _RESULTS[rec.id] = rec
    _ORDER.append(rec.id)
    while len(_ORDER) > _MAX_KEEP:
        old = _ORDER.pop(0)
        _RESULTS.pop(old, None)
