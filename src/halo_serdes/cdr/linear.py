"""Small-signal model of the two digital CDR loops: what they do to clock jitter.

Why this exists
---------------
A CDR is a high-pass to the transmit clock's jitter: wander inside the loop
bandwidth is followed, everything above it lands on the sampling instant.
The time-domain kernels (``cdr/kernels.py``, ``cdr/adc_kernel.py``) do this
by running the loop; the statistical engine cannot run anything and needs
the same answer in closed form -- the error response ``E(f) = 1 - H(f)`` that
weights a phase-noise profile before it is integrated
(:meth:`halo_serdes.tx.clock.ClockProfile.untracked_sigma_s`), plus the
jitter the loop adds by itself. Without it a profile clock would smear the
statistical bathtub with its *total* jitter and the two engines could never
agree on a coloured clock (invariant #3).

The loop as the kernels run it
------------------------------
Both kernels are the same discrete-time PI loop, one update per ``f_update``::

    integ += ki * pd
    pos   += UI + kp * pd + integ          (one update of delay before pos moves)

with ``kp = 2**-kp_shift`` and ``ki = 2**-ki_shift`` in UI per unit of phase
detector output. The ADC kernel updates once per interleave block
(``n_lanes`` symbols) on the block-averaged PD and spreads the correction over
the block, so its ``f_update`` is ``f_baud / n_lanes``; the mixed-signal
kernel updates every symbol. Writing the PD as ``pd = k_pd * e + q`` (``e`` the
phase error in UI, ``q`` the detector's own noise) and ``D`` for any extra
latency in updates::

    L(z) = k_pd * (kp + ki * z / (z - 1)) * z**-D / (z - 1)
    H    = L / (1 + L)            what the loop follows
    E    = 1 / (1 + L)            what it leaves on the sampler
    e    = E * phi_in  -  (H / k_pd) * q

so the sampling-instant error has two parts: the clock jitter the loop did not
track, and the detector noise it *did* track. The second is not a detail --
for a bang-bang loop on a quiet clock it is the whole answer.

The phase detectors
-------------------
* **Bang-bang** (mixed-signal path): the output is a sign, so for Gaussian
  input jitter ``sigma_e`` the mean output per UI of error is
  ``E[pd | e] = rho * erf(e / (sqrt(2) sigma_e))``, slope
  ``k_pd = rho * sqrt(2/pi) / sigma_e`` (``rho`` = transition density, 1/2
  for random data), and the remainder ``q`` is taken white with variance ``rho - rho^2/3``
  (:func:`bb_pd_noise_var`). ``sigma_e`` is
  itself the loop's output, so :func:`bb_loop_fixed_point` iterates
  ``sigma_e -> k_pd -> (E, H) -> sigma_e`` to a fixed point. On a quiet clock
  this closes at ``sigma_e ~ 0.6 kp``: the familiar bang-bang hunting,
  derived rather than assumed. Noise and ISI at the edge sample randomise
  the comparison too; the caller converts them to UI through the edge slope
  and passes them as ``sigma_edge_ui`` (the statistical engine reads both
  off its pulse response).
* **Mueller-Muller** (ADC path) is linear in volts, and its gain is the mean
  slope of ``x[n] (sign x[n+1] - sign x[n-1])`` over random data on the
  pulse's cursor set at the lock point (:func:`mm_pd_statistics`). Its noise
  is not white: neighbouring outputs share their main cursors with opposite
  signs, and what a loop far slower than the symbol rate integrates is the
  output's density at low frequency, a fifth of its per-symbol variance on
  the links measured -- and on a short repeating pattern not even that,
  only lines at multiples of the pattern's repetition rate
  (:class:`PdNoise`). Its timing function is linear only near the lock
  point: :func:`mm_hold_range_ui` measures how far the pull back keeps
  growing, which a linear model cannot see and the caller compares with the
  loop's error.

A run is not the steady state
-----------------------------
``|E|^2`` integrated against a profile is what the loop leaves once it has
run long enough to forget its start. A time-domain run starts the loop from
rest and lasts ``N`` UI, and its clock's jitter sits on the lines ``k/(N UI)``
(the synthesiser's FFT bins). A well-damped loop forgets its start in a few
bandwidth periods and the two agree; an underdamped one rings, and its
peak takes ``~Q / (pi f)`` to build -- on example 31's ADC link at kp_shift 8-9
(``|E|`` peaking 8-16x near 1.1 MHz) far longer than a 200k-symbol run, so the
steady state read 1.34-1.70x the run's error. :class:`ErrorResponse` carries
both: called, ``|E(f)|``; :meth:`ErrorResponse.run_mean_sq`, the mean square
a sinusoid at ``f`` leaves over a window of a run that starts from rest, in
closed form from the loop's poles (ROADMAP P2 #28).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

TWOPI = 2.0 * np.pi


@dataclass(frozen=True)
class LoopParams:
    """One CDR loop in the units the small-signal model wants."""

    f_update: float          # loop update rate [Hz]
    kp_ui: float             # proportional step per PD unit [UI]
    ki_ui: float             # integral step per PD unit [UI]
    latency_updates: int = 0 # extra pipeline delay in loop updates
    # An adaptive FFE takes up part of a sampling offset by itself: its LMS
    # moves the taps along their timing derivative, ``ffe_track`` of what is
    # left per loop update (``c += ffe_track (e - c)``), so the equalised
    # samples see ``e - c``. With the detector on the FFE's output
    # (``ffe_in_loop``) that is a second tracking path in parallel with the
    # loop, in effect more proportional gain; on raw samples a high-pass
    # after it. 0: no adaptive FFE (ROADMAP P2 #29).
    ffe_track: float = 0.0
    ffe_in_loop: bool = True

    @classmethod
    def from_config(cls, cfg) -> "LoopParams":
        """Read the loop the kernels will run from a ``LinkConfig``."""
        cdr = cfg.rx.cdr
        if cfg.rx.arch == "adc_dsp":
            n_lanes = max(int(cfg.rx.adc.n_lanes), 1)
            return cls(f_update=cfg.symbol_rate / n_lanes,
                       kp_ui=2.0 ** -cdr.kp_shift, ki_ui=2.0 ** -cdr.ki_shift,
                       latency_updates=max(0, cdr.loop_latency_symbols // n_lanes))
        return cls(f_update=cfg.symbol_rate,
                   kp_ui=2.0 ** -cdr.kp_shift, ki_ui=2.0 ** -cdr.ki_shift,
                   latency_updates=0)


@dataclass(frozen=True)
class LoopSolution:
    """The small-signal operating point of one loop on one clock."""

    k_pd: float              # linearised PD gain [PD units / UI]
    sigma_untracked_ui: float  # clock jitter the loop left [UI]
    sigma_self_ui: float     # detector noise the loop followed [UI]
    bandwidth_hz: float      # -3 dB point of |E|, the tracking bandwidth
    # how far from the lock point a Mueller-Muller detector's restoring force
    # keeps growing [UI] (mm_hold_range_ui), and the part of the error slow
    # enough to carry the loop itself there: its own detector noise and the
    # clock it follows late inside its bandwidth [UI]. None for bang-bang
    hold_range_ui: float | None = None
    sigma_wander_ui: float | None = None
    # the error [UI] once the loop has run long enough to forget its start --
    # sigma_ui is a run of the configured length from rest, which an
    # underdamped loop does not ring up to (ErrorResponse.run_mean_sq)
    sigma_steady_ui: float | None = None
    # the RMS error [UI] at points spread over the run (their mean square is
    # sigma_ui^2): an underdamped loop's grows as it rings up, and a smear of
    # its mean alone reads its tail short (ErrorResponse.run_sq_at)
    sigma_over_run_ui: tuple[float, ...] | None = None
    # the adaptive FFE's share of the tracking (LoopParams.ffe_track), per update
    ffe_track: float = 0.0
    # the second-order loop's damping ratio at this gain, kp sqrt(k_pd / ki) / 2
    # (loop_damping); None without an integral path
    damping: float | None = None
    # what the loop leaves of each sinusoidal tone it was given, (amplitude
    # [UI], frequency [Hz]) as in, the amplitude now ``A |E(f)|`` -- a phase
    # that swings, not noise: read as an arcsine, not folded into sigma_ui
    sj_residual: tuple[tuple[float, float], ...] = ()

    @property
    def sigma_ui(self) -> float:
        """Total RMS sampling-instant error [UI]."""
        return float(np.hypot(self.sigma_untracked_ui, self.sigma_self_ui))


def loop_gain(f: np.ndarray, loop: LoopParams, k_pd: float) -> np.ndarray:
    """Open-loop L(f) (complex) on ``f`` [Hz]; zero where ``f`` is outside (0, f_update/2]."""
    f = np.asarray(f, dtype=np.float64)
    out = np.zeros(f.shape, dtype=complex)
    inside = (f > 0) & (f <= loop.f_update / 2.0)
    z = np.exp(1j * TWOPI * f[inside] / loop.f_update)
    zm1 = z - 1.0
    gain = k_pd * (loop.kp_ui + loop.ki_ui * z / zm1) / zm1
    if loop.latency_updates:
        gain = gain * z ** (-loop.latency_updates)
    out[inside] = gain
    return out


def _polys(loop: LoopParams, k_pd: float) -> tuple[np.ndarray, np.ndarray]:
    """The denominator ``D`` of ``E = N / D`` and ``N - D``, polynomials in z
    (descending), the adaptive FFE's tracking included (``LoopParams.ffe_track``).

    Loop alone: ``N = (z - 1)^2 z^lat``, ``T = k (kp (z - 1) + ki z)``,
    ``D = N + T``. The FFE on the detector's input adds ``g (z - 1) z^lat``
    to ``D``; on raw samples ``E`` (and ``T / D``) is followed by
    ``(z - 1) / (z - 1 + g)``. ``N - D`` is built from its own terms, not by
    subtracting: near z = 1 both are small differences of order-1
    coefficients.
    """
    lat = np.r_[1.0, np.zeros(loop.latency_updates)]
    n = np.polymul([1.0, -2.0, 1.0], lat)
    t = np.array([k_pd * (loop.kp_ui + loop.ki_ui), -k_pd * loop.kp_ui])
    d = np.polyadd(n, t)
    r = -t
    g = float(loop.ffe_track)
    if g > 0:
        if loop.ffe_in_loop:
            track = g * np.polymul([1.0, -1.0], lat)
            d, r = np.polyadd(d, track), np.polysub(r, track)
        else:
            # N (z - 1) - D (z - 1 + g) = -T (z - 1) - g D
            r = np.polysub(np.polymul(r, [1.0, -1.0]), g * d)
            d = np.polymul(d, [1.0, g - 1.0])
    return d, r


def _on_circle(f: np.ndarray, loop: LoopParams, k_pd: float, which: str) -> np.ndarray:
    """|N / D| (``which`` "e") or |T / D| ("h") on ``f`` (as ``_polys``);
    1 / 0 outside (0, f_update / 2]. On the circle with ``z - 1`` taken
    whole, which a polynomial in z loses near z = 1."""
    f = np.asarray(f, dtype=np.float64)
    inside = (f > 0) & (f <= loop.f_update / 2.0)
    out = np.ones(f.shape) if which == "e" else np.zeros(f.shape)
    w = TWOPI * f[inside] / loop.f_update
    z = np.exp(1j * w)
    zm1 = 2j * np.sin(w / 2.0) * np.exp(0.5j * w)
    n = zm1 ** 2 * z ** loop.latency_updates
    t = k_pd * (loop.kp_ui * zm1 + loop.ki_ui * z)
    g = float(loop.ffe_track)
    if loop.ffe_in_loop:
        h = (n if which == "e" else t) / (n + t + g * zm1 * z ** loop.latency_updates)
    else:
        h = (n if which == "e" else t) / (n + t) * zm1 / (zm1 + g)
    out[inside] = np.abs(h)
    return out


def error_response(f: np.ndarray, loop: LoopParams, k_pd: float) -> np.ndarray:
    """|E(f)| = |1 - H(f)|: the fraction of input jitter at ``f`` left on the sampler.

    Above ``f_update / 2`` the loop never sees the frequency as distinct and
    the response is 1 (nothing tracked). With an adaptive FFE
    (``LoopParams.ffe_track``) it is what the equalised samples are left with.
    """
    if loop.ffe_track > 0:
        return _on_circle(f, loop, k_pd, "e")
    return np.abs(1.0 / (1.0 + loop_gain(f, loop, k_pd)))


@dataclass(frozen=True)
class ErrorResponse:
    """One loop's error response at one detector gain: ``|E(f)|`` when called.

    The solvers hand this, not a bare function, to the caller's
    ``untracked_ui``, so a caller that knows the run (the statistical engine)
    can ask what a run starting from rest leaves (:meth:`run_mean_sq`)
    instead of the steady state; one that only calls it gets ``|E|``.
    """

    loop: LoopParams
    k_pd: float

    def __call__(self, f: np.ndarray) -> np.ndarray:
        return error_response(f, self.loop, self.k_pd)

    def _transient(self, f: np.ndarray):
        """``(inside, e, d, q)`` on the frequencies the loop tracks: ``G_n = e -
        sum_i d_i q_i^n``; None if the loop is not stable or has no gain."""
        loop, k = self.loop, self.k_pd
        inside = (f > 0) & (f <= loop.f_update / 2.0)
        if not np.any(inside) or k <= 0:
            return None
        if loop.ffe_track > 0:
            # E = N / D = 1 + (N - D) / D, the residues at D's poles (D monic)
            den, rem = _polys(loop, k)
            poles = np.roots(den)
            if np.any(np.abs(poles) >= 1.0):
                return None
            c = np.polyval(rem, poles) / np.array(
                [np.prod(p - np.delete(poles, i)) for i, p in enumerate(poles)])
        else:
            # D(z) = (z - 1)^2 z^lat + k (kp (z - 1) + ki z); E = 1 + R / D
            den = np.zeros(loop.latency_updates + 3)
            den[:3] = (1.0, -2.0, 1.0)
            den[-2] += k * (loop.kp_ui + loop.ki_ui)
            den[-1] -= k * loop.kp_ui
            poles = np.roots(den)
            if np.any(np.abs(poles) >= 1.0):
                return None
            c = (k * loop.kp_ui - k * (loop.kp_ui + loop.ki_ui) * poles) / np.polyval(np.polyder(den), poles)
        zi = np.exp(-1j * TWOPI * f[inside] / loop.f_update)[:, None]
        q = poles[None, :] * zi
        d = c[None, :] * zi / (1.0 - q)
        return inside, 1.0 + np.sum(d, axis=1), d, q

    def run_mean_sq(self, f: np.ndarray, first_update: int, last_update: int) -> np.ndarray:
        """Mean of ``|G_n(f)|^2`` over updates ``first_update..last_update`` of a
        run that starts from rest: a unit sinusoid switched on with the run
        leaves ``|G_n|`` on the sampler at update ``n``.

        ``E(z) = 1 + sum_i c_i / (z - p_i)`` over the loop's poles, so
        ``G_n = E - sum_i d_i q_i^n`` with ``q_i = p_i e^{-jw}`` and
        ``d_i = c_i e^{-jw} / (1 - q_i)``; the mean of its square over the
        window is a sum of geometric series. Tends to ``|E|^2`` once the
        window starts long after the loop's ring-down; 1 above
        ``f_update / 2`` (nothing tracked), and ``|E|^2`` if the loop is not
        stable (no run settles then).
        """
        f = np.asarray(f, dtype=np.float64)
        tr = self._transient(f)
        if tr is None:
            return np.where((f > 0) & (f <= self.loop.f_update / 2.0), self(f) ** 2, 1.0)
        inside, e, d, q = tr
        n0, w = int(first_update), max(int(last_update) - int(first_update) + 1, 1)

        def mean_pow(r: np.ndarray) -> np.ndarray:
            """mean of r**n over n0 .. n0 + w - 1."""
            near = np.abs(1.0 - r) < 1e-14
            safe = np.where(near, 0.5, r)
            return np.where(near, 1.0 + 0j, safe ** n0 * (1.0 - safe ** w) / ((1.0 - safe) * w))

        cross = np.sum(d * mean_pow(q), axis=1)
        quad = np.sum(d[:, :, None] * np.conj(d)[:, None, :]
                      * mean_pow(q[:, :, None] * np.conj(q)[:, None, :]), axis=(1, 2))
        out = np.ones_like(f)
        out[inside] = np.maximum(np.real(np.abs(e) ** 2 - 2.0 * np.conj(e) * cross + quad), 0.0)
        return out

    def run_sq_at(self, f: np.ndarray, updates: np.ndarray) -> np.ndarray:
        """``|G_n(f)|^2`` at each of ``updates`` (shape ``(f.size, updates.size)``):
        how the run's error grows as an underdamped loop rings up."""
        f = np.asarray(f, dtype=np.float64)
        n = np.asarray(updates, dtype=np.float64)
        tr = self._transient(f)
        if tr is None:
            base = np.where((f > 0) & (f <= self.loop.f_update / 2.0), self(f) ** 2, 1.0)
            return np.repeat(base[:, None], n.size, axis=1)
        inside, e, d, q = tr
        out = np.ones((f.size, n.size))
        g = e[:, None] - np.sum(d[:, :, None] * q[:, :, None] ** n[None, None, :], axis=1)
        out[inside] = np.abs(g) ** 2
        return out


def tracking_response(f: np.ndarray, loop: LoopParams, k_pd: float) -> np.ndarray:
    """|H(f)|: the fraction of input jitter at ``f`` the loop follows -- and,
    over ``k_pd``, how its detector's noise reaches the sampler (with an
    adaptive FFE, the equalised samples)."""
    if loop.ffe_track > 0:
        return _on_circle(f, loop, k_pd, "h")
    gain = loop_gain(f, loop, k_pd)
    return np.abs(gain / (1.0 + gain))


def bandwidth_hz(loop: LoopParams, k_pd: float) -> float:
    """Frequency where |E(f)| first reaches 1/sqrt(2): the -3 dB tracking bandwidth."""
    f = _grid(loop)
    e = error_response(f, loop, k_pd)
    hit = np.flatnonzero(e >= 1.0 / np.sqrt(2.0))
    return float(f[hit[0]]) if hit.size else float(loop.f_update / 2.0)


@dataclass(frozen=True)
class PdNoise:
    """A phase detector's noise ``q`` as the loop sees it, per update.

    ``white_var`` is a flat density [PD units^2 per update]; ``line_freq``
    (fractions of ``f_update`` in (0, 1/2]) and ``line_power`` (one-sided
    [PD units^2]) are the lines a repeating pattern puts on top of it. A loop
    follows a line only as far as ``|H|`` reaches it, so a pattern that
    repeats faster than the loop's bandwidth adds nothing a loop can see,
    however large its per-symbol variance.
    """

    white_var: float
    line_freq: np.ndarray = field(default_factory=lambda: np.zeros(0))
    line_power: np.ndarray = field(default_factory=lambda: np.zeros(0))


def self_noise_ui(loop: LoopParams, k_pd: float, pd_noise: float | PdNoise) -> float:
    """RMS phase [UI] the loop acquires by following its own detector noise.

    White ``q`` of variance ``var`` per update, through ``H / k_pd``:
    ``sigma^2 = var / k_pd^2 * mean |H|^2`` over the loop's band -- the
    discrete-time noise-bandwidth integral, done on a log grid because |H|^2
    is a low-pass occupying a sliver of the band. A :class:`PdNoise` adds
    its lines, each through ``|H|^2 / k_pd^2`` at its own frequency. A bare
    float is the white variance.
    """
    noise = pd_noise if isinstance(pd_noise, PdNoise) else PdNoise(float(pd_noise))
    if k_pd <= 0:
        return 0.0
    var = 0.0
    if noise.white_var > 0:
        f = _grid(loop)
        h2 = tracking_response(f, loop, k_pd) ** 2
        var += noise.white_var * float(_trapezoid(h2, f) / (loop.f_update / 2.0))
    if noise.line_power.size:
        h = tracking_response(np.asarray(noise.line_freq) * loop.f_update, loop, k_pd)
        var += float(np.sum(h ** 2 * noise.line_power))
    return float(np.sqrt(var) / k_pd)


def bb_pd_gain(sigma_e_ui: float, rho: float = 0.5) -> float:
    """Linearised bang-bang PD gain [1/UI] for Gaussian input jitter ``sigma_e_ui``."""
    return rho * np.sqrt(2.0 / np.pi) / max(sigma_e_ui, 1e-12)


def bb_pd_noise_var(rho: float = 0.5) -> float:
    """Variance of the bang-bang PD output around its linear part.

    ``E[pd^2] = rho`` (a transition gives +-1 either way) less the power of the
    mean response: for Gaussian error ``E[erf^2(e / sqrt(2) sigma_e)] = 1/3``
    exactly, so ``var(q) = rho - rho^2 / 3``. Under a pure limit cycle the
    remainder is also anti-correlated from update to update rather than white,
    which this does not capture: on a quiet clock the model's hunting comes
    out some 20-30% above the kernel's (measured 0.6-0.7 kp against 0.76 kp).
    """
    return rho - rho ** 2 / 3.0


def loop_damping(loop: LoopParams, k_pd: float) -> float | None:
    """Damping ratio of the proportional-integral loop linearised at gain
    ``k_pd``: ``kp sqrt(k_pd / ki) / 2`` (the open loop ``k_pd (kp + ki / s)
    / s`` in updates has ``w_n^2 = k_pd ki`` and ``2 zeta w_n = k_pd kp``).
    None without an integral path. A bang-bang loop's gain falls as its
    error grows, and with it the damping, so an underdamped loop rings up
    until it slips: the mixed-signal kernel on the NRZ 16G preset held at
    0.34 (kp_shift 7: 0.057 UI RMS, 1.1x the model) and slipped at 0.17
    (kp_shift 8: 0.46 UI RMS, 1.15 UI of drift over 200k symbols, BER 2.2x
    the model's 3.9e-4; ROADMAP P2 #32)."""
    if loop.ki_ui <= 0.0 or k_pd <= 0.0:
        return None
    return float(loop.kp_ui * np.sqrt(k_pd / loop.ki_ui) / 2.0)


def bb_stationary_density(u: np.ndarray, mean_curve: np.ndarray, sigma: float,
                          n_iter: int = 60) -> np.ndarray:
    """Stationary density of a bang-bang loop's sampling phase on the grid
    ``u`` (uniform; samples or UI, as the caller likes) under a detector whose
    mean output at offset ``u`` is ``mean_curve`` -- positive early (the loop
    moves later), negative late -- with RMS ``sigma`` in the grid's unit.

    A loop far slower than the symbol rate sees the detector as a drift
    ``kp M(u)`` plus white noise, so its phase diffuses in the potential
    ``-int M`` and settles to the Boltzmann density ``exp(int_0^u M / T)``.
    The linearised model (:func:`bb_loop_fixed_point`) already gives the
    RMS, and its gain is the statistical linearisation -- the slope of ``M``
    under the error's own distribution, not at the origin -- so the
    temperature ``T`` is set (bisected) to make the density's RMS about its
    mean equal ``sigma``; the curve sets the shape only. Where ``M`` keeps
    growing that is the Gaussian; where the detector saturates (every
    transition already voting one way, 0.2 UI out on the NRZ 16G preset) the
    potential turns linear and the tails exponential, heavier than the
    Gaussian's and asymmetric where the pulse's two sides differ: the kernel's
    late tail at 0.23 UI was 3-7x a Gaussian of its own RMS, and under a
    steep bathtub that read the statistical BER 0.25-0.84x the time engine's
    with a Gaussian smear, 0.4-0.9x with this density (ROADMAP P2 #32).
    Beyond the point where the potential stops falling (the detector's pull
    reverses, half a UI out) the density is zero: a loop there slips, which
    no stationary picture holds. Returns ``p`` with ``sum(p) du = 1``; a
    curve with no restoring slope, or ``sigma`` 0, returns the Gaussian of
    that ``sigma`` (a delta on the nearest grid point at 0).
    """
    u = np.asarray(u, dtype=np.float64)
    m = np.asarray(mean_curve, dtype=np.float64)
    du = float(u[1] - u[0]) if u.size > 1 else 1.0
    i0 = int(np.argmin(np.abs(u)))

    def gaussian() -> np.ndarray:
        if sigma <= 0.0:
            p = np.zeros_like(u)
            p[i0] = 1.0 / du
            return p
        g = np.exp(-0.5 * (u / sigma) ** 2)
        return g / (g.sum() * du)

    if sigma <= 0.0 or u.size < 3 or m.shape != u.shape:
        return gaussian()
    pot = np.concatenate(([0.0], np.cumsum(0.5 * (m[1:] + m[:-1]) * du)))
    pot -= pot[i0]
    # the well around the mode: outward from it the potential must fall;
    # where it rises again the pull has reversed and the density ends
    imax = int(np.argmax(pot))
    rise = np.flatnonzero(np.diff(pot[imax:]) > 1e-12)
    hi_cut = imax + int(rise[0]) + 1 if rise.size else u.size
    rise = np.flatnonzero(np.diff(pot[:imax + 1][::-1]) > 1e-12)
    lo_cut = imax - int(rise[0]) if rise.size else 0
    well = np.zeros(u.size, dtype=bool)
    well[lo_cut:hi_cut] = True
    drop = pot[imax] - float(pot[well].min())
    if drop <= 1e-12 or well.sum() < 3:
        return gaussian()
    e_pot = np.where(well, pot - pot[imax], -np.inf)

    def density(temp: float) -> np.ndarray:
        p = np.exp(e_pot / temp)
        return p / (p.sum() * du)

    def rms(p: np.ndarray) -> float:
        mean = float(np.sum(p * u) * du)
        return float(np.sqrt(np.sum(p * (u - mean) ** 2) * du))

    # hotter is wider; bisect the temperature in log between a near-delta
    # and a near-uniform fill of the well
    lo, hi = 1e-4 * drop, 1e2 * drop
    if rms(density(hi)) <= sigma:
        return density(hi)          # the well is too narrow for this RMS
    if rms(density(lo)) >= sigma:
        return density(lo)
    for _ in range(n_iter):
        mid = float(np.sqrt(lo * hi))
        if rms(density(mid)) > sigma:
            hi = mid
        else:
            lo = mid
    return density(float(np.sqrt(lo * hi)))


def bb_data_noise(mean_out: np.ndarray, period: np.ndarray | None = None) -> tuple[float, PdNoise]:
    """What a loop sees of a bang-bang detector's data-dependent mean output.

    ``mean_out[i, j]``: the detector's mean output at the lock point when a
    symbol at level ``i`` is followed by one at level ``j`` (0 where the sign
    does not change). PAM4's sign changes between unequal magnitudes cross
    zero away from the lock, so their output is all but set by the data: +1
    from the larger magnitude to the smaller, -1 back. A white model counts
    its whole power, ``mean(mean_out^2)`` per symbol; but consecutive
    transitions share a symbol, and what a loop far slower than the symbol
    rate integrates is the sequence's density at low frequency, its variance
    plus twice its lag-1 covariance on independent equiprobable levels --
    0.125 against 0.25 on PAM4. A repeating pattern (``period``: one period
    of the level indices) puts it on lines at multiples of the repetition
    rate instead, nothing at zero frequency (the transitions over a period
    balance). Returns the power a white model counts and the noise the loop
    sees, per update of one symbol (ROADMAP P2 #30).
    """
    m = np.asarray(mean_out, dtype=np.float64)
    counted = float(np.mean(m ** 2))
    if period is None:
        mean = float(np.mean(m))
        var = counted - mean ** 2
        # E[m(a0, a1) | a1] and E[m(a1, a2) | a1]: a0 and a2 are independent given a1
        cov = float(np.mean(m.mean(axis=0) * m.mean(axis=1))) - mean ** 2
        return counted, PdNoise(max(var + 2.0 * cov, 0.0))
    idx = np.asarray(period, dtype=np.int64)
    g = m[np.roll(idx, 1), idx]          # circular: the period's first symbol follows its last
    n = g.size
    spec = np.fft.rfft(g - g.mean())
    power = 2.0 * np.abs(spec) ** 2 / n ** 2
    if n % 2 == 0:
        power[-1] /= 2.0                 # the Nyquist bin is its own mirror
    return counted, PdNoise(0.0, np.arange(spec.size)[1:] / n, power[1:])


_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(16)   # E over a standard normal
_GH_W = _GH_W / _GH_W.sum()


@dataclass(frozen=True)
class BbEdgeStats:
    """An Alexander detector's edge samples over a symbol sequence: what its
    gain and its noise are, without taking the edge's ISI for a Gaussian.

    ``u[k]``: the edge voltage at transition ``k``, signed toward the old
    symbol (the detector reads early, +1, when it is positive), its ISI and
    the main cursors' offset from the lock included; ``du[k]`` its slope per
    UI of sampling delay; ``at[k]`` the symbol it ends at (from the first one counted). The edge's ISI is
    a sum of a few large cursors: on the NRZ 16G / PAM4 32G presets its
    density at zero is 0.85-0.88x a Gaussian's of the same variance, and the
    gain the Gaussian gave read 1.22 / 1.07x the kernel's frozen slope.
    And the ISI is data, not noise: consecutive edges share symbols, so the
    detector's output at low frequency -- what a slow loop integrates -- is
    0.6-0.8x its variance in the kernel, which a white model counts in full.
    The two errors had cancelled in the hunting (ROADMAP P2 #38, 2026-10-31).

    ``periodic``: the sequence is one period of a repeating pattern, whose
    data noise the loop sees as lines; otherwise independent symbols, whose
    data noise is the density at zero frequency (autocovariance summed over
    the ISI span). ``noise_sigma`` is the AWGN at the edge sample [V],
    ``edge_ui`` the transmitter's white edge jitter.
    """

    u: np.ndarray
    du: np.ndarray
    at: np.ndarray
    n_symbols: int
    noise_sigma: float
    edge_ui: float
    periodic: bool
    span: int

    @staticmethod
    def build(pulse_y: np.ndarray, osr: int, lock_pos: float, half_span: int,
              amps: np.ndarray, noise_sigma: float, edge_ui: float = 0.0,
              periodic: bool = False, n_pre: int = 24, n_post: int = 64,
              dcd_ui: float = 0.0) -> "BbEdgeStats":
        """From the pre-DFE pulse, the lock (fractional sample of the data
        sample), and the symbols' amplitudes [V]. ``dcd_ui``: the
        transmitter's duty-cycle distortion, a rising edge late and a falling
        one early by half of it -- the edge sample of that transition moves
        the other way along its slope."""
        y = np.asarray(pulse_y, dtype=np.float64)
        dy = np.gradient(y)
        grid = np.arange(y.size, dtype=np.float64)
        pos = lock_pos - half_span + np.arange(-n_pre, n_post + 1) * osr
        c = np.interp(pos, grid, y, left=0.0, right=0.0)
        dc = np.interp(pos, grid, dy, left=0.0, right=0.0) * osr     # per UI
        a = np.asarray(amps, dtype=np.float64)
        n = a.size
        if periodic:
            ext = a[np.arange(-n_post, n + n_pre) % n]                # circular
            v = np.convolve(ext, c)[n_pre + n_post: n_pre + n_post + n]
            dv = np.convolve(ext, dc)[n_pre + n_post: n_pre + n_post + n]
            prev = np.roll(a, 1)
            keep = np.ones(n, dtype=bool)
            first = 0
        else:
            v = np.convolve(a, c)[n_pre: n_pre + n]
            dv = np.convolve(a, dc)[n_pre: n_pre + n]
            prev = np.concatenate([[0.0], a[:-1]])
            k = np.arange(n)
            keep = (k >= n_post) & (k < n - n_pre)                  # whole ISI in view
            first = n_post
        trans = keep & (np.sign(a) != np.sign(prev)) & (prev != 0.0)
        sg = np.sign(prev[trans])
        if dcd_ui > 0.0:
            # an edge later by tau reads like sampling earlier by tau
            v = v.copy()
            v[trans] -= dv[trans] * (dcd_ui / 2.0) * np.sign(a[trans] - prev[trans])
        return BbEdgeStats(u=v[trans] * sg, du=dv[trans] * sg,
                           at=np.flatnonzero(trans) - first, n_symbols=int(keep.sum()),
                           noise_sigma=float(noise_sigma), edge_ui=float(edge_ui),
                           periodic=bool(periodic), span=int(n_pre + n_post + 1))

    @property
    def rho(self) -> float:
        return self.u.size / max(self.n_symbols, 1)

    def mean_output(self, sigma_e: float = 0.0) -> float:
        """The detector's mean output per symbol: zero where the loop locks
        (+ early, the edges reading the old symbol)."""
        from scipy.special import erf

        m = erf(self.u / (np.sqrt(2.0) * self._s(sigma_e)))
        return float(np.sum(m) / max(self.n_symbols, 1))

    def _s(self, sigma_e: float) -> np.ndarray:
        return np.sqrt(self.noise_sigma ** 2 + self.du ** 2 * (sigma_e ** 2 + self.edge_ui ** 2)) + 1e-300

    def gain(self, sigma_e: float) -> float:
        """Mean output per symbol per UI of delay at loop jitter ``sigma_e``
        [UI]: the edges' density at zero times their slope, exactly over
        the sequence (``bb_pd_gain`` for a Gaussian edge)."""
        s = self._s(sigma_e)
        dens = np.exp(-0.5 * (self.u / s) ** 2) / (np.sqrt(2.0 * np.pi) * s)
        return float(np.sum(-2.0 * self.du * dens) / max(self.n_symbols, 1))

    def gain_at(self, x: np.ndarray, sigma_e: float) -> np.ndarray:
        """The local slope of the mean output per symbol per UI at offsets
        ``x`` [UI] from the lock, the loop's jitter ``sigma_e`` folded in."""
        x = np.atleast_1d(np.asarray(x, dtype=np.float64))
        s = self._s(sigma_e)
        z = (self.u[None, :] + self.du[None, :] * x[:, None]) / s[None, :]
        dens = np.exp(-0.5 * z * z) / (np.sqrt(2.0 * np.pi) * s[None, :])
        return np.sum(-2.0 * self.du[None, :] * dens, axis=1) / max(self.n_symbols, 1)

    def tone_gains(self, amp_ui: float, sigma_e: float, n_theta: int = 48) -> tuple[float, float]:
        """Under a tone the loop leaves ``amp_ui`` of: the gain the tone itself
        meets (the describing function, its fundamental over the amplitude)
        and the slope the loop's own noise meets on average over its swing.
        Both are ``gain(sigma_e)`` as the tone goes to zero."""
        if amp_ui <= 1e-9:
            g = self.gain(sigma_e)
            return g, g
        from scipy.special import erf

        th = (np.arange(n_theta) + 0.5) * (2.0 * np.pi / n_theta)
        x = amp_ui * np.sin(th)
        s = self._s(sigma_e)
        m = np.sum(erf((self.u[None, :] + self.du[None, :] * x[:, None]) / (np.sqrt(2.0) * s[None, :])),
                   axis=1) / max(self.n_symbols, 1)
        describing = float(-2.0 * np.mean(m * np.sin(th)) / amp_ui)
        return describing, float(np.mean(self.gain_at(x, sigma_e)))

    def noise(self, sigma_e: float) -> PdNoise:
        """The output's noise per update as the loop sees it, at each phase
        the loop's own jitter puts it (Gauss-Hermite over ``sigma_e``): the
        part the AWGN and the edge jitter randomise (white), and the part
        the data set, as lines on a repeating pattern or its density at
        zero frequency on random data. The sequence's mean at that phase is
        the response to it -- the signal, not noise."""
        from scipy.special import erf

        s0 = np.sqrt(self.noise_sigma ** 2 + self.du ** 2 * self.edge_ui ** 2) + 1e-300
        nn = max(self.n_symbols, 1)
        nodes = ((0.0, 1.0),) if sigma_e <= 0.0 else tuple(zip(_GH_X * sigma_e, _GH_W))
        white = 0.0
        power = 0.0
        for t, w in nodes:
            m = erf((self.u + self.du * t) / (np.sqrt(2.0) * s0))
            white += w * float(np.sum(1.0 - m ** 2)) / nn
            g = np.zeros(nn)
            g[self.at] = m
            g -= g.mean()
            if self.periodic:
                spec = np.fft.rfft(g)
                p = 2.0 * np.abs(spec) ** 2 / nn ** 2
                if nn % 2 == 0:
                    p[-1] /= 2.0
                power = power + w * p[1:]
            else:
                f = np.fft.rfft(g, 2 * nn)
                acov = np.fft.irfft(np.abs(f) ** 2)[: self.span + 2] / nn
                power += w * float(acov[0] + 2.0 * np.sum(acov[1:]))
        if self.periodic:
            return PdNoise(white, np.arange(1, np.size(power) + 1) / nn, power)
        return PdNoise(white + max(power, 0.0))


def bb_loop_fixed_point(loop: LoopParams,
                        untracked_ui: Callable[[Callable[[np.ndarray], np.ndarray]], float],
                        rho: float = 0.5, sigma_edge_ui: float = 0.0,
                        n_iter: int = 60,
                        transitions: Sequence[tuple[float, float, float]] | None = None,
                        mean_output: Callable[[float], np.ndarray] | None = None,
                        period: np.ndarray | None = None, voters: int = 1,
                        edge: BbEdgeStats | None = None,
                        tones: Sequence[tuple[float, float]] = ()) -> LoopSolution:
    """Self-consistent operating point of a bang-bang loop.

    ``untracked_ui(err_fn)`` must return the RMS clock jitter [UI] left when
    ``err_fn(f)`` is the loop's error response (an :class:`ErrorResponse`,
    which can also say what a run from rest leaves); the caller supplies it
    so this module knows nothing about profiles. ``sigma_edge_ui`` is whatever else
    randomises the edge comparison -- AWGN and ISI at the edge sample,
    expressed in UI through the edge slope -- and enters the detector's
    effective input jitter but not the sampling error it reports (the data
    sampler sees the clock, not the edge sample's noise). Iterates on
    ``sigma_e^2 = untracked^2 + self^2 + edge^2``; converges in a handful of
    steps (a quieter input raises the gain, which widens the loop, which
    quiets the input less than proportionally).

    ``transitions``: the detector's transitions by class, ``(probability
    per symbol, where the class crosses zero relative to the lock point [UI],
    its edge noise [UI])``, in place of one class ``(rho, 0, sigma_edge_ui)``.
    A class crossing ``d`` away adds ``p sqrt(2/pi) / s exp(-d^2 / 2 s^2)`` to
    the gain (``s`` its edge noise and the loop's error together) and its
    whole ``p`` to the output's power: PAM4's sign changes between unequal
    magnitudes (+-1 <-> -+1/3) cross 0.1 UI off the lock and carry almost no
    timing, only noise -- taken at the full ``rho`` the gain was 2x and the
    hunting 0.65-0.72x the kernel's (ROADMAP P2 #27).

    ``mean_output(sigma_e)``: the detector's mean output per pair of levels
    (:func:`bb_data_noise`), whose power the white noise ``rho - info^2 / 3``
    counts in full; it is replaced by what the loop sees of it -- its density
    at low frequency on random data, ``period``'s lines on a repeating
    pattern. Counted white, PAM4's hunting read 1.11-1.21x the time engine's
    (ROADMAP P2 #30).

    ``voters``: lanes whose detectors the loop averages (one loop listening
    to N lanes alike, each its own data and noise): the mean output and the
    gain are one lane's, the output's noise power 1/N of it. On NRZ 16G at
    15 mV the time engine's hunting went 1.92 / 1.33 / 0.92 / 0.65 samples
    RMS on 1 / 2 / 4 / 8 such lanes (ROADMAP P3 #9c).

    ``edge``: the detector's edge samples over the sequence
    (:class:`BbEdgeStats`), whose gain and noise replace the classes' and
    the mean output's -- both read off the same samples, so neither is taken
    Gaussian or white while the other is not (ROADMAP P2 #38).

    ``tones``: sinusoidal clock jitter, ``(amplitude [UI], frequency [Hz])``.
    The loop leaves ``A |E(f)|`` of each, a swing the detector sees on top
    of its noise: it flattens the detector, so the gain the tone meets (the
    describing function, ``BbEdgeStats.tone_gains``) and the one the loop's
    noise meets (the slope averaged over the swing) both fall, which widens
    what is left of the tone -- solved together with the rest. The first
    tone is taken exactly, any others as Gaussian jitter of their RMS; on the
    classes (no ``edge``) every tone is taken that way. What is left of each
    is in ``LoopSolution.sj_residual``.
    """
    classes = ((rho, 0.0, sigma_edge_ui),) if transitions is None else tuple(transitions)
    rho_all = sum(p for p, _, _ in classes)

    tones = tuple((float(a), float(f)) for a, f in tones if a > 0.0 and f > 0.0)
    resid = [a for a, _ in tones]          # what the loop leaves of each, refined below

    def with_tones(sigma_e: float) -> tuple[float, float]:
        """The input jitter less the first tone (taken exactly) and that tone's residual."""
        if not tones:
            return sigma_e, 0.0
        if edge is None:
            return float(np.sqrt(sigma_e ** 2 + sum(a * a / 2.0 for a in resid))), 0.0
        rest = sum(a * a / 2.0 for a in resid[1:])
        return float(np.sqrt(sigma_e ** 2 + rest)), resid[0]

    def gain(sigma_e: float) -> tuple[float, float]:
        """The linearised gain and the classes' timing share at input jitter sigma_e."""
        sigma_e, a0 = with_tones(sigma_e)
        if edge is not None:
            return (edge.tone_gains(a0, sigma_e)[1] if a0 > 0.0 else edge.gain(sigma_e)), 0.0
        k = info = 0.0
        for p, offset, edge_ui in classes:
            s = float(np.hypot(sigma_e, edge_ui))
            w = float(np.exp(-0.5 * (offset / max(s, 1e-12)) ** 2))
            k += bb_pd_gain(s, p) * w
            info += p * w
        return k, info

    sigma_e = max(untracked_ui(lambda f: np.ones_like(np.asarray(f, dtype=np.float64))),
                  loop.kp_ui)
    k_pd, info = gain(sigma_e)
    sig_u = sig_s = 0.0
    def pd_noise(sigma_e: float, info: float) -> float | PdNoise:
        if edge is not None:
            # the tones swing the detector across its edges too, which moves its
            # noise: taken as Gaussian jitter of their RMS here
            return edge.noise(float(np.sqrt(sigma_e ** 2 + sum(a * a / 2.0 for a in resid))))
        # E[pd^2] less the power of the mean response (bb_pd_noise_var)
        white = rho_all - info ** 2 / 3.0
        if mean_output is None:
            return white
        counted, seen = bb_data_noise(mean_output(sigma_e), period)
        return PdNoise(max(white - counted, 0.0) + seen.white_var, seen.line_freq, seen.line_power)

    def averaged(noise: float | PdNoise) -> float | PdNoise:
        if voters <= 1:
            return noise
        if isinstance(noise, PdNoise):
            return PdNoise(noise.white_var / voters, noise.line_freq,
                           np.asarray(noise.line_power) / voters)
        return noise / voters

    def update_tones(sigma_e: float) -> None:
        """What the loop leaves of each tone, at the gain that tone meets."""
        for i, (amp, f) in enumerate(tones):
            for _ in range(40):
                if i == 0 and edge is not None:
                    sig, _ = with_tones(sigma_e)
                    k_tone = edge.tone_gains(resid[0], sig)[0]
                else:
                    k_tone = k_pd
                new_a = amp * float(error_response(np.array([f]), loop, max(k_tone, 0.0))[0])
                done = abs(new_a - resid[i]) <= 1e-7 + 1e-5 * amp
                resid[i] = 0.5 * (resid[i] + new_a)
                if done:
                    break

    for _ in range(n_iter):
        sig_u = untracked_ui(ErrorResponse(loop, k_pd))
        sig_s = self_noise_ui(loop, k_pd, averaged(pd_noise(sigma_e, info)))
        new = float(np.hypot(sig_u, sig_s))
        if tones:
            update_tones(new)
        k_new, info = gain(new)
        converged = abs(k_new - k_pd) <= 1e-6 * k_pd
        k_pd, sigma_e = k_new, new
        if converged:
            break
    return LoopSolution(k_pd=float(k_pd), sigma_untracked_ui=float(sig_u),
                        sigma_self_ui=float(sig_s), bandwidth_hz=bandwidth_hz(loop, k_pd),
                        damping=loop_damping(loop, k_pd),
                        sj_residual=tuple((float(a), f) for a, (_, f) in zip(resid, tones)))


def mm_loop_solution(loop: LoopParams, k_pd: float, pd_noise: float | PdNoise,
                     untracked_ui: Callable[[Callable[[np.ndarray], np.ndarray]], float],
                     tones: Sequence[tuple[float, float]] = ()) -> LoopSolution:
    """Operating point of a Mueller-Muller loop: linear, so no iteration;
    each tone ``(amplitude [UI], frequency [Hz])`` is left at ``A |E(f)|``."""
    sig_u = untracked_ui(ErrorResponse(loop, k_pd))
    sig_s = self_noise_ui(loop, k_pd, pd_noise)
    resid = tuple((float(a * error_response(np.array([f]), loop, k_pd)[0]), float(f))
                  for a, f in tones if a > 0.0 and f > 0.0)
    return LoopSolution(k_pd=float(k_pd), sigma_untracked_ui=float(sig_u),
                        sigma_self_ui=float(sig_s), bandwidth_hz=bandwidth_hz(loop, k_pd),
                        damping=loop_damping(loop, k_pd), sj_residual=resid)


def lock_offset_samples(pulse_y: np.ndarray, peak_idx: int, osr: int, half_span: int) -> float:
    """Where a timing function ``g(tau) = h(tau - half_span) - h(tau + half_span)``
    crosses zero nearest the pulse peak, in samples relative to ``peak_idx``.

    Both detectors lock where their two taps see equal pulse amplitude --
    half a UI apart for the Alexander edge sample, a UI either side of the
    data sample for Mueller-Muller -- and on a real pulse that is not the
    peak: an NRZ pulse through a short channel is a plateau whose argmax is
    its leading corner, a lossy one is skewed. Reading gain, slope and ISI at
    the peak instead of here was a factor-of-several error in the first cut.
    Linear interpolation between the two samples straddling the crossing;
    the peak itself if the function never crosses within ``+-osr``.
    """
    y = np.asarray(pulse_y, dtype=np.float64)

    def at(i: int) -> float:
        return float(y[i]) if 0 <= i < y.size else 0.0

    taus = np.arange(-osr, osr + 1)
    g = np.array([at(peak_idx + t - half_span) - at(peak_idx + t + half_span) for t in taus])
    sign_change = np.flatnonzero(np.sign(g[:-1]) * np.sign(g[1:]) < 0)
    if sign_change.size == 0:
        return 0.0
    i = sign_change[np.argmin(np.abs(taus[sign_change]))]
    frac = g[i] / (g[i] - g[i + 1])
    return float(taus[i] + frac)


def mm_pd_statistics(pulse_y: np.ndarray, osr: int, peak_idx: int, levels_v: np.ndarray,
                     noise_sigma: float, n_symbols: int = 1 << 17, n_pre: int = 24,
                     n_post: int = 64, seed: int = 0, n_feedback: int = 0, n_lanes: int = 1,
                     period_levels: np.ndarray | None = None,
                     lock_shift: int = 0) -> tuple[float, PdNoise]:
    """Mueller-Muller PD gain [V/UI] at lock and its noise per loop update.

    ``pd = x[n] (sign x[n+1] - sign x[n-1])`` on the raw samples the PD sees.
    With an open eye the gain is the textbook ``(h(-T) - h(+T)) E|level|``
    slope; with the heavy ISI of a pre-FFE 112 GBd sample it is not even
    close -- the sign of a neighbour correlates with every symbol in its own
    ISI, and the first closed form tried here (slope at the peak times
    E|level|) was 3.7x the measured gain. So the expectations are taken over
    random symbols on the pulse's baud-spaced cursor set at the lock point
    (:func:`lock_offset_samples`), plus AWGN: a seeded Monte-Carlo, repeatable
    to the bit and a fifth of a second of numpy. Validated against the
    received waveform of the PAM4 112G preset: slope 0.0144 vs 0.0148 V/UI.

    ``n_feedback``: the detector reads samples from which decision feedback
    has taken that many postcursors -- an ADC receiver's DFE (and a PR
    target's controlled cursors before it), the kernel's slicer input. The
    feedback is the lock point's postcursors, fixed while the phase moves (the
    taps adapt far slower than the loop), and the loop locks at the pulse
    peak: what is left there has no first postcursor to balance its first
    precursor against. Taking the samples with the DFE's postcursor still in
    them read the gain 1.41x low on example 31's 112 GBd link (ROADMAP P2
    #24); with it, 1.17x high (0.032 vs 0.0276 V/UI measured with a frozen
    loop), and its timing function follows the kernel's over +-3 samples on
    the fingerprint's 53 Gb/s link. ``lock_shift``: where the receiver
    samples, in samples from ``peak_idx`` -- the cursors and the feedback are
    taken there. On example 31's link the loop samples 2 samples before the
    equalised pulse's peak (``stat_loop._lock_position``); read at the peak
    the gain came out 1.12x the kernel's (0.0328 against 0.0292, with the
    detector's mean there 0.002, not 0), read at the lock it is the kernel's
    (2026-10-28).

    The noise: a loop updating every ``n_lanes`` symbols and closing far below
    that rate integrates the output's density at low frequency, not its
    per-symbol variance. The two differ by 5x here, because the sum of
    ``pd`` over a block is, up to its ends, the sum of
    ``x[n] sign x[n+1] - x[n+1] sign x[n]``: neighbours' main cursors cancel
    and what is left is ISI and noise. Taking the variance as white read the
    self-noise 2-3x high against the kernel on random symbols. The noise splits
    in two:

    * the receiver's noise, white, and
    * the data's, which a repeating pattern (``period_levels``: one period
      of the line levels, as the transmitter sends them) concentrates on lines
      at multiples of its repetition rate. On PRBS13Q at 112 GBd the first
      sits at 13.7 MHz, above the loop, and the kernel's self-noise is a
      tenth of the random-data figure. Without a period (PRBS31, a pattern
      too long to matter) the data's share is random data's density.

    The cursor and noise are the samples as given: on equalised samples the
    FFE's gain on the receiver's and the ADC's noise is not applied (2.5 %
    on that gain).
    """
    y = np.asarray(pulse_y, dtype=np.float64)
    ks = np.arange(-n_pre, n_post + 1)
    cursors, fb = _mm_cursor_set(y, osr, peak_idx, n_feedback, n_pre, n_post, lock_shift)

    rng = np.random.default_rng(seed)
    levels_v = np.asarray(levels_v, dtype=np.float64)
    # x[t] = sum_k cursor_k * level[t - k]: lv carries n_post symbols of
    # history and n_pre of lookahead around the n_symbols that are sampled
    lv = levels_v[rng.integers(0, levels_v.size, size=n_symbols + ks.size - 1)]
    noise = rng.normal(scale=noise_sigma, size=n_symbols) if noise_sigma > 0 else np.zeros(n_symbols)

    def samples(levels: np.ndarray, shift: int) -> np.ndarray:
        return np.convolve(levels, cursors(shift) - fb, mode="valid")

    def pd_of(x: np.ndarray) -> np.ndarray:
        return x[1:-1] * (np.sign(x[2:]) - np.sign(x[:-2]))

    k_pd = abs(pd_of(samples(lv, +1) + noise).mean()
               - pd_of(samples(lv, -1) + noise).mean()) / 2.0 * osr

    clean = samples(lv, 0)
    noisy = clean + noise
    span = ks.size + 2               # pairs further apart share no symbol
    noise_s0 = _low_frequency_density(_paired(noisy) - _paired(clean), span)
    if period_levels is None:
        return k_pd, PdNoise(_low_frequency_density(_paired(noisy), span) / n_lanes)
    return k_pd, PdNoise(noise_s0 / n_lanes,
                         *_pattern_lines(np.asarray(period_levels, dtype=np.float64),
                                         cursors(0) - fb, n_pre, n_post, n_lanes))


def mm_hold_range_ui(pulse_y: np.ndarray, osr: int, peak_idx: int, levels_v: np.ndarray,
                     noise_sigma: float, n_feedback: int = 0, n_symbols: int = 1 << 16,
                     n_pre: int = 24, n_post: int = 64, seed: int = 0) -> float:
    """How far from its lock point the Mueller-Muller detector's pull back keeps growing [UI].

    The linear model reads the detector's slope at the lock point
    (:func:`mm_pd_statistics`) and assumes it holds at any error. It holds
    only up to where the timing function -- the mean of ``x[n] (sign x[n+1]
    - sign x[n-1])`` against a timing shift -- turns over; past that the pull
    weakens, and where it crosses zero again the loop is pushed away and,
    its integrator winding up, runs off for good. On the fingerprint's
    53 Gb/s link (ADC, 0.2 m, Tx FIR -0.08 / 0.72 / -0.2) the equaliser
    leaves the function at zero at the lock point and turning over 0.42
    samples (0.026 UI) later, 2e-4 above zero at most -- below what this
    model resolves, so it is the turnover's distance that is reported, not
    whether a lock point exists. The kernel with its loop frozen at each
    offset follows the same function to +-3 samples. The ADC presets turn
    over 0.075-0.15 UI out.

    Swept outward from the lock point a sample at a time on random symbols
    (with the same cursors, lock point and fixed DFE feedback as
    :func:`mm_pd_statistics`; a pattern moves the function by less than
    this resolves), the expectation over the receiver noise taken in closed
    form, ``E sign(x + n) = erf(x / (sqrt(2) noise_sigma))``; the turnover is
    the vertex of the parabola through the last three samples. The nearer
    side counts, capped at half a UI. Zero if the function does not rise
    through the lock point, i.e. the detector pushes away from it.
    """
    y = np.asarray(pulse_y, dtype=np.float64)
    cursors, fb = _mm_cursor_set(y, osr, peak_idx, n_feedback, n_pre, n_post)
    levels_v = np.asarray(levels_v, dtype=np.float64)
    rng = np.random.default_rng(seed)
    lv = levels_v[rng.integers(0, levels_v.size, size=n_symbols + n_pre + n_post)]
    if noise_sigma > 0:
        from scipy.special import erf

        def soft(x: np.ndarray) -> np.ndarray:
            return erf(x / (np.sqrt(2.0) * noise_sigma))
    else:
        soft = np.sign
    f_at: dict[int, float] = {}

    def f(shift: int) -> float:
        if shift not in f_at:
            x = np.convolve(lv, cursors(shift) - fb, mode="valid")
            f_at[shift] = float(np.mean(x[1:-1] * (soft(x[2:]) - soft(x[:-2]))))
        return f_at[shift]

    if f(1) <= f(-1):
        return 0.0
    half = osr // 2
    reach = []
    for step in (1, -1):
        j = 0
        while abs(j) < half and (f(j + step) - f(j)) * step > 0:
            j += step
        if abs(j) >= half:
            reach.append(float(half))
            continue
        a, b, c = f(j - 1), f(j), f(j + 1)
        den = a - 2.0 * b + c
        reach.append(abs(j + (0.5 * (a - c) / den if den != 0.0 else 0.0)))
    return min(reach) / osr


def _mm_cursor_set(y: np.ndarray, osr: int, peak_idx: int, n_feedback: int, n_pre: int,
                   n_post: int, lock_shift: int = 0) -> tuple[Callable[[int], np.ndarray], np.ndarray]:
    """The baud-spaced cursors a Mueller-Muller detector sees around its lock
    point, as a function of a timing shift in samples, and the decision
    feedback taken from them: the lock point's postcursors 1..n_feedback,
    fixed while the phase moves (:func:`mm_pd_statistics`). Without feedback
    the lock point is where ``h(-T) = h(+T)``; with it, where the receiver
    samples (``peak_idx + lock_shift``)."""
    if n_feedback > 0:
        c = peak_idx + int(lock_shift)
    else:
        c = peak_idx + int(round(lock_offset_samples(y, peak_idx, osr, osr)))
    ks = np.arange(-n_pre, n_post + 1)

    def cursors(shift: int) -> np.ndarray:
        idx = c + ks * osr + shift
        ok = (idx >= 0) & (idx < y.size)
        out = np.zeros(idx.size)
        out[ok] = y[idx[ok]]
        return out

    fb = np.zeros(ks.size)
    if n_feedback > 0:
        fb[n_pre + 1: n_pre + 1 + n_feedback] = cursors(0)[n_pre + 1: n_pre + 1 + n_feedback]
    return cursors, fb


def _paired(x: np.ndarray) -> np.ndarray:
    """``x[n] sign x[n+1] - x[n+1] sign x[n]``: the MM output with its
    neighbours' cancelling main cursors taken out. Its sums over a block
    equal ``pd``'s up to the two end terms, so its density at low frequency
    is ``pd``'s, from a far smaller variance."""
    s = np.sign(x)
    return x[:-1] * s[1:] - x[1:] * s[:-1]


def _low_frequency_density(g: np.ndarray, span: int) -> float:
    """Two-sided density at zero frequency [units^2 per sample] of a sequence
    whose autocovariance ends at lag ``span``: the covariances summed."""
    g = g - g.mean()
    n = g.size
    spec = np.fft.rfft(g, 2 * n)
    acov = np.fft.irfft(np.abs(spec) ** 2)[: span + 1] / n
    return float(max(acov[0] + 2.0 * acov[1:].sum(), 0.0))


def _pattern_lines(period_levels: np.ndarray, cursor_set: np.ndarray, n_pre: int, n_post: int,
                   n_lanes: int) -> tuple[np.ndarray, np.ndarray]:
    """Lines of the block-averaged noiseless MM output on a repeating pattern.

    The pattern is repeated until a whole number of blocks covers a whole
    number of periods; over that the update sequence is periodic and its DFT
    is the exact line spectrum, aliasing of the block average included.
    """
    per = period_levels.size
    reps = n_lanes // np.gcd(per, n_lanes)
    lv = np.tile(period_levels, reps)
    n = lv.size
    # circular: the history before sample 0 is the end of the pattern
    ext = np.concatenate([lv[n - n_post:], lv, lv[:n_pre]])
    x = np.convolve(ext, cursor_set, mode="valid")
    s = np.sign(x)
    pd = x * (np.roll(s, -1) - np.roll(s, 1))
    u = pd.reshape(-1, n_lanes).mean(axis=1)
    m = u.size
    spec = np.fft.rfft(u - u.mean())
    freq = np.arange(spec.size) / m
    power = 2.0 * np.abs(spec) ** 2 / m ** 2
    if m % 2 == 0:
        power[-1] /= 2.0                 # the Nyquist bin is its own mirror
    return freq[1:], power[1:]


def _trapezoid(y: np.ndarray, x: np.ndarray) -> float:
    # numpy renamed trapz -> trapezoid in 2.0; spelled out so both work
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(x)))


def _grid(loop: LoopParams, n: int = 4000) -> np.ndarray:
    return np.geomspace(loop.f_update * 1e-7, loop.f_update / 2.0, n)
