"""Static skew budget of lanes that share one sampling clock (ROADMAP P3 #9c).

When N lanes are sampled by one CDR's clock (one lane's loop drives them all,
or one loop listens to all of them), a lane whose data arrives ``s`` UI later
than the clock expects samples ``s`` UI early. That is the same as sampling
that lane ``-s`` UI from where its own loop would have locked. If the skew is
static, its BER is the lane's BER at that offset.

The lane's other loops still adapt where it samples: its DFE taps, its slicer
levels, and on an ADC receiver its FFE. So the curve that answers the
question is BER against sampling offset *with the equalisers re-solved at
each offset*. That is one statistical run per offset
(``run_statistical(phase_ui=)``). The engine puts the DFE and the slicer
levels at the phase it samples. The FFE it takes as given, so an ADC lane
needs the FFE it adapts to at each offset (``lane_curve(ffe_taps=)``).
Held at the lock point's instead, it reads 79-102x high a quarter UI off on
the 224G preset at 1.5 mV (example 45; 21x against the FFE a time-domain lane
converged to there, measured before ROADMAP P1 #21 engaged the ADC
receiver's DFE).

``cairn/多lane建模.md`` §8 has the time-domain cross-check and its setup (a
frozen loop held at the offset, started from the cursors there).

Static only: what the master loop's tracking does to the other lanes, and
jitter correlated between lanes, need the lanes run together in the time
domain. The clock jitter each run smears its curve with is still the one
its own loop would leave.

Phases are in UI. ``LaneCurve.offsets_ui`` is measured from the lane's own
lock point. ``shared_cdr`` works in the frame where a lane with skew 0 locks
at phase 0, so a lane with skew ``s`` locks at ``s``. Offsets wrap at half a
UI: beyond that the lane samples the next symbol, and a deskew FIFO
re-aligns the symbol index.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config.schema import LinkConfig

_FINE = 1024          # points per UI where a curve is searched (window, minmax)
_FLOOR = 1e-300


def _wrap(x):
    """Into [-0.5, 0.5) UI."""
    return (np.asarray(x, dtype=np.float64) + 0.5) % 1.0 - 0.5


def _read(phi_ui: np.ndarray, ber: np.ndarray, at_ui) -> np.ndarray:
    """``ber`` over one UI of ``phi_ui`` at ``at_ui``: log-linear, cyclic."""
    phi = np.asarray(phi_ui, dtype=np.float64)
    order = np.argsort(phi)
    phi = phi[order]
    y = np.log10(np.maximum(np.asarray(ber, dtype=np.float64)[order], _FLOOR))
    x3 = np.concatenate([phi - 1.0, phi, phi + 1.0])
    y3 = np.concatenate([y, y, y])
    lo = phi[0]
    a = (np.asarray(at_ui, dtype=np.float64) - lo) % 1.0 + lo
    return 10.0 ** np.interp(a, x3, y3)


@dataclass(frozen=True)
class LaneCurve:
    """A lane's BER against its sampling offset from its own lock point.

    ``offsets_ui`` covers one UI, ascending in [-0.5, 0.5). ``readapt`` says
    whether an FFE was given per offset (False for one FFE at every offset,
    and for a receiver without one).
    """

    offsets_ui: np.ndarray
    ber: np.ndarray
    readapt: bool = True

    def at(self, offset_ui):
        """BER at ``offset_ui`` from the lock point (log-linear between grid
        points, wrapping at half a UI)."""
        return _read(self.offsets_ui, self.ber, offset_ui)

    @property
    def best_offset_ui(self) -> float:
        """Where the curve is lowest, from the lock point."""
        x = np.arange(_FINE) / _FINE - 0.5
        return float(x[int(np.argmin(self.at(x)))])

    def window(self, ber_target: float) -> tuple[float, float] | None:
        """The offsets around the curve's minimum where BER <= ``ber_target``
        -- (low, high), from the lock point, either end possibly past half a
        UI -- or None if no offset gets there; (-inf, inf) if every one does.

        Its width is the most lane-to-lane skew a shared clock can carry at
        that BER when the clock's phase can be placed (``shared_cdr``
        "minmax"); with one lane's loop driving (``"master"``) a lane may run
        ``-high`` to ``-low`` UI later than that lane."""
        c = self.best_offset_ui
        k = np.arange(-_FINE // 2, _FINE // 2 + 1)
        x = c + k / _FINE
        ly = np.log10(np.maximum(self.at(x), _FLOOR))
        lt = np.log10(ber_target)
        ok = ly <= lt
        mid = _FINE // 2
        if not ok[mid]:
            return None
        if ok.all():
            return (-np.inf, np.inf)
        bad = np.flatnonzero(~ok)
        right = bad[bad > mid]
        left = bad[bad < mid]
        # cyclic: a curve good everywhere but one stretch has its far edge on
        # the other side of the search span
        r = right[0] if right.size else left[0] + len(x) - 1
        l_ = left[-1] if left.size else right[-1] - (len(x) - 1)

        def edge(i_bad: int, i_ok: int) -> float:
            xb = c + (i_bad - mid) / _FINE
            xo = c + (i_ok - mid) / _FINE
            yb, yo = (np.log10(max(float(self.at(v)), _FLOOR)) for v in (xb, xo))
            f = (lt - yo) / (yb - yo) if yb != yo else 0.0
            return float(xo + f * (xb - xo))

        return edge(l_, l_ + 1), edge(r, r - 1)

    def budget_ui(self, ber_target: float) -> float:
        """Width of :meth:`window`: 0 when no offset reaches the target, 1 UI
        (any skew) when every one does."""
        w = self.window(ber_target)
        if w is None:
            return 0.0
        if not np.isfinite(w[0]):
            return 1.0
        return min(w[1] - w[0], 1.0)


def _pre_ffe_lock_ui(cfg: LinkConfig, channel) -> float:
    """Where an ADC receiver's own Mueller-Muller loop samples, in UI from
    the pre-FFE pulse peak -- the statistical engine's rule
    (``statistical._lock_position``): the peak itself when the detector reads
    the equalised samples, h(-1) = h(+1) when it reads the raw ones."""
    if cfg.mm_pd_input == "ffe":
        return 0.0
    from ..cdr.linear import lock_offset_samples
    from ..engine.cascade import adc_start_for

    st = adc_start_for(cfg, channel)
    return lock_offset_samples(st.pulse.y, st.peak_rx, cfg.osr, cfg.osr) / cfg.osr


def lane_curve(cfg: LinkConfig, channel=None, *, n_phases: int = 16,
               ffe_taps=None, xtalk=None) -> LaneCurve:
    """BER of one lane against its sampling offset, from the statistical engine.

    ``n_phases`` offsets over one UI, one run each, sampled there
    (``run_statistical(phase_ui=)``): each puts the DFE taps and the slicer
    levels at that phase. ``xtalk`` is passed through.

    An ADC receiver with an FFE needs ``ffe_taps``: one array for every
    offset, or a function of the sampling phase (UI from the pre-FFE pulse
    peak) giving the FFE the lane adapts to there -- e.g. ``lambda at:
    engine.cascade.initial_ffe_taps(cfg, channel, at_ui=at)``, the MMSE FFE
    the time engine starts from, and its LMS barely leaves (0.2 % over 200k
    symbols on the 224G preset): against a time-domain lane held at the
    offset it reads 0.9-1.5x, as the FFE that lane converged to does
    (cairn/多lane建模.md §8). Until 2026-10-14 that stand-in left the Tx FFE
    out of the pulse and read 14-25x high (ROADMAP P2 #23).
    """
    from ..channel import ChannelModel
    from ..engine.statistical import run_statistical

    if channel is None:
        channel = ChannelModel.from_config(cfg)
    fcfg = cfg.rx.ffe
    has_ffe = cfg.rx.arch == "adc_dsp" and fcfg.n_pre + fcfg.n_post > 0
    if has_ffe and ffe_taps is None:
        raise ValueError(
            "an ADC lane's curve needs the FFE it adapts to: ffe_taps= one array for "
            "every offset, or a function of the sampling phase (UI from the pulse peak), "
            "e.g. lambda at: initial_ffe_taps(cfg, channel, at_ui=at)")
    per_offset = has_ffe and callable(ffe_taps)
    lock = _pre_ffe_lock_ui(cfg, channel) if per_offset else 0.0
    offs = np.arange(n_phases) / n_phases - 0.5
    ber = np.empty(n_phases)
    for i, d in enumerate(offs):
        kw = {}
        if has_ffe:
            taps = ffe_taps(float(lock + d)) if per_offset else ffe_taps
            kw = dict(ffe_taps=np.asarray(taps, dtype=np.float64), ffe_pre=fcfg.n_pre)
        ber[i] = run_statistical(cfg, channel=channel, xtalk=xtalk, phase_ui=float(d), **kw).ber
    return LaneCurve(offs, ber, readapt=per_offset)


@dataclass(frozen=True)
class SharedCdr:
    """N lanes on one sampling clock: where it samples, and what each lane reads.

    ``phase_ui`` is the clock's phase in the frame where a lane with skew 0
    locks at 0; ``offsets_ui[i]`` is lane i's offset from its own lock point
    (``phase_ui - skew_i``, wrapped) and ``ber[i]`` its BER there."""

    policy: str
    phase_ui: float
    skews_ui: np.ndarray
    offsets_ui: np.ndarray
    ber: np.ndarray

    @property
    def worst(self) -> float:
        return float(np.max(self.ber))

    @property
    def mean(self) -> float:
        """All lanes' bits together (each lane carries as many)."""
        return float(np.mean(self.ber))


POLICIES = ("master", "average", "minmax")


def shared_cdr(curves, skews_ui, policy: str = "master", master: int = 0) -> SharedCdr:
    """Per-lane BER when the lanes in ``skews_ui`` share one sampling clock.

    ``curves``: one :class:`LaneCurve` for every lane, or a list (one per
    lane). ``skews_ui[i]``: how much later lane i's data arrives [UI]. Where
    the clock samples:

    - ``"master"``: lane ``master``'s loop drives all of them, at its own
      lock point;
    - ``"average"``: one loop whose phase detector sums every lane's timing
      error with equal weight; for small errors (the linear range) it settles
      at the lanes' mean skew (taken on the circle, so skews a UI apart are
      the same lane alignment);
    - ``"minmax"``: the phase that minimises the worst lane's BER -- what a
      calibration that eye-scans every lane would choose.
    """
    s = np.asarray(skews_ui, dtype=np.float64)
    if s.ndim != 1 or s.size == 0:
        raise ValueError("skews_ui: one skew per lane")
    cv = [curves] * s.size if isinstance(curves, LaneCurve) else list(curves)
    if len(cv) != s.size:
        raise ValueError(f"{len(cv)} curves for {s.size} lanes")
    if policy not in POLICIES:
        raise ValueError(f"policy {policy!r}: one of {POLICIES}")

    def lanes(phi: np.ndarray) -> np.ndarray:
        """BER per lane (rows) at each clock phase (columns)."""
        return np.array([c.at(phi - si) for c, si in zip(cv, s)])

    if policy == "master":
        if not 0 <= master < s.size:
            raise ValueError(f"master {master}: lanes are 0..{s.size - 1}")
        phase = float(s[master])
    elif policy == "average":
        phase = float(np.angle(np.mean(np.exp(2j * np.pi * s))) / (2 * np.pi))
    else:
        grid = np.arange(_FINE) / _FINE - 0.5
        worst = np.max(np.log10(np.maximum(lanes(grid), _FLOOR)), axis=0)
        best = np.flatnonzero(worst <= worst.min() + 1e-9)
        # a flat floor: the middle of the longest run of best phases
        runs = np.split(best, np.flatnonzero(np.diff(best) > 1) + 1)
        run = max(runs, key=len)
        phase = float(grid[run[len(run) // 2]])
    off = _wrap(phase - s)
    ber = np.array([float(c.at(o)) for c, o in zip(cv, off)])
    return SharedCdr(policy, phase, s, off, ber)
