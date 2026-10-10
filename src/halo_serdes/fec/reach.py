"""Reach: where a sweep's pre-FEC BER crosses the FEC's threshold.

Reading reach off a sweep has two traps, both measured on the examples
(2026-10-09):

- Interpolating log post-FEC BER across a coarse step. The post-FEC map is
  far from linear in log pre-FEC near the threshold (steepest for a
  concatenated code), so a straight line between two post-FEC points lands
  towards the worse one: example 21's full stack read 44.2 dB where the
  pre-FEC BER crosses at about 42.8 dB. The crossing is defined by the
  pre-FEC BER reaching the threshold, so that is what is interpolated here.
- Interpolating from a point with no errors. A count of zero bounds the BER;
  it does not measure it, and whatever stand-in replaces it (1e-9, 0.5 / N)
  decides the answer: example 21's baseline read 33.2 dB on a grid that went
  from no errors to above the threshold in one 3 dB step, 32.4 dB on
  example 20's finer grid of the same receiver.

``reach`` reads a fixed sweep and refuses both; ``refine`` adds sweep points
inside the bracket (bisecting the swept parameter) until it is narrower than
a tolerance, which also takes most of the curvature of log BER out of the
interpolation (example 20's 1.5 dB step was still 0.4 dB off).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .rs import concatenated_post_fec_ber, pre_to_post_fec_ber


def fec_threshold(code: str = "kp4", inner: tuple[int, int] | None = None,
                  target: float = 1e-15) -> float:
    """The pre-FEC BER at which the projected post-FEC BER reaches ``target``
    (``code`` the RS outer, ``inner`` an (n, t) block code in front of it)."""
    def post(p):
        return (concatenated_post_fec_ber(p, *inner, outer=code) if inner
                else pre_to_post_fec_ber(p, code))
    lo, hi = -12.0, -0.3                       # log10 pre-FEC BER
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if post(10.0 ** mid) > target:
            hi = mid
        else:
            lo = mid
    return 10.0 ** lo


@dataclass(frozen=True)
class Reach:
    """A crossing read off a sweep. ``value`` is None when the sweep cannot
    place it: the BER is above the threshold at the first point ("below
    sweep") or below it at the last ("beyond sweep"), or the point before the
    crossing had no errors ("no errors at lo"). ``lo`` / ``hi`` bracket it."""
    value: float | None
    lo: float | None
    hi: float | None
    note: str = ""


def reach(x, ber, threshold: float) -> Reach:
    """The first crossing of ``threshold`` by ``ber`` along ``x`` (increasing:
    loss in dB, fibre length), log BER interpolated linearly between the two
    points that bracket it. A BER of 0 is a point with no errors."""
    x = np.asarray(x, float)
    ber = np.asarray(ber, float)
    above = np.flatnonzero(ber > threshold)
    if above.size == 0:
        return Reach(None, float(x[-1]), None, "beyond sweep")
    k = int(above[0])
    if k == 0:
        return Reach(None, None, float(x[0]), "below sweep")
    x0, x1, b0, b1 = x[k - 1], x[k], ber[k - 1], ber[k]
    if b0 <= 0:
        return Reach(None, float(x0), float(x1), "no errors at lo")
    t = (np.log10(threshold) - np.log10(b0)) / (np.log10(b1) - np.log10(b0))
    return Reach(float(x0 + t * (x1 - x0)), float(x0), float(x1))


def refine(points: dict, measure: Callable[[float], tuple[float, float]], threshold: float,
           tol: float, max_runs: int = 6) -> Reach:
    """``reach`` on a sweep that ``measure`` can extend.

    ``points`` maps the swept parameter (channel or fibre length) to
    ``(x, ber)``, ``x`` increasing with it; ``measure(param)`` runs one more.
    While the bracket is wider than ``tol`` (in ``x``) the parameter is
    bisected and the new point added to ``points`` (so a second threshold on
    the same sweep reuses it), at most ``max_runs`` times."""
    def current():
        ps = sorted(points)
        return ps, reach([points[p][0] for p in ps], [points[p][1] for p in ps], threshold)

    ps, r = current()
    for _ in range(max_runs):
        if r.lo is None or r.hi is None or r.hi - r.lo <= tol:
            break
        k = next(i for i, p in enumerate(ps) if points[p][0] == r.hi)
        mid = 0.5 * (ps[k - 1] + ps[k])
        points[mid] = measure(mid)
        ps, r = current()
    return r
