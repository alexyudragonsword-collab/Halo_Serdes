"""The statistical engine's pattern-phase factor (``PatternPhase``).

A free-running bang-bang loop's phase depends on the data it was driven by,
and that data is the data its samples are weakest on: the factor is the
joint read over the phase-independent one, from per-history conditional
phase densities propagated through the loop's votes (``cdr.pattern_chain``).
Part of ``engine.statistical`` (split out 2026-10-30; the names are
importable from there as before).
"""

from __future__ import annotations

from dataclasses import dataclass

import collections
import hashlib

import numpy as np

from ..cdr.linear import lock_offset_samples
from ..core.waveform import Waveform


#: symbols of history the pattern-phase read conditions on (2^n histories)
_PATTERN_HISTORY = 12


@dataclass(frozen=True)
class PatternPhase:
    """How much more a free-running bang-bang loop's errors read than the
    phase-independent bathtub, because the data that drives its phase late
    is the data its samples are weakest on (``_pattern_phase_factor``).

    ``factor``: the joint read over the independent one, what the engine
    multiplies its smeared read by. ``shift``: per history, the loop's mean
    phase given it [samples, late positive]; ``history`` the symbols before
    the one sampled (column ``k - 1`` is symbol ``i - k``, +1 the sampled
    symbol's own level), ``next_same`` whether the symbol after it is too.
    ``read_joint`` / ``read_indep``: the two reads of the model behind the
    factor -- its own Gaussian-residual bathtub, not the engine's.
    """

    factor: float
    shift: np.ndarray
    history: np.ndarray
    next_same: np.ndarray
    read_joint: float
    read_indep: float
    #: the phase grid [samples from the lock] and each class's conditional
    #: density on it (float32, classes x grid), the class mixture being the
    #: stationary density; ``marginal_error`` is how far the propagated
    #: mixture sat from it before that correction (max relative deviation
    #: where the density is above 1e-6 of its peak)
    phase_grid: np.ndarray | None = None
    cond_density: np.ndarray | None = None
    marginal_error: float = 0.0
    #: the votes' own stationary density on ``phase_grid`` (the class
    #: mixture at the chain's fixed point, before the scaling onto p0)
    density: np.ndarray | None = None
    #: the joint over the independent read before the floor at 1, and the
    #: chain's iterations and whether its tails converged within them
    factor_raw: float = 1.0
    steps: int = 0
    converged: bool = True
    #: the chain's own marginal read against the engine's density, without
    #: the classes' correlation (``density`` in place of the class
    #: densities): a lane on another lane's clock (``own_clock=False``)
    factor_marginal: float = 1.0


_PATTERN_PHASE_TRACE: dict | None = None     # probes: a dict here receives the propagation's internals
#: results of ``_pattern_phase_factor`` by a digest of their inputs, most
#: recent last (about 2 MB each: the class densities)
_PATTERN_PHASE_CACHE: "collections.OrderedDict[str, PatternPhase]" = collections.OrderedDict()
_PATTERN_PHASE_CACHE_SIZE = 8


def _pattern_phase_factor(pulse: Waveform, pulse_pd: Waveform, osr: int, lock: float,
                          noise_sigma: float, swing: float, n_dfe: int, u: np.ndarray,
                          density: np.ndarray, kp: float,
                          edge_jitter=None, xtalk_var=None, edge_jitter_ui: float = 0.0,
                          n_hist: int = _PATTERN_HISTORY, step: float = 0.5,
                          n_pre: int = 24, n_post: int = 64, n_steps: int = 600,
                          tol: float = 1e-3, onto_p0: bool | None = None, trust: float = 1e-3,
                          ki: float = 0.0, second_order: bool = True,
                          kernel="auto") -> PatternPhase:
    """The pattern-phase factor of a bang-bang loop on NRZ with a held DFE.

    The smear reads the bathtub as if the sampling phase and the data were
    independent. They are not: the loop's phase at symbol ``i`` is the sum
    of its last votes (``kp`` samples each), and a vote is the sign of an
    edge sample whose ISI the symbols around that edge set -- a long run of
    the opposite level before a short run delays the first crossing out of
    it, so those edges vote late; the same run's postcursors beyond the
    DFE's taps pull the short run's data samples toward the opposite level,
    so the last symbol of the short run, sampled late in front of its own
    transition, is where the free-running loop errs. On the NRZ 16G preset
    at 12 mV + 0.03 UI the kernel's errors sat 2.0x above its own phase
    histogram times the held curve, in the symbols ending a run of 2-3
    after a run of 6 or more of the other level (0.5 % of the symbols, a
    third of the errors); binned by pattern class and phase the joint read
    was 2.01x the independent one (1.40x at 15 mV / 0.04 UI, 1.15x at
    18 mV / 0.06 UI), and so was the free-running BER against this engine
    (2.27 / 1.44 / 1.03x; ROADMAP P3 #13).

    Every history of ``n_hist`` symbols before the sampled one (and the
    symbol after it) is taken as one class. Its phase density is the
    loop's own Markov chain at its fixed point: the state is the class,
    the phase and whether the previous data sample's raw sign was its
    symbol's; a step appends a symbol and casts the edge's vote as the
    kernel does -- where the two raw data samples' signs differ, late or
    early by the edge sample's sign against the old one's, each sign a
    Gaussian about its conditioned cursors on the detector's pulse,
    smoothed over the transmitter's edge jitter. The sign tables are the
    kernel's with its loop frozen at +2 to +8 samples within 0.02 in every
    class; the chain is its own Monte Carlo within 10 % per class. Until
    2026-10-25 the class density was the stationary one shifted by the
    class's mean vote bias; that is right to 0.2 samples in the mean but
    light in the tail where it matters (ROADMAP P3 #14).

    ``ki`` > 0: the loop's integral path rides along as a moment closure --
    every state also carries its mass times the integrator's mean, which a
    vote steps by +-``ki``, and each symbol the mass moves by the
    integrator's mean at its phase (over the classes; it remembers further
    back than the window). With it the chain's own marginal is the phase
    on random data and is read as is: against the kernel fed independent
    symbols, RMS within 0.02 samples and P(phase > 6.5 samples) within 5 %
    at 10 / 9 / 8 / 6 / 3 mV, the far late tail (> 8.5) 1.2-2.9x heavy;
    without it the tail beyond 8.5 samples is 50x light at 8 mV. ``ki`` 0
    (or ``onto_p0``): only the chain's class posterior is placed on the
    engine's ``density``, frozen beyond the phases where the chain carries
    ``trust`` of it (the 2026-10-26 P3 #14 form, which matched PRBS31's
    tail by the coincidence of p0's lying between the two patterns').
    ``PatternPhase.density`` is the chain's own marginal, ``marginal_error``
    how far it sits from ``density``.

    Each class's errors at each phase: the data sample's conditioned
    cursors on the receiver's pulse, the DFE's taps held at the lock, the
    transmitter's edge jitter from the engine's own per-phase tables
    (``edge_jitter(offset)``, the own two edges exact) and the rest
    Gaussian, with the engine's error-propagation chain; the model's own
    bathtub is the engine's held curve within 0.87-1.05x at every phase and
    ``read_indep`` is that read, a self-check against the engine's read
    before the factor. ``factor`` is the class-weighted joint read over the
    same classes read with the stationary density (below 1 where that
    density's tails run heavy, see there), 1 where the chain does not
    converge: 1.35 / 1.18 / 1.05x on the three points above and 1.35 /
    1.27 / 1.13 / 0.80 / 0.44x at 10 / 9 / 8 / 6 / 3 mV -- the engine then
    0.92 / 0.95 / 1.05x and 1.05 / 0.93 / 0.88 / 1.09x the kernel's on
    independent symbols (3 mV: no kernel error in 20 seeds, consistent).
    ``second_order``: the integrator's push as Lax-Wendroff fluxes; the
    first-order move diffuses the phase, and the factor read 1.71 / 1.32 /
    1.17 at 8 mV on 0.5 / 0.25 / 0.125-sample grids against 1.13 / 1.08 /
    1.07 with it. ``xtalk_var(pos)``: the
    aggressors' ISI variance [V^2] at absolute waveform positions ``pos``
    (the victim pulse's time origin), added to the data and edge samples'
    Gaussian rest -- crosstalk is noise to both the detector's votes and
    the decisions (NRZ 16G at 12 mV with 2 FEXT + 2 NEXT at -27 / -33 dB:
    the factor 1.26 / 1.36, the engine 1.06 / 1.10x the kernel on
    independent symbols, 0.84 / 0.81x without it). ``step``: the phase grid [samples], rounded to a
    whole fraction of ``kp`` so a vote moves whole bins; ``n_steps`` /
    ``tol``: the chain's iteration budget and its stopping change of the
    marginal, relative to itself wherever the engine's density is above
    1e-6 of its peak (the tails converge last and set the factor).
    """
    from scipy.special import erf

    def ncdf(x):
        return 0.5 * (1.0 + erf(x / np.sqrt(2.0)))

    y = np.asarray(pulse.y, dtype=np.float64)
    peak = int(np.argmax(np.abs(y)))
    grid = np.arange(y.size, dtype=np.float64)

    def at(x):
        return np.interp(x, grid, y, left=0.0, right=0.0)

    y_e = np.asarray(pulse_pd.y, dtype=np.float64)
    peak_e = int(np.argmax(np.abs(y_e)))
    lock_e = lock_offset_samples(y_e, peak_e, osr, osr // 2)
    grid_e = np.arange(y_e.size, dtype=np.float64)

    def at_e(x):
        return np.interp(x, grid_e, y_e, left=0.0, right=0.0)

    u = np.asarray(u, dtype=np.float64)
    # the phase grid: a whole number of bins per vote, so a vote moves the
    # mass by whole bins (no interpolation diffusing it, step by step)
    n_sub = max(1, int(round(kp / step))) if kp > 0.0 else 1
    dus = kp / n_sub if kp > 0.0 else step
    us = np.arange(np.ceil(float(u.min()) / dus), np.floor(float(u.max()) / dus) + 1.0) * dus
    p0 = np.interp(us, u, np.asarray(density, dtype=np.float64))
    p0 = p0 / max(float(p0.sum() * dus), 1e-300)

    # the same inputs come back often (a sweep's repeated points, the API's
    # and the tests' reruns of one config: 84 of the first 110 calls in the
    # test suite) -- the result is a pure function of them, closures read
    # where they are used
    key = None
    if _PATTERN_PHASE_TRACE is None:
        h = hashlib.sha1()
        for arr_in in (pulse.y, pulse_pd.y, u, density, us):
            h.update(np.ascontiguousarray(np.asarray(arr_in, dtype=np.float64)).tobytes())
        h.update(repr((osr, float(lock), float(noise_sigma), float(swing), int(n_dfe), float(kp),
                       float(edge_jitter_ui), n_hist, step, n_pre, n_post, n_steps, tol, onto_p0,
                       trust, float(ki), second_order)).encode())
        if edge_jitter is not None:
            for off in sorted({int(np.rint(lock + uu)) for uu in us}):
                jit = edge_jitter(off)
                if jit is None:
                    h.update(b"none")
                    continue
                for part in (jit.sigma, jit.rest, jit.step, jit.phi, jit.weight):
                    h.update(np.ascontiguousarray(np.asarray(part, dtype=np.float64)).tobytes())
        if xtalk_var is not None:
            pk = int(np.argmax(np.abs(np.asarray(pulse.y))))
            for pos_k in (pk + lock + us, pk + lock + us - osr / 2.0):
                h.update(np.asarray(xtalk_var(pos_k), dtype=np.float64).tobytes())
        key = h.hexdigest()
        if key in _PATTERN_PHASE_CACHE:
            _PATTERN_PHASE_CACHE.move_to_end(key)
            return _PATTERN_PHASE_CACHE[key]

    n = 2 ** n_hist
    hist = 1.0 - 2.0 * ((np.arange(n)[:, None] >> np.arange(n_hist)[None, :]) & 1)
    hist = np.concatenate([hist, hist])
    nxt = np.concatenate([np.ones(n), -np.ones(n)])
    n_cls = hist.shape[0]
    w_cls = 1.0 / n_cls
    ks = np.arange(1, n_hist + 1)
    k_rand = np.concatenate([np.arange(n_hist + 1, n_post + 1), -np.arange(2, n_pre + 1)])
    # --- the data sample at every phase: conditioned cursors, DFE held at the lock, rest Gaussian ---
    pos = peak + lock + us                                              # (n_phase,)
    c_post = at(pos[None, :] + ks[:, None] * osr)                      # (n_hist, n_phase)
    r = c_post.copy()
    if n_dfe > 0:
        m_d = min(n_dfe, n_hist)
        r[:m_d] -= at(peak + lock + ks[:m_d] * osr)[:, None]
    margin = swing * (at(pos)[None, :] + hist @ r + at(pos - osr)[None, :] * nxt[:, None])
    var_rest = noise_sigma ** 2 + swing ** 2 * np.sum(at(pos[None, :] + k_rand[:, None] * osr) ** 2, axis=0)
    if xtalk_var is not None:
        var_rest = var_rest + np.asarray(xtalk_var(pos), dtype=np.float64)
    p_err = np.zeros((n_cls, us.size))
    p_ep = np.zeros((n_cls, us.size))      # P(error | the previous decision was wrong)
    # a wrong previous decision feeds the wrong level back through tap 1: the
    # sample moves by 2 swing h1(lock) either way (the engine's two-state chain)
    step = 2.0 * swing * at(peak + lock + osr) if n_dfe > 0 else 0.0
    kernels: dict = {}
    for iu, uu in enumerate(us):
        off_s = int(np.rint(lock + uu))
        jit = edge_jitter(off_s) if edge_jitter is not None else None
        if jit is None:
            s_tot = np.sqrt(max(var_rest[iu], (swing * 1e-6) ** 2))
            p_err[:, iu] = ncdf(-margin[:, iu] / s_tot)
            p_ep[:, iu] = 0.5 * (ncdf(-(margin[:, iu] - step) / s_tot) + ncdf(-(margin[:, iu] + step) / s_tot))
            continue
        # the own two edges' exact kernel per neighbour pattern, applied on a
        # grid of margins and read back per class (the grid on the noise, or
        # on the jitter itself where the receiver has none)
        dv = (noise_sigma if noise_sigma > 0.0 else max(float(np.max(jit.sigma)), swing * 1e-3)) / 6.0
        for sp in (-1.0, 1.0):
            for sn in (-1.0, 1.0):
                sel = (hist[:, 0] == sp) & (nxt == sn)
                # the tables' bins run (this symbol, the next, the previous):
                # _BINNED_NEIGHBOURS = (-1, 1) are cursor offsets, c(-1) is
                # the symbol after this one. Read the other way round the
                # classes that err most (a transition next, none before) got
                # the weak kernel of the edge behind them, and the model's
                # bathtub sat 2-3.5x under the engine's off the lock
                combo = (1, int(sn > 0), int(sp > 0))
                if (off_s, combo) not in kernels:
                    ker, lo = jit.kernel(combo, dv)
                    kernels[(off_s, combo)] = (ker, (lo + np.arange(ker.size)) * dv)
                ker, x = kernels[(off_s, combo)]
                s_tot = np.sqrt(var_rest[iu] + float(jit.rest[combo]) ** 2)
                m_sel = margin[sel, iu]
                mg = np.linspace(m_sel.min() - step, m_sel.max() + step, 385)
                pg = (ncdf(-(mg[:, None] + x[None, :]) / s_tot) * ker[None, :]).sum(axis=1)
                p_err[sel, iu] = np.interp(m_sel, mg, pg)
                p_ep[sel, iu] = 0.5 * (np.interp(m_sel - step, mg, pg) + np.interp(m_sel + step, mg, pg))
    # DFE error propagation as the engine applies it: the chain over the
    # marginal rates at each phase (ser / (1 - p_ep + ser)), one factor per
    # phase on every class -- per class it overstates the lock point 1.6x
    if n_dfe > 0:
        pe_m = p_err.mean(axis=0)
        ep = 1.0 / np.maximum(1.0 - np.minimum(p_ep.mean(axis=0), 0.99) + pe_m, 1e-9)
        p_err = p_err * ep[None, :]
    # --- the vote at the edge before the sampled symbol, as the kernel casts it ---
    # Edge between symbols i-2 and i-1: a vote where the two *raw* data
    # samples' signs differ (an isolated symbol after a long run of the other
    # level often reads as that level before the DFE and casts none: the
    # kernel's mean vote there was +0.55 against +1.00 for a vote on every
    # true transition), its sign the edge sample's against the old one's --
    # pd = (sgn y_old - sgn y_new) sgn y_edge / 2 with the three samples
    # independent Gaussians about their conditioned means (the receiver's
    # noise is band-limited to the baud, uncorrelated a UI or half a UI
    # apart), smoothed over the transmitter's edge jitter as the marginal
    # curve is. Measured against the kernel's votes with its loop frozen at
    # +2 / +4 / +6 / +8 samples, within 0.02 in every (run before, run after)
    # class and in the mean (-0.319 against -0.315 at +6).
    x_new = peak_e + lock_e + us - osr                                  # symbol i-1's data instant
    js = np.arange(-2, n_hist)                 # symbol i-1-j, from i+1 to i-n_hist
    cols = []
    for j in js:
        q = 1 + j
        cols.append(nxt if q == -1 else np.ones(n_cls) if q == 0 else hist[:, q - 1])
    cols = np.stack(cols, axis=1)                                         # (n_cls, n_j)
    j_rand = np.concatenate([-np.arange(3, n_pre + 1), np.arange(n_hist, n_post + 1)])
    xt_d = np.asarray(xtalk_var(pos), dtype=np.float64) if xtalk_var is not None else 0.0
    xt_e = (np.asarray(xtalk_var(peak + lock + us - osr / 2.0), dtype=np.float64)
            if xtalk_var is not None else 0.0)   # the aggressors half a UI before the data instant
    if edge_jitter_ui > 0.0 and us.size > 1:
        half_k = int(np.ceil(4.0 * edge_jitter_ui * osr / dus))
        k_jit = np.exp(-0.5 * (np.arange(-half_k, half_k + 1) * dus / (edge_jitter_ui * osr)) ** 2)
        k_jit /= k_jit.sum()
    else:
        k_jit = None

    def sign_table(x: np.ndarray, xt) -> np.ndarray:
        """E[sgn y] of the sample at absolute positions ``x`` (one per phase), per class."""
        mu = swing * (cols @ at_e(x[None, :] + ((1 + js) * osr)[:, None]))
        var = noise_sigma ** 2 + swing ** 2 * np.sum(at_e(x[None, :] + ((1 + j_rand) * osr)[:, None]) ** 2, axis=0) + xt
        t = erf(mu / np.sqrt(2.0 * np.maximum(var, (swing * 1e-6) ** 2))[None, :])
        if k_jit is not None:
            from scipy.ndimage import convolve1d
            t = convolve1d(t, k_jit, axis=1, mode="nearest")
        return t.astype(np.float32)

    c_e = sign_table(x_new - osr / 2.0, xt_e)      # the edge sample
    a_new = sign_table(x_new, xt_d)                 # symbol i-1's data sample
    if _PATTERN_PHASE_TRACE is not None:
        _PATTERN_PHASE_TRACE.update(c_e=c_e, a_new=a_new, a_old=sign_table(x_new - osr, xt_d))

    # --- the joint of phase and pattern: the loop's own Markov chain, run to its fixed point ---
    # State: the class (the 12 symbols before the sampled one, and the one
    # after it, all relative to it), the phase, and whether the previous
    # data sample's raw sign was its symbol's -- the kernel reads that sign
    # twice, as the new side of one edge and the old side of the next, so
    # its votes come in pairs: an isolated symbol read right casts both
    # its edges' votes, read wrong neither. Both or neither doubles the
    # variance of the net vote where the two agree (late phases, where
    # both read early) and cancels it where they differ (early phases: the
    # edge into a short run votes late, the edge out of it early) -- the
    # kernel's early tail at 8 mV was 300x lighter than the independent
    # picture's and its late tail heavier. One step appends a symbol
    # (either level, equally) and casts the edge's vote at the phase the
    # mass is at: the class's own probabilities, from the sign tables.
    # The fixed point is the stationary joint of (class, phase), i.e. every
    # class's conditional phase density -- not the Boltzmann density of the
    # mean curve shifted by a class bias, which was right to 0.2 samples in
    # the mean but 2-3.6x light beyond 8.5 samples for the classes that err
    # (ROADMAP P3 #14). Reproduced in Monte Carlo against the kernel's own
    # record with its loop's integral path and the transmitted pattern in:
    # P(phase > 8.5 samples) 3.75e-5 against 3.70e-5 at 8 mV + 0.019 UI.
    # Not in the chain: the integral path (its state would double the
    # dimension; it accounts for the marginal's tail, which p0 carries)
    # and the pattern's own correlations (PRBS31's x_k = x_(k-28) + x_(k-31)
    # recurs the run structure at the loop's memory, 7x the independent
    # data's tail at 8 mV; PRBS13 0.1x) -- the class mixture is scaled onto
    # p0 so only the classes' relative shapes come from here.
    # The step i -> i+1 re-expresses the window relative to the next
    # symbol: h'_1 = n, h'_q = h_(q-1) xor n, n' = the new one, h_12 is
    # dropped. Class index bits: h_1 .. h_12 at 0 .. n_hist-1, n at n_hist.
    # The two predecessors of a class differ only in h_12, and where n is
    # set the history is complemented -- which on the low bits is the
    # index read backwards -- so the arrival is a sum over h_12 and one
    # reversed slice, no gather. "The two symbols before i agree" is
    # bit 0 == bit 1, so the routing below works on quadrant views.
    half = n_cls // 4                       # 2^(n_hist - 1): the bits h_1 .. h_(n_hist-1)
    n_u = us.size
    n_q = n_cls // 4
    t_old = hist[:, 1]                                                    # symbol i-2, relative to i
    probs = []
    for b in (0, 1):
        s_old = (t_old * (1 - 2 * b)).astype(np.float32)[:, None]
        p_stay = 0.5 * (1.0 + s_old * a_new)
        p_vote = 1.0 - p_stay
        p_late = p_vote * 0.5 * (1.0 + s_old * c_e)
        probs.append((p_stay.astype(np.float32), p_late.astype(np.float32), (p_vote - p_late).astype(np.float32)))
    n_mv = n_sub if kp > 0.0 else 0                 # bins a vote moves the mass (none: nothing moves)
    n_state = 4 if ki > 0.0 else 2
    buf = [np.zeros((n_state, n_cls, n_u), dtype=np.float32) for _ in range(2)]
    arr = [np.empty((n_cls, n_u), dtype=np.float32) for _ in range(2)]
    tmp = [np.empty((n_cls, n_u), dtype=np.float32) for _ in range(4)]

    def arrive(x: np.ndarray, out_a: np.ndarray) -> None:
        """Half the mass of each class's two predecessors, into ``out_a``."""
        summed = x.reshape(2, 2, half, n_u).sum(axis=1)                  # (n, h_12 summed, h_1..h_11)
        a4 = out_a.reshape(2, half, 2, n_u)                               # (new, h'_2..h'_12, h'_1 = n)
        np.multiply(summed[0], 0.5, out=a4[0, :, 0])
        np.multiply(summed[1, ::-1], 0.5, out=a4[0, :, 1])
        a4[1] = a4[0]

    def quads(x: np.ndarray):
        """(same, diff) quadrant views: bit 1 == bit 0, and not."""
        v = x.reshape(n_q, 2, 2, n_u)
        return (v[:, 0, 0], v[:, 1, 1]), (v[:, 0, 1], v[:, 1, 0])

    def advance(st: np.ndarray, out: np.ndarray) -> None:
        """One symbol. ``st`` = mass for the two pairing states, then (with
        the integral path) the same masses times the integrator's mean."""
        for o in out:
            o.fill(0.0)
        stay, late, early, extra = tmp
        for b in (0, 1):
            p_stay, p_late, p_early = probs[b]
            for i_j, j in enumerate((b, b + 2) if n_state == 4 else (b,)):
                arrived = arr[i_j]
                arrive(st[j], arrived)
                # staying keeps the raw sign: right iff the two symbols
                # agree; a vote flips it: right iff they differ
                k_same, k_diff = (b, 1 - b) if j < 2 else (b + 2, 3 - b)
                np.multiply(arrived, p_stay, out=stay)
                np.multiply(arrived, p_late, out=late)
                np.multiply(arrived, p_early, out=early)
                if j >= 2:
                    # the vote's own step into the integrator
                    np.multiply(arr[0], p_late, out=extra)
                    extra *= ki
                    late += extra
                    np.multiply(arr[0], p_early, out=extra)
                    extra *= ki
                    early -= extra
                st_same, st_diff = quads(stay)
                la_same, la_diff = quads(late)
                ea_same, ea_diff = quads(early)
                to_same_s, to_same_d = quads(out[k_same])
                to_diff_s, to_diff_d = quads(out[k_diff])
                for dst, src in zip(to_same_s, st_same):
                    dst += src
                for dst, src in zip(to_diff_d, st_diff):
                    dst += src
                for dst, src in zip(to_diff_s, la_same):
                    dst[:, n_mv:] += src[:, :n_u - n_mv]
                for dst, src in zip(to_same_d, la_diff):
                    dst[:, n_mv:] += src[:, :n_u - n_mv]
                for dst, src in zip(to_diff_s, ea_same):
                    dst[:, :n_u - n_mv] += src[:, n_mv:]
                for dst, src in zip(to_same_d, ea_diff):
                    dst[:, :n_u - n_mv] += src[:, n_mv:]
        if n_state == 4:
            # the integrator's push, at its mean given the phase (over the
            # classes: it remembers further back than the window), as a
            # chance of one bin in its direction
            m_tot = out[0].sum(axis=0) + out[1].sum(axis=0)
            i_mean = (out[2].sum(axis=0) + out[3].sum(axis=0)) / np.maximum(m_tot, 1e-30)
            if _PATTERN_PHASE_TRACE is not None:
                _PATTERN_PHASE_TRACE["i_mean"] = i_mean.copy()
            frac = np.clip(np.abs(i_mean) / dus, 0.0, 1.0)
            f_up = np.where(i_mean > 0.0, frac, 0.0).astype(np.float32)[None, :]
            f_down = np.where(i_mean < 0.0, frac, 0.0).astype(np.float32)[None, :]
            f_up[0, -1] = 0.0
            f_down[0, 0] = 0.0
            up, down, side = tmp[0], tmp[1], tmp[2]
            if second_order:
                # Lax-Wendroff fluxes: the first-order (upwind) move of a
                # fraction c of a bin's mass one bin on adds c (1 - c) / 2
                # bins^2 of diffusion a step, which the tails read as a
                # wider loop (the factor fell 1.71 / 1.32 / 1.17 at 8 mV as
                # the grid went 0.5 / 0.25 / 0.125 samples); the second-order
                # flux takes it back. Outward of the mode the density falls
                # in the push's direction, so the flux stays within (0, c m]
                # and no mass goes negative. Flux = m (c - g) + g m_next.
                g_up = (0.5 * f_up * (1.0 - f_up)).astype(np.float32)
                g_down = (0.5 * f_down * (1.0 - f_down)).astype(np.float32)
                a_up, a_down = f_up - g_up, f_down - g_down
            else:
                a_up, a_down = f_up, f_down
            # each push is non-zero on one side of the grid only
            cols_up = np.flatnonzero(f_up[0] > 0.0)
            cols_dn = np.flatnonzero(f_down[0] > 0.0)
            up_lo, up_hi = (int(cols_up[0]), int(cols_up[-1]) + 1) if cols_up.size else (0, 0)
            dn_lo, dn_hi = (int(cols_dn[0]), int(cols_dn[-1]) + 1) if cols_dn.size else (1, 1)
            for o in out:
                # both fluxes from the mass before either moves (f_up[-1] = 0,
                # f_down[0] = 0 keep the neighbours on the grid)
                f_u = up[:, up_lo:up_hi]
                np.multiply(o[:, up_lo:up_hi], a_up[:, up_lo:up_hi], out=f_u)
                f_d = down[:, dn_lo:dn_hi]
                np.multiply(o[:, dn_lo:dn_hi], a_down[:, dn_lo:dn_hi], out=f_d)
                if second_order:
                    np.multiply(o[:, up_lo + 1:up_hi + 1], g_up[:, up_lo:up_hi], out=side[:, up_lo:up_hi])
                    f_u += side[:, up_lo:up_hi]
                    np.multiply(o[:, dn_lo - 1:dn_hi - 1], g_down[:, dn_lo:dn_hi], out=side[:, dn_lo:dn_hi])
                    f_d += side[:, dn_lo:dn_hi]
                o[:, up_lo:up_hi] -= f_u
                o[:, dn_lo:dn_hi] -= f_d
                o[:, up_lo + 1:up_hi + 1] += f_u
                o[:, dn_lo - 1:dn_hi - 1] += f_d

    buf[0][0][...] = (p0 / n_cls).astype(np.float32)[None, :]
    cur, nxt_buf = buf
    sig = p0 > 1e-6 * p0.max()
    mean0 = float(np.sum(p0 * us) * dus)
    rms0 = float(np.sqrt(max(float(np.sum(p0 * (us - mean0) ** 2) * dus), 1e-12)))
    p_s = p0
    n_used = 0
    done = False
    # the step: the numpy one above defines it; the fused loop
    # (cdr/pattern_chain.py, numba) gives its arrays bit for bit, 4-5x faster
    if kernel == "auto":
        from ..cdr.pattern_chain import chain_step as kernel
        if _PATTERN_PHASE_TRACE is not None:
            kernel = None           # the trace reads the integrator's mean from the numpy step
    elif kernel == "numpy":
        kernel = None
    if kernel is not None:
        p_st = np.ascontiguousarray(np.stack([probs[0][0], probs[1][0]]))
        p_la = np.ascontiguousarray(np.stack([probs[0][1], probs[1][1]]))
        p_ea = np.ascontiguousarray(np.stack([probs[0][2], probs[1][2]]))

        def advance(st, out):    # noqa: F811 -- the same step, fused
            kernel(st, out, p_st, p_la, p_ea, n_hist, n_mv, float(ki), bool(second_order),
                   float(dus))
    for _ in range(n_steps):
        n_used += 1
        advance(cur, nxt_buf)
        cur, nxt_buf = nxt_buf, cur
        tot = float(cur[0].sum() + cur[1].sum())
        for o in cur:
            o /= tot
        mix = (cur[0].sum(axis=0) + cur[1].sum(axis=0)).astype(np.float64) / dus
        # a loop whose mean walks off by its own RMS is slipping (a closed
        # eye: NRZ 32G static, no noise, BER 0.1 -- 600 steps for nothing);
        # no stationary picture holds
        walk = abs(float(np.sum(mix * us) * dus) - mean0)
        if walk > rms0:
            p_s = mix
            break
        # converged where the engine's density has mass, tail included,
        # relative to itself: the tails converge last and set the factor
        done = float(np.max(np.abs(mix[sig] / np.maximum(p_s[sig], 1e-300) - 1.0))) < tol
        p_s = mix
        if done:
            break
    mass = cur[:2]
    pc = (mass[0] + mass[1]).astype(np.float64)
    marginal_error = float(np.max(np.abs(p_s[sig] / p0[sig] - 1.0))) if sig.any() else 0.0
    if _PATTERN_PHASE_TRACE is not None:
        _PATTERN_PHASE_TRACE.update(us=us, p0=p0, p_s=p_s, p_err=p_err, pc_raw=pc.copy(), n_steps=n_used)
    if onto_p0 is None:
        # with the integral path the chain's own marginal is the loop's
        # phase on random data; without it only its class posterior is
        onto_p0 = not ki > 0.0
    if onto_p0:
        # the class posterior P(class | phase) from the chain, on the
        # engine's marginal. It is the chain's only where the chain itself
        # puts a fair share of p0's mass (``trust``): the tails beyond come
        # from paths it does not have (the integral path, the pattern's
        # correlations), and read there it extrapolates -- on the NRZ 28G
        # analytic preset its own marginal is 1e-17 of p0 at the phases
        # its errors sit and the factor read 2e-6. Outward of the last
        # phase the share is still ``trust`` on either side the posterior
        # stays the one there. Checked to 10 samples late on the NRZ 16G
        # preset at 8 mV (the class with the errors 0.86-1.4x the kernel's
        # there, where the chain carries 1e-3 of p0).
        rel = p_s / np.maximum(p0, 1e-300)
        posterior = pc / np.maximum(p_s * dus, 1e-300)[None, :]
        i_mode = int(np.argmax(p0))
        ok = rel >= trust
        hi = i_mode
        while hi + 1 < us.size and ok[hi + 1]:
            hi += 1
        lo = i_mode
        while lo - 1 >= 0 and ok[lo - 1]:
            lo -= 1
        if ok[i_mode]:
            posterior[:, hi + 1:] = posterior[:, hi:hi + 1]
            posterior[:, :lo] = posterior[:, lo:lo + 1]
        else:
            posterior[...] = 1.0 / n_cls
        pc = posterior * (p0 * dus)[None, :]
    pc /= np.maximum(pc.sum(axis=1, keepdims=True) * dus, 1e-300)
    shift = (pc * us[None, :]).sum(axis=1) * dus
    read_joint = w_cls * float(np.sum(pc * p_err)) * dus
    read_indep = w_cls * float(np.sum(p0[None, :] * p_err)) * dus
    raw = read_joint / read_indep if read_indep > 0.0 else 1.0
    # the chain's marginal alone, no correlation: what a lane reads on a
    # clock whose phase is distributed as this loop's but which its own
    # data does not vote (another lane's loop, engine.shared_clock)
    read_marg = w_cls * float(np.sum(np.asarray(p_s, dtype=np.float64)[None, :] * p_err)) * dus
    raw_marg = read_marg / read_indep if read_indep > 0.0 and not onto_p0 else 1.0
    # The factor carries the chain's own marginal as well as the classes'
    # correlation, and below 1 it is the marginal: the engine's stationary
    # density is a Boltzmann fit to the linearised RMS whose tails run far
    # heavier than the loop's at low noise (NRZ 16G 6 mV: the kernel 0.74x
    # the read before the factor, the chain 0.80; NRZ 28G analytic at its
    # 4 mV preset: the kernel's phase never beyond 2 samples in 1.6e7
    # symbols, p0 1.1e-4 there, the chain 1e-5). Until 2026-10-26 it was
    # floored at 1. A chain that does not converge (a slipping loop) carries
    # no reading
    factor = raw if done else 1.0
    result = PatternPhase(factor=float(factor), shift=shift, history=hist.astype(np.int8),
                          next_same=nxt > 0, read_joint=float(read_joint), read_indep=float(read_indep),
                          phase_grid=us, cond_density=pc.astype(np.float32), marginal_error=marginal_error,
                          density=p_s, factor_raw=float(raw), steps=n_used, converged=bool(done),
                          factor_marginal=float(raw_marg if done else 1.0))
    if key is not None:
        _PATTERN_PHASE_CACHE[key] = result
        while len(_PATTERN_PHASE_CACHE) > _PATTERN_PHASE_CACHE_SIZE:
            _PATTERN_PHASE_CACHE.popitem(last=False)
    return result
