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
    return not any(s in msg for s in _WARN_DENY)


@dataclass
class RunRecord:
    id: str
    cfg: LinkConfig
    engines: tuple[str, ...]
    sim: Any = None            # SimResult (time or static)
    stat: Any = None           # StatResult
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    tb: str | None = None
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
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
             progress: "Callable[[str], None] | None" = None,
             **engine_kw) -> RunRecord:
    """Run the requested engines on ``cfg`` and register the result.

    ``progress`` is called with a stage name at each boundary. The stages are
    deliberately coarse — building the channel, each engine, done — because
    that is where this layer can see. Inside ``run_time_link`` the receiver is
    one call into a numba/pure-Python kernel that runs the whole symbol loop;
    reporting from within it would mean chunking that loop and carrying the
    CDR and DFE state across the seams, which is exactly the code architecture
    invariants #3 and #4 rest on. A progress bar is not worth reopening it.

    A callback may raise :class:`Cancelled` to abandon the run; that one
    exception is re-raised rather than recorded, so the caller can tell a
    cancellation from a failure. It can only take effect at a stage boundary.
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
                rec.sim = run_time_link(cfg, channel=channel,
                                        collect_eye=collect_eye,
                                        collect_jitter=collect_jitter,
                                        **engine_kw)
            elif "static" in engines:
                if progress:
                    progress("static engine")
                rec.sim = run_static_link(cfg, channel=channel,
                                          collect_eye=collect_eye)
            if "stat" in engines:
                if progress:
                    progress("statistical engine")
                rec.stat = run_statistical(cfg, channel=channel)
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
    return _RESULTS.get(rid) if rid else None


def _register(rec: RunRecord) -> None:
    _RESULTS[rec.id] = rec
    _ORDER.append(rec.id)
    while len(_ORDER) > _MAX_KEEP:
        old = _ORDER.pop(0)
        _RESULTS.pop(old, None)
