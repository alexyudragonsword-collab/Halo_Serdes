"""Jitter tolerance (JTOL): the max sinusoidal jitter the receiver tolerates
vs SJ frequency, at a target BER.

Standard SerDes compliance deliverable. Inject Tx sinusoidal jitter (``tx.clock.sj_ui``
amplitude at ``tx.clock.sj_freq``) and binary-search the amplitude where BER crosses a
threshold. Below the CDR loop bandwidth the recovered clock tracks the jitter →
large tolerance; above it the sampling point drifts → tolerance falls to the
intrinsic timing margin. The tolerated amplitude vs frequency is compared to a
compliance mask (the tolerance must stay above it).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from ..config.schema import LinkConfig


@dataclass
class JtolResult:
    freqs: np.ndarray        # SJ frequency [Hz]
    tol_ui: np.ndarray       # tolerated SJ amplitude (0-pk) [UI]
    ber_threshold: float

    @property
    def tol_ui_pp(self) -> np.ndarray:
        return 2.0 * self.tol_ui

    def summary(self) -> str:
        lo = self.tol_ui[0] if self.tol_ui.size else float("nan")
        hi = self.tol_ui[-1] if self.tol_ui.size else float("nan")
        return (f"JTOL @ BER<{self.ber_threshold:.0e}: "
                f"{lo:.2f} UI at {self.freqs[0]/1e6:.2f} MHz -> "
                f"{hi:.3f} UI at {self.freqs[-1]/1e6:.1f} MHz (0-pk)")


def _ber_at(cfg: LinkConfig, sj_freq: float, sj_ui: float, channel,
            engine: str = "time", ffe_taps=None) -> float:
    from ..engine import run_time_link

    clock = dataclasses.replace(cfg.tx.clock, sj_ui=float(sj_ui), sj_freq=float(sj_freq))
    tx = dataclasses.replace(cfg.tx, clock=clock)
    c = dataclasses.replace(cfg, tx=tx)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if engine == "statistical":
            from ..engine.statistical import run_statistical

            kw = {} if ffe_taps is None else dict(ffe_taps=ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
            return float(run_statistical(c, channel=channel, **kw).ber)
        res = run_time_link(c, channel=channel)
    return res.ber.ber


def jitter_tolerance(cfg: LinkConfig, freqs, ber_threshold: float = 1e-3,
                     amp_lo: float = 0.02, amp_hi: float = 2.0,
                     iters: int = 6, n_symbols: int | None = None,
                     channel=None, engine: str = "time", ffe_taps=None) -> JtolResult:
    """Tolerated SJ amplitude (0-pk UI) at each frequency for BER < threshold.

    For each frequency a binary search brackets the amplitude where BER crosses
    ``ber_threshold``. ``amp_hi`` caps the search (report it when even the max
    tested amplitude passes); ``amp_lo`` is the floor (report 0 when the link
    fails even at the smallest amplitude). Use a moderate ``ber_threshold``
    (e.g. 1e-3) so a manageable ``n_symbols`` resolves it.

    ``engine="statistical"`` reads each point from ``run_statistical``, which
    takes the tone through the loop's error response since 2026-11-01: a
    threshold such as 1e-12 that no simulation reaches. Its loop model is
    linear: past about 0.4 of the bang-bang loop's slew
    (``stat_loop.tone_slew_fraction``) it warns, and its read there can be
    optimistic -- NRZ 16G slipped at 0.41, NRZ 28G held to 0.96 with no
    errors in 60k symbols but read 1.4e-5 against the time engine's 6.7e-4
    at 0.80 (0.3 UI at 400 MHz, 2026-11-01). Below the loop the tolerance
    runs to ``amp_hi``; that side is the time engine's to say. From 0.1 UI
    RMS of phase error it warns too. ``ffe_taps``: an ADC receiver's adapted
    FFE, handed to every point.
    """
    freqs = np.asarray(freqs, dtype=float)
    if n_symbols is not None:
        cfg = dataclasses.replace(
            cfg, sim=dataclasses.replace(cfg.sim, n_symbols=int(n_symbols)))
    if channel is None:
        from ..channel import ChannelModel

        channel = ChannelModel.from_config(cfg)

    if engine not in ("time", "statistical"):
        raise ValueError(f"engine must be 'time' or 'statistical', got {engine!r}")

    def passes(f, amp):
        return _ber_at(cfg, f, amp, channel, engine, ffe_taps) < ber_threshold

    tol = np.zeros(freqs.size)
    for i, f in enumerate(freqs):
        if passes(f, amp_hi):
            tol[i] = amp_hi
            continue
        if not passes(f, amp_lo):
            tol[i] = 0.0
            continue
        lo, hi = amp_lo, amp_hi          # lo passes, hi fails
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            if passes(f, mid):
                lo = mid
            else:
                hi = mid
        tol[i] = lo
    return JtolResult(freqs=freqs, tol_ui=tol, ber_threshold=ber_threshold)


def jitter_tolerance_levels(cfg: LinkConfig, freqs, thresholds, amp_lo: float = 0.01,
                            amp_hi: float = 4.0, rtol: float = 0.08, max_runs: int = 10,
                            channel=None, engine: str = "statistical", ffe_taps=None,
                            before_run=None) -> list[JtolResult]:
    """The tolerance at each of several thresholds, from one set of runs per
    frequency (2026-11-01, the GUI's statistical JTOL).

    Every BER read at a frequency brackets every threshold, so the runs one
    threshold's search made narrow the next. Each search halves its bracket
    geometrically while it spans more than 3x or its failing end has
    saturated (BER > 1e-2), then runs regula falsi (Illinois) on
    ``sqrt(-log10 BER)``, nearly linear in amplitude where the tone eats a
    Gaussian margin, to ``rtol`` of the amplitude; ``max_runs`` caps each
    search. BER is taken to grow with the amplitude.
    ``before_run()`` is called before every engine run (a cancel point).
    """
    freqs = np.asarray(freqs, dtype=float)
    thr = sorted({float(t) for t in thresholds}, reverse=True)
    if channel is None:
        from ..channel import ChannelModel

        channel = ChannelModel.from_config(cfg)

    def score(ber: float) -> float:
        return float(np.sqrt(-np.log10(min(max(ber, 1e-300), 0.999))))

    tol = {t: np.zeros(freqs.size) for t in thr}
    for i, f in enumerate(freqs):
        seen: dict[float, float] = {}

        def ber_at(a: float) -> float:
            if a not in seen:
                if before_run is not None:
                    before_run()
                seen[a] = _ber_at(cfg, f, a, channel, engine, ffe_taps)
            return seen[a]

        for t in thr:
            if ber_at(amp_hi) < t:
                tol[t][i] = amp_hi
                continue
            if ber_at(amp_lo) >= t:
                tol[t][i] = 0.0
                continue
            lo = max(a for a, b in seen.items() if b < t)
            hi = min(a for a, b in seen.items() if b >= t and a > lo)
            target = score(t)
            g_lo, g_hi = score(seen[lo]) - target, score(seen[hi]) - target   # > 0, <= 0
            side = 0
            for _ in range(max_runs):
                if hi - lo <= rtol * lo:
                    break
                if hi > 3.0 * lo or seen[hi] > 1e-2 or g_hi == g_lo:
                    # a wide bracket, or an end where the BER has saturated and
                    # says nothing of the distance: halve it geometrically
                    a = float(np.sqrt(lo * hi))
                    side = 0
                else:
                    a = (lo * g_hi - hi * g_lo) / (g_hi - g_lo)
                    a = float(min(max(a, lo + 0.05 * (hi - lo)), hi - 0.05 * (hi - lo)))
                g = score(ber_at(a)) - target
                if g > 0.0:
                    lo, g_lo = a, g
                    if side == 1:
                        g_hi *= 0.5
                    side = 1
                else:
                    hi, g_hi = a, g
                    if side == -1:
                        g_lo *= 0.5
                    side = -1
            tol[t][i] = lo
    return [JtolResult(freqs=freqs, tol_ui=tol[t], ber_threshold=t) for t in thresholds]


def jtol_mask(freqs, lf_max_ui: float = 5.0, f_corner: float = 4e6,
              hf_floor_ui: float = 0.1) -> np.ndarray:
    """A generic compliance mask (0-pk UI) vs frequency: flat ``lf_max_ui`` at
    low frequency, rolling off at 20 dB/dec below ``f_corner`` to a high-
    frequency floor ``hf_floor_ui``. Parameterize per standard as needed."""
    freqs = np.asarray(freqs, dtype=float)
    rolloff = lf_max_ui * f_corner / freqs
    return np.maximum(hf_floor_ui, np.minimum(lf_max_ui, rolloff))
