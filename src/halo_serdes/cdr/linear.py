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
* **Mueller-Muller** (ADC path) is linear in volts, and its gain and noise
  are the mean slope and the variance of ``x[n] (sign x[n+1] - sign x[n-1])``
  over random data on the pulse's cursor set at the lock point
  (:func:`mm_pd_statistics`); the block average divides the variance by
  ``n_lanes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

TWOPI = 2.0 * np.pi


@dataclass(frozen=True)
class LoopParams:
    """One CDR loop in the units the small-signal model wants."""

    f_update: float          # loop update rate [Hz]
    kp_ui: float             # proportional step per PD unit [UI]
    ki_ui: float             # integral step per PD unit [UI]
    latency_updates: int = 0 # extra pipeline delay in loop updates

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


def error_response(f: np.ndarray, loop: LoopParams, k_pd: float) -> np.ndarray:
    """|E(f)| = |1 - H(f)|: the fraction of input jitter at ``f`` left on the sampler.

    Above ``f_update / 2`` the loop never sees the frequency as distinct and
    the response is 1 (nothing tracked).
    """
    return np.abs(1.0 / (1.0 + loop_gain(f, loop, k_pd)))


def tracking_response(f: np.ndarray, loop: LoopParams, k_pd: float) -> np.ndarray:
    """|H(f)|: the fraction of input jitter at ``f`` the loop follows."""
    gain = loop_gain(f, loop, k_pd)
    return np.abs(gain / (1.0 + gain))


def bandwidth_hz(loop: LoopParams, k_pd: float) -> float:
    """Frequency where |E(f)| first reaches 1/sqrt(2): the -3 dB tracking bandwidth."""
    f = _grid(loop)
    e = error_response(f, loop, k_pd)
    hit = np.flatnonzero(e >= 1.0 / np.sqrt(2.0))
    return float(f[hit[0]]) if hit.size else float(loop.f_update / 2.0)


def self_noise_ui(loop: LoopParams, k_pd: float, pd_noise_var: float) -> float:
    """RMS phase [UI] the loop acquires by following its own detector noise.

    White ``q`` of variance ``pd_noise_var`` per update, through ``H / k_pd``:
    ``sigma^2 = var / k_pd^2 * mean |H|^2`` over the loop's band -- the
    discrete-time noise-bandwidth integral, done on a log grid because |H|^2
    is a low-pass occupying a sliver of the band.
    """
    if pd_noise_var <= 0 or k_pd <= 0:
        return 0.0
    f = _grid(loop)
    h2 = tracking_response(f, loop, k_pd) ** 2
    mean_h2 = float(_trapezoid(h2, f) / (loop.f_update / 2.0))
    return float(np.sqrt(pd_noise_var * mean_h2) / k_pd)


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


def bb_loop_fixed_point(loop: LoopParams,
                        untracked_ui: Callable[[Callable[[np.ndarray], np.ndarray]], float],
                        rho: float = 0.5, sigma_edge_ui: float = 0.0,
                        n_iter: int = 60) -> LoopSolution:
    """Self-consistent operating point of a bang-bang loop.

    ``untracked_ui(err_fn)`` must return the RMS clock jitter [UI] left when
    ``err_fn(f)`` is the loop's error response; the caller supplies it so this
    module knows nothing about profiles. ``sigma_edge_ui`` is whatever else
    randomises the edge comparison -- AWGN and ISI at the edge sample,
    expressed in UI through the edge slope -- and enters the detector's
    effective input jitter but not the sampling error it reports (the data
    sampler sees the clock, not the edge sample's noise). Iterates on
    ``sigma_e^2 = untracked^2 + self^2 + edge^2``; converges in a handful of
    steps (a quieter input raises the gain, which widens the loop, which
    quiets the input less than proportionally).
    """
    sigma_e = max(untracked_ui(lambda f: np.ones_like(np.asarray(f, dtype=np.float64))),
                  loop.kp_ui)
    k_pd = bb_pd_gain(float(np.hypot(sigma_e, sigma_edge_ui)), rho)
    sig_u = sig_s = 0.0
    for _ in range(n_iter):
        sig_u = untracked_ui(lambda f, k=k_pd: error_response(f, loop, k))
        sig_s = self_noise_ui(loop, k_pd, bb_pd_noise_var(rho))
        new = float(np.hypot(sig_u, sig_s))
        k_new = bb_pd_gain(float(np.hypot(new, sigma_edge_ui)), rho)
        converged = abs(k_new - k_pd) <= 1e-6 * k_pd
        k_pd, sigma_e = k_new, new
        if converged:
            break
    return LoopSolution(k_pd=float(k_pd), sigma_untracked_ui=float(sig_u),
                        sigma_self_ui=float(sig_s), bandwidth_hz=bandwidth_hz(loop, k_pd))


def mm_loop_solution(loop: LoopParams, k_pd: float, pd_noise_var: float,
                     untracked_ui: Callable[[Callable[[np.ndarray], np.ndarray]], float]
                     ) -> LoopSolution:
    """Operating point of a Mueller-Muller loop: linear, so no iteration."""
    sig_u = untracked_ui(lambda f: error_response(f, loop, k_pd))
    sig_s = self_noise_ui(loop, k_pd, pd_noise_var)
    return LoopSolution(k_pd=float(k_pd), sigma_untracked_ui=float(sig_u),
                        sigma_self_ui=float(sig_s), bandwidth_hz=bandwidth_hz(loop, k_pd))


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
                     noise_sigma: float, n_symbols: int = 60_000, n_pre: int = 24,
                     n_post: int = 64, seed: int = 0) -> tuple[float, float]:
    """Mueller-Muller PD gain [V/UI] and per-symbol output variance [V^2] at lock.

    ``pd = x[n] (sign x[n+1] - sign x[n-1])`` on the raw samples the PD sees.
    With an open eye this is the textbook ``(h(-T) - h(+T)) E|level|`` slope;
    with the heavy ISI of a pre-FFE 112 GBd sample it is not even close -- the
    sign of a neighbour correlates with every symbol in its own ISI, and the
    first closed form tried here (slope at the peak times E|level|) was 3.7x
    the measured gain, its variance ``2 E[x^2]`` 2.3x the measured. So the two
    expectations are taken over random symbols on the pulse's baud-spaced
    cursor set at the lock point (:func:`lock_offset_samples`), plus AWGN: a
    seeded Monte-Carlo over ``n_symbols`` symbols, repeatable to the bit,
    accurate to about a percent, and a few milliseconds of numpy. Validated
    against the received waveform of the PAM4 112G preset: slope 0.0144 vs
    0.0148 V/UI, variance 0.00322 vs 0.0032 V^2.
    """
    y = np.asarray(pulse_y, dtype=np.float64)
    c = peak_idx + int(round(lock_offset_samples(y, peak_idx, osr, osr)))
    ks = np.arange(-n_pre, n_post + 1)

    def cursors(shift: int) -> np.ndarray:
        idx = c + ks * osr + shift
        ok = (idx >= 0) & (idx < y.size)
        out = np.zeros(idx.size)
        out[ok] = y[idx[ok]]
        return out

    rng = np.random.default_rng(seed)
    levels_v = np.asarray(levels_v, dtype=np.float64)
    pad = n_pre + n_post + 4
    lv = levels_v[rng.integers(0, levels_v.size, size=n_symbols + pad)]
    t = np.arange(n_symbols) + n_post + 2
    # x[t] = sum_k cursor_k * level[t - k]: a sliding window over the symbols
    win = np.stack([lv[t - k] for k in ks], axis=1)          # (n_symbols, n_cursors)
    noise = rng.normal(scale=noise_sigma, size=n_symbols) if noise_sigma > 0 else 0.0

    def pd_mean_var(shift: int) -> tuple[float, float]:
        x = win @ cursors(shift) + noise
        pd = x[1:-1] * (np.sign(x[2:]) - np.sign(x[:-2]))
        return float(pd.mean()), float(pd.var())

    m_plus, _ = pd_mean_var(+1)
    m_minus, _ = pd_mean_var(-1)
    _, var0 = pd_mean_var(0)
    k_pd = abs(m_plus - m_minus) / 2.0 * osr
    return k_pd, var0


def _trapezoid(y: np.ndarray, x: np.ndarray) -> float:
    # numpy renamed trapz -> trapezoid in 2.0; spelled out so both work
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(x)))


def _grid(loop: LoopParams, n: int = 4000) -> np.ndarray:
    return np.geomspace(loop.f_update * 1e-7, loop.f_update / 2.0, n)
