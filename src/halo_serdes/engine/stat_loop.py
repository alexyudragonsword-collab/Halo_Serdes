"""The statistical engine's clock-recovery model: where the loop samples, how it wanders.

The lock position on the equalised pulse (``_lock_position``), the
bang-bang detector's mean output against phase (``_bb_mean_curve``) and its
pair statistics, the sampling jitter the bathtub is smeared with
(``_sampling_jitter_ui``: the clocks through the linearised loop of
``cdr.linear`` and the loop's own wander), the smear helpers, and the
warnings where that model stops holding. Part of ``engine.statistical``
(split out 2026-10-30; the names are importable from there as before).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..cdr.linear import (
    BbEdgeStats, ErrorResponse, LoopParams, LoopSolution, bb_loop_fixed_point, error_response,
    lock_offset_samples, mm_hold_range_ui, mm_loop_solution, mm_pd_statistics,
)
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.mapping import level_probabilities
from ..core.waveform import Waveform
from .stat_noise import adc_noise_sigma
from .static_link import _levels, make_pattern


#: how many of the loop's own RMS wanders (LoopSolution.sigma_wander_ui) the
#: Mueller-Muller timing function must keep growing for before
#: run_statistical stops warning that the time engine's loop may get over its
#: turnover and run away. Calibrated, not derived: on the fingerprint's
#: 53 Gb/s link with a profile clock (PRBS7Q-15Q, kp_shift 4-10, 4 seeds x
#: 200k symbols) the time engine lost lock at 2.8-6.9x and never from 8.6x;
#: random symbols sit at 0.1-0.7x there. The ADC presets hold at 15-33x on
#: random symbols (cairn/DSP发端与PR.md §27)
_MM_HOLD_SIGMAS = 8.0

#: how far a loop's steady-state error may exceed what a run of the
#: configured length from rest leaves before run_statistical says the run is
#: too short to show it. Example 31's ADC loop: 1.00-1.07x to kp_shift 6,
#: 1.17x at 7, 1.37x / 1.72x at 8 / 9 (|E| peaking 8-16x) (ROADMAP P2 #28)
_RUN_STEADY_WARN = 1.25


def _lock_position(cfg: LinkConfig, h_pre_ffe: np.ndarray, h_chan: np.ndarray, tx_pipe,
                   has_ffe: bool, ffe_pre: int) -> float:
    """The sample, on the equalised pulse's axis, where the receiver's loop
    samples the main cursor (``run_statistical``'s notes on the sampling
    phase): found on the pre-FFE pulse, then delayed by the FFE's main tap
    (``ffe_pre`` UI)."""
    osr = cfg.osr
    h_lock = h_pre_ffe
    if tx_pipe.pr_taps is not None:
        # a 1 + aD Tx pulse peaks on either cursor; the time engine
        # starts on the unshaped pulse's (TxPipeline.receiver_view)
        un = tx_pipe.equivalent_symbol_response(osr, shaping=False)
        h_lock = h_chan if un is None else np.convolve(h_chan, un)
    pre_y = pulse_from_impulse(Waveform(h_lock, cfg.dt), osr).y
    pk_pre = int(np.argmax(np.abs(pre_y)))
    if cfg.rx.arch == "mixed_signal":
        at = 0.0 if has_ffe else lock_offset_samples(pre_y, pk_pre, osr, osr // 2)
    else:
        at = 0.0 if cfg.mm_pd_input == "ffe" else lock_offset_samples(pre_y, pk_pre, osr, osr)
    return pk_pre + at + (ffe_pre * osr if has_ffe else 0)


def _pulse_at(y: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """``y`` at fractional sample positions: Keys' cubic convolution
    (``a = -1/2``), zero outside the array -- on a pulse sampled ``osr`` times
    a UI, far finer than the linear interpolation between its BER values."""
    pos = np.asarray(pos, dtype=np.float64)
    i0 = np.floor(pos).astype(np.int64)
    t = pos - i0
    weights = ((-t ** 3 + 2.0 * t ** 2 - t) / 2.0, (3.0 * t ** 3 - 5.0 * t ** 2 + 2.0) / 2.0,
               (-3.0 * t ** 3 + 4.0 * t ** 2 + t) / 2.0, (t ** 3 - t ** 2) / 2.0)
    out = np.zeros(pos.shape)
    for k, w in zip((-1, 0, 1, 2), weights):
        j = i0 + k
        ok = (j >= 0) & (j < y.size)
        out[ok] += w[ok] * y[j[ok]]
    return out


def _smeared_at(curve: np.ndarray, phi: np.ndarray, center: float, sigma: float) -> float:
    """A BER curve over phase averaged over a Gaussian sampling instant:
    ``int curve(phi) N(phi; center, sigma) dphi``, the curve interpolated
    linearly between its phases and held at its ends (as the smear of the
    bathtub pads them). Linear, so the read stays an average over phases --
    a drifting aggressor is the mean of its skews' reads; in log between the
    phases a steep bathtub would read up to ~10 % lower."""
    x = np.linspace(center - 8.0 * sigma, center + 8.0 * sigma, 2001)
    v = np.interp(x, np.asarray(phi, dtype=np.float64), np.asarray(curve, dtype=np.float64))
    g = np.exp(-0.5 * ((x - center) / sigma) ** 2)
    return float(np.sum(v * g) / np.sum(g))


def _smeared_with(curve: np.ndarray, phi: np.ndarray, center: float, u: np.ndarray,
                  density: np.ndarray) -> float:
    """``_smeared_at`` with a density on the uniform grid ``u`` of offsets
    from ``center`` in place of the Gaussian: ``sum curve(center + u) p(u) du``,
    the curve interpolated linearly and held at its ends."""
    v = np.interp(center + np.asarray(u, dtype=np.float64),
                  np.asarray(phi, dtype=np.float64), np.asarray(curve, dtype=np.float64))
    du = float(u[1] - u[0]) if np.size(u) > 1 else 1.0
    return float(np.sum(v * density) * du)


def _bb_mean_curve(pulse_pd: Waveform, osr: int, noise_sigma: float, swing: float,
                   levels_norm: np.ndarray, u: np.ndarray, extra_edge_ui: float = 0.0,
                   n_pre: int = 24, n_post: int = 64, level_probs: np.ndarray | None = None
                   ) -> np.ndarray:
    """The Alexander detector's mean output against the clock's offset ``u``
    [samples, late positive] from the lock point, as the kernel's detector
    reads it (``cdr/kernels.py``): on the raw (pre-DFE) samples, ``pd = (sign
    y_prev - sign y_cur) sign y_edge / 2`` -- a transition where the two data
    samples' signs differ, +1 (early) where the edge sample reads as the old
    one. Each sample is its two conditioned symbols' cursors at the shifted
    instant plus the other symbols' ISI and the AWGN as one Gaussian (the
    data samples' ``sigma_d``, the edge's ``sigma_e``, independent across the
    three), summed over every pair of levels by its probability; the
    transmitter's white edge jitter moves the crossing, so the curve is
    averaged over it. At the origin its slope is the linearised gain
    (``bb_pd_gain``: 1.81-2.08/UI against the kernel's 1.92-2.15 on the NRZ
    16G preset); out from it the curve saturates, sooner on the late side,
    where a late clock's data sample slides down the pulse and its raw sign
    flips more often -- which is the shape of the sampler's phase density
    (:func:`halo_serdes.cdr.linear.bb_stationary_density`). Measured against
    the kernel's output binned by its phase, within 0.01-0.03 to 5 samples
    either side; its late plateau beyond is softer still in the kernel
    (-0.22 against -0.26 to -0.34, ROADMAP P2 #32).
    """
    from scipy.special import erf

    y = np.asarray(pulse_pd.y, dtype=np.float64)
    peak = int(np.argmax(np.abs(y)))
    half = osr // 2
    data = peak + lock_offset_samples(y, peak, osr, half)     # fractional: the sampler interpolates
    e0, e1 = data - half, data + half                         # this symbol's and the previous one's cursor
    grid = np.arange(y.size, dtype=np.float64)

    def at(x):
        return np.interp(x, grid, y, left=0.0, right=0.0)

    pw = (np.full(levels_norm.size, 1.0 / levels_norm.size) if level_probs is None
          else np.asarray(level_probs, dtype=np.float64))
    sym_var = float(np.sum(pw * levels_norm ** 2))

    def isi_sigma(centre: float, conditioned: tuple[float, float]) -> float:
        idx = centre + np.arange(-n_pre, n_post + 1) * osr
        keep = np.all([np.abs(idx - c) > 0.5 for c in conditioned], axis=0)
        return float(np.sqrt(swing ** 2 * sym_var * np.sum(at(idx[keep]) ** 2) + noise_sigma ** 2))

    s_e = isi_sigma(e0, (e0, e1))
    s_d = isi_sigma(data, (data, data + osr))
    u = np.asarray(u, dtype=np.float64)
    curve = np.zeros_like(u)
    for i_old, a_old in enumerate(levels_norm):
        for i_new, a_new in enumerate(levels_norm):
            p = float(pw[i_old] * pw[i_new])
            mu_e = swing * (a_new * at(e0 + u) + a_old * at(e1 + u))
            mu_prev = swing * (a_old * at(data + u) + a_new * at(data + u - osr))
            mu_cur = swing * (a_new * at(data + u) + a_old * at(data + u + osr))
            curve += p * 0.5 * (erf(mu_prev / (np.sqrt(2.0) * s_d)) - erf(mu_cur / (np.sqrt(2.0) * s_d))) \
                * erf(mu_e / (np.sqrt(2.0) * s_e))
    if extra_edge_ui > 0.0 and u.size > 1:
        du = float(u[1] - u[0])
        half_k = int(np.ceil(4.0 * extra_edge_ui * osr / du))
        k = np.exp(-0.5 * (np.arange(-half_k, half_k + 1) * du / (extra_edge_ui * osr)) ** 2)
        k /= k.sum()
        curve = np.convolve(np.pad(curve, (half_k,), mode="edge"), k, mode="valid")
    return curve


def _sampling_jitter_ui(cfg: LinkConfig, pulse_pd: Waveform, levels_norm: np.ndarray,
                        swing: float, noise_sigma: float, n_feedback: int = 0,
                        loop: bool = True, ffe_track: float = 0.0, voters: int = 1,
                        lock_shift: int = 0) -> tuple[float, LoopSolution | None]:
    """RMS sampling-instant jitter [UI] to smear the bathtub with, and the loop model behind it.

    The transmitter's white jitter is not in it: it moves each edge on its
    own and reaches the slicer as noise that follows the transitions
    (``tx_edge_jitter``), not as a shift of the sampling instant -- though it
    does randomise a bang-bang detector's edge samples. A profile on either
    side (transmit or receiver -- the loop tracks their difference, so they
    are interchangeable here) is integrated through the linearised CDR of the
    configured architecture (bang-bang for mixed-signal, Mueller-Muller for
    ADC, matching which kernel the time engine would run) as a run of ``N``
    symbols holds it: on the lines ``k / (N UI)`` the time engine's
    synthesiser puts it on, the loop starting from rest, over the window the
    time engine scores (``ErrorResponse.run_mean_sq``). A loop that rings
    longer than the run never reaches its steady state in it; that is
    reported (``sigma_steady_ui``, warned past 1.25x), and its error at
    points over the run (``sigma_over_run_ui``) is what the read averages
    over (ROADMAP P2 #28). The receiver clock's white-kind RJ on top rides
    through the same error response, steady.

    The bang-bang loop hunts on any clock, so mixed-signal runs its model on
    white clocks too: 0.02-0.06 UI on the presets' loops, which the time
    engine's sampler follows (0.95-1.07x on NRZ, PAM4 0.97-1.01x since its
    data-set detector output is shaped, ROADMAP P2 #30). Until
    2026-10-15 white clocks returned the receiver's RJ alone, and the
    statistical BER was 0.1-0.5x the time engine's on the NRZ 16G preset
    with transmit RJ (ROADMAP P2 #27). An ADC receiver on white clocks keeps
    the receiver's RJ: its Mueller-Muller loop's own noise is 0.0001-0.001
    UI on PRBS13Q and 0.005-0.006 UI on random symbols on the presets, and
    its model would triple the cost of a white-clock run. ``loop`` False: a
    mixed-signal receiver with an FFE stands for the static engine, which
    samples at a fixed phase -- no loop to hunt.
    """
    from ..tx.clock import ClockProfile

    clocks = (cfg.tx.clock, cfg.rx.clock)
    if (cfg.rx.arch == "adc_dsp" or not loop) and all(c.kind != "profile" for c in clocks):
        # white on both sides, ADC or no loop: the receiver's RJ smears the sampling instant
        return float(cfg.rx.clock.rj_ui), None
    # The loop tracks the *difference* between the two clocks, so both ride
    # through the same error response and their untracked powers add.
    profs = [ClockProfile.load(c.file, f0_hz=c.f0_hz) if c.kind == "profile" else None
             for c in clocks]
    lp = LoopParams.from_config(cfg)
    ui = cfg.ui
    f_lo = cfg.symbol_rate / max(int(cfg.sim.n_symbols), 2)
    f_nyq = cfg.symbol_rate / 2.0
    f_white = np.linspace(f_lo, f_nyq, 2048)
    rj_white = float(cfg.rx.clock.rj_ui)       # the transmitter's: tx_edge_jitter
    # the window the time engine scores, in loop updates: the loop starts
    # from rest at the first symbol (training_schedule's settle onwards)
    n_sym = max(int(cfg.sim.n_symbols), 2)
    per_update = max(int(round(cfg.symbol_rate / lp.f_update)), 1)
    first = min(int(cfg.sim.cdr_settle), n_sym // 4) // per_update
    last = max(n_sym // per_update - 1, first)

    def untracked(err_fn) -> float:
        """Clock jitter the loop leaves [UI]: over the run, from rest, on the
        run's lines when the solver hands an ErrorResponse; the steady state
        of a continuous profile for a bare |E| (and for the white part)."""
        if isinstance(err_fn, ErrorResponse):
            ms = lambda f: err_fn.run_mean_sq(f, first, last)   # noqa: E731
            var = sum((p.run_untracked_sigma_s(ms, n_sym, ui) / ui) ** 2
                      for p in profs if p is not None)
        else:
            var = sum((p.untracked_sigma_s(err_fn, f_lo) / ui) ** 2 for p in profs if p is not None)
        white = rj_white * float(np.sqrt(np.mean(np.asarray(err_fn(f_white)) ** 2)))
        return float(np.sqrt(var + white ** 2))

    if cfg.rx.arch == "adc_dsp":
        # the adaptive FFE takes up part of the error itself (ffe_track per
        # update): on the detector's input a second path in the loop
        lp = replace(lp, ffe_track=float(ffe_track), ffe_in_loop=cfg.mm_pd_input == "ffe")
        peak = int(np.argmax(np.abs(pulse_pd.y)))
        levels_v = levels_norm * swing
        period = _pattern_period(cfg)
        k_pd, pd_noise = mm_pd_statistics(
            pulse_pd.y, cfg.osr, peak, levels_v, noise_sigma, n_feedback=n_feedback,
            n_lanes=max(int(cfg.rx.adc.n_lanes), 1),
            period_levels=None if period is None else levels_v[period], lock_shift=lock_shift)
        if k_pd <= 0:
            # no timing gradient: the loop does not move, nothing is tracked
            return untracked(lambda f: np.ones_like(np.asarray(f, dtype=np.float64))), None
        sol = mm_loop_solution(lp, k_pd, pd_noise, untracked)
        # what moves the loop itself: its own noise and the clock it follows
        # late; jitter above its bandwidth only blurs the timing function
        lagged = untracked(lambda f: error_response(f, lp, k_pd)
                           * (np.asarray(f, dtype=np.float64) <= sol.bandwidth_hz))
        sol = replace(sol, ffe_track=lp.ffe_track,
                      sigma_wander_ui=float(np.hypot(sol.sigma_self_ui, lagged)),
                      hold_range_ui=mm_hold_range_ui(pulse_pd.y, cfg.osr, peak, levels_v,
                                                     noise_sigma, n_feedback=n_feedback))
        _warn_if_mm_cannot_hold(sol)
    else:
        # PAM4's off-lock sign changes give an output the data sets, which
        # consecutive transitions largely cancel: through the pattern's lines
        level_probs = level_probabilities(cfg.modulation)
        edge = _bb_edge_stats(cfg, pulse_pd, levels_norm, swing, noise_sigma, level_probs)
        pairs = _bb_pairs(pulse_pd, cfg.osr, noise_sigma, swing, levels_norm,
                          extra_edge_ui=float(cfg.tx.clock.rj_ui), level_probs=level_probs)
        sol = bb_loop_fixed_point(
            lp, untracked,
            transitions=_bb_transitions(pulse_pd, cfg.osr, noise_sigma, swing, levels_norm,
                                        pairs=pairs, level_probs=level_probs),
            mean_output=lambda sigma_e: _bb_mean_output(pairs, levels_norm.size, sigma_e),
            period=_pattern_period(cfg), voters=voters, edge=edge)
    # the same loop once it has forgotten its start: what a longer run, and
    # the link in service, settles at
    steady = float(np.hypot(untracked(lambda f, k=sol.k_pd: error_response(f, lp, k)),
                            sol.sigma_self_ui))
    sol = replace(sol, sigma_steady_ui=steady, sigma_over_run_ui=_over_run(
        profs, ErrorResponse(lp, sol.k_pd), sol, first, last, n_sym, ui))
    _warn_if_run_short_of_steady(sol, cfg)
    if cfg.rx.arch != "adc_dsp":
        _warn_if_underdamped(sol)
    for p in profs:
        if p is not None:
            _warn_if_slew_limited(p, lp, f_lo, sol, cfg)
    if cfg.rx.arch != "adc_dsp" and sol.sigma_ui > 0.1:
        import warnings

        warnings.warn(
            f"the bang-bang detector sees {sol.sigma_ui:.3f} UI RMS of phase error "
            "(clock jitter it cannot track plus its own hunting); its linearisation "
            "assumes a small error, and the kernel loses lock from about 0.1 UI "
            "(measured: locked at 0.08, slipping at 0.14) -- the statistical engine's "
            "sampling-jitter model is noise-limited here", stacklevel=3)
    return sol.sigma_ui, sol


def _pattern_period(cfg: LinkConfig, max_symbols: int = 1 << 21) -> np.ndarray | None:
    """One period of the line symbols the configured pattern repeats, or None.

    A loop sees a repeating pattern's data noise only as lines at multiples
    of its repetition rate (:func:`halo_serdes.cdr.linear.mm_pd_statistics`),
    so the Mueller-Muller model wants the period itself. PRBS-N, bit- or
    symbol-wise, repeats every ``2**N - 1`` symbols; a 1/(1+D) precoder can
    double that. Anything longer than ``max_symbols`` once tiled over whole
    ADC blocks -- PRBS31, PRQS10 -- is random data to a loop and gets None.
    """
    import re

    m = re.fullmatch(r"prbs(\d+)q?", cfg.sim.pattern.lower())
    if m is None:
        return None
    per = (1 << int(m.group(1))) - 1
    n_lanes = max(int(cfg.rx.adc.n_lanes), 1)
    if 2 * per * n_lanes > max_symbols:
        return None
    sym = make_pattern(replace(cfg, sim=replace(cfg.sim, n_symbols=2 * per)))
    if cfg.precode:
        from ..core.mapping import precode_1plusd

        sym = precode_1plusd(sym, cfg.n_levels)
    return sym[:per] if np.array_equal(sym[:per], sym[per:]) else sym


def _bb_edge_stats(cfg: LinkConfig, pulse_pd: Waveform, levels_norm: np.ndarray, swing: float,
                   noise_sigma: float, level_probs: np.ndarray | None = None,
                   n_symbols: int = 1 << 17) -> BbEdgeStats:
    """The Alexander detector's edge samples over the configured pattern --
    one period of a repeating one, else seeded random symbols at the
    modulation's level probabilities -- at the lock point the symmetric
    transitions set (``lock_offset_samples``, fractional: the sampler
    interpolates), with the transmitter's white edge jitter on the edge."""
    y = np.asarray(pulse_pd.y, dtype=np.float64)
    peak = int(np.argmax(np.abs(y)))
    half = cfg.osr // 2
    lock = peak + lock_offset_samples(y, peak, cfg.osr, half)
    levels_v = np.asarray(levels_norm, dtype=np.float64) * swing
    period = _pattern_period(cfg)
    if period is not None:
        amps, periodic = levels_v[period], True
    else:
        rng = np.random.default_rng(0)
        pw = None if level_probs is None else np.asarray(level_probs, dtype=np.float64)
        amps, periodic = levels_v[rng.choice(levels_v.size, size=n_symbols, p=pw)], False
    return BbEdgeStats.build(y, cfg.osr, lock, half, amps, noise_sigma,
                             edge_ui=float(cfg.tx.clock.rj_ui), periodic=periodic)


def _bb_pairs(pulse_pd: Waveform, osr: int, noise_sigma: float, swing: float,
              levels_norm: np.ndarray, extra_edge_ui: float = 0.0, n_pre: int = 24,
              n_post: int = 64, level_probs: np.ndarray | None = None
              ) -> dict[tuple[int, int], tuple[float, float]]:
    """The Alexander detector's sign changes, pair by pair of levels:
    ``{(old, new): (zero crossing relative to the lock point [UI], edge noise
    [UI])}`` -- what ``_bb_transitions`` groups into classes and
    ``_bb_mean_output`` reads the detector's data-dependent output from.

    The edge sample sits half a UI before the data sample on the raw (pre-DFE)
    waveform, and the detector compares signs (``cdr/kernels.py``): every
    sign change between two symbols is a transition. The loop locks where a
    symmetric one (+a -> -a) reads zero, where this symbol's half-cursor
    equals the previous symbol's (``lock_offset_samples``). Its edge noise is
    AWGN and the ISI of every other symbol over its edge slope -- voltage
    noise over slope is timing noise, which the detector cannot tell from
    clock jitter -- plus ``extra_edge_ui`` (the transmitter's white edge
    jitter, which moves the edge it samples). A sign change between unequal
    magnitudes (PAM4's +-1 <-> -+1/3) crosses zero away from the lock: where
    ``a_new c0(tau) + a_old c1(tau)`` does, on the two cursors' linearised
    slopes -- 0.1 UI off on the PAM4 32G preset, so almost no timing, only
    noise. NRZ is one class, as before (2026-10-15, ROADMAP P2 #27).
    """
    y = np.asarray(pulse_pd.y, dtype=np.float64)
    peak = int(np.argmax(np.abs(y)))
    half = osr // 2
    lock = lock_offset_samples(y, peak, osr, half)
    data = peak + int(round(lock))
    e0, e1 = data - half, data + half           # this symbol's and the previous symbol's cursor

    def at(i: int) -> float:
        return float(y[i]) if 0 <= i < y.size else 0.0

    diff = (at(e0 + 1) - at(e0 - 1)) - (at(e1 + 1) - at(e1 - 1))
    s0, s1 = (at(e0 + 1) - at(e0 - 1)) / 2.0, (at(e1 + 1) - at(e1 - 1)) / 2.0
    c0, c1 = at(e0), at(e1)
    tau_sym = (c1 - c0) / (s0 - s1) if s0 != s1 else 0.0     # the symmetric classes' zero
    idx = e0 + np.arange(-n_pre, n_post + 1) * osr
    others = [at(int(i)) for i in idx if 0 <= i < y.size and i not in (e0, e1)]
    pw = (np.full(levels_norm.size, 1.0 / levels_norm.size) if level_probs is None
          else np.asarray(level_probs, dtype=np.float64))
    isi_var = swing ** 2 * float(np.sum(pw * levels_norm ** 2)) * float(np.sum(np.square(others)))
    sigma_v = float(np.sqrt(isi_var + noise_sigma ** 2))
    pairs: dict[tuple[int, int], tuple[float, float]] = {}
    for i_old, a_old in enumerate(levels_norm):
        for i_new, a_new in enumerate(levels_norm):
            if (a_old > 0) == (a_new > 0):
                continue
            if a_new == -a_old:
                # d/dtau [c0(tau) - c1(tau)] * amplitude, volts per sample
                slope = swing * float(abs(a_old)) * diff / 2.0
                offset = 0.0
            else:
                slope = swing * float(a_new * s0 + a_old * s1)
                offset = float(-(a_new * c0 + a_old * c1) / (a_new * s0 + a_old * s1)
                               - tau_sym) / osr if slope != 0.0 else float("inf")
            if abs(slope) < 1e-18:
                if offset != 0.0:
                    continue
                edge = float(extra_edge_ui)     # no slope to convert the voltage noise with
            else:
                edge = float(np.hypot(sigma_v / (abs(slope) * osr), extra_edge_ui))
            pairs[(i_old, i_new)] = (offset, edge)
    return pairs


def _bb_transitions(pulse_pd: Waveform, osr: int, noise_sigma: float, swing: float,
                    levels_norm: np.ndarray, extra_edge_ui: float = 0.0, n_pre: int = 24,
                    n_post: int = 64, pairs: dict | None = None,
                    level_probs: np.ndarray | None = None
                    ) -> tuple[tuple[float, float, float], ...]:
    """The Alexander detector's transitions by class, for ``bb_loop_fixed_point``:
    ``(probability per symbol, zero crossing relative to the lock point [UI],
    edge noise [UI])`` -- ``_bb_pairs`` grouped where they cross and how
    noisily."""
    if pairs is None:
        pairs = _bb_pairs(pulse_pd, osr, noise_sigma, swing, levels_norm, extra_edge_ui,
                          n_pre, n_post, level_probs)
    n = levels_norm.size
    pw = np.full(n, 1.0 / n) if level_probs is None else np.asarray(level_probs, dtype=np.float64)
    classes: dict[tuple[float, float], float] = {}
    for (i_old, i_new), key in pairs.items():
        classes[key] = classes.get(key, 0.0) + float(pw[i_old] * pw[i_new])
    return tuple((p, offset, edge) for (offset, edge), p in classes.items())


def _bb_mean_output(pairs: dict[tuple[int, int], tuple[float, float]], n_levels: int,
                    sigma_e: float) -> np.ndarray:
    """The detector's mean output at the lock point per pair of levels, for
    ``bb_data_noise``: ``erf(offset / (sqrt 2 s))`` with ``s`` the pair's edge
    noise and the loop's own error together -- +1 where the clock samples
    well before a crossing (an edge equal to the old symbol: early), 0 for a
    symmetric pair or no sign change."""
    from scipy.special import erf

    m = np.zeros((n_levels, n_levels))
    for (i_old, i_new), (offset, edge) in pairs.items():
        if offset != 0.0:
            m[i_old, i_new] = float(erf(offset / (np.sqrt(2.0) * max(np.hypot(edge, sigma_e), 1e-12))))
    return m


def _warn_if_mm_cannot_hold(sol: LoopSolution) -> None:
    """The linear Mueller-Muller model assumes the detector pulls back in
    proportion at any error. Where its timing function turns over within a
    few of the loop's own RMS wanders of the lock point (``hold_range_ui``
    against ``sigma_wander_ui``) the time engine's loop gets over the
    turnover, past the next zero the pull reverses, and its integrator winds
    it off for good: on the fingerprint's 53 Gb/s link, turnover 0.026 UI
    out, it ran up to 100 UI away within 200k symbols -- on every seed on
    random symbols (0.1-0.7x), on 1-4 seeds of 4 at 2.8-6.9x on a profile
    clock (PRBS9Q-15Q), never from 8.6x. The ADC presets sit at 15-33x on
    random symbols, example 31's loops at 15-37x."""
    hold, wander = sol.hold_range_ui, sol.sigma_wander_ui
    if hold is None or wander is None or hold >= _MM_HOLD_SIGMAS * wander:
        return
    import warnings

    warnings.warn(
        f"the Mueller-Muller detector's pull back to its lock point stops growing "
        f"{hold:.3f} UI out, {hold / max(wander, 1e-12):.1f}x the loop's own "
        f"{wander:.4f} UI RMS wander (< {_MM_HOLD_SIGMAS:g}x): the time-domain loop "
        "can get over that turnover and run away (on the link measured: lost lock at "
        "2.8-6.9x, never from 8.6x), "
        "which the linear loop model and this engine's BER do not describe -- check with "
        "the time engine on the pattern and length that matter", stacklevel=3)


#: points over the run at which an underdamped loop's error is taken
_RUN_POINTS = 12


def _ffe_lms_track(cfg: LinkConfig, pulse_pre: Waveform, n_dfe: int) -> float:
    """How much of a sampling offset an ADC receiver's LMS FFE takes up per
    loop update, ``n_lanes * mu * lambda'``.

    A sampling offset ``d`` moves the FFE's optimum along its timing
    derivative ``w'``; the LMS (``w -= mu e x`` per symbol) closes the gap
    along it at ``mu`` times the input autocorrelation's Rayleigh quotient
    there, ``lambda' = w'R w' / w'w'`` -- so the equalised samples see
    ``d - c`` with ``c`` following ``d`` at that rate. ``w'`` from the time
    engine's own start-up solve (``mmse_ffe``, the DFE's postcursors left to
    it) one sample either side of the pulse peak, ``R`` from the pulse at
    the symbol power plus the receiver's and the ADC's noise. On example 31's
    link 2.4e-5 per update at mu 1e-3: the time engine's CDR error over kp_shift
    6-9 at mu 1e-3 and 1e-2 fits 3e-5 / 3e-4 (ROADMAP P2 #29). 0 unless the
    FFE adapts by LMS."""
    from ..dsp import channel_cursors, mmse_ffe
    from ..dsp.ffe import _conv_matrix

    fcfg = cfg.rx.ffe
    n_taps = fcfg.n_pre + 1 + fcfg.n_post
    if cfg.rx.arch != "adc_dsp" or fcfg.adapt != "lms" or fcfg.mu <= 0 or n_taps < 2:
        return 0.0
    osr = cfg.osr
    peak = int(np.argmax(np.abs(pulse_pre.y)))
    n_pre_c, n_post_c = fcfg.n_pre + 4, fcfg.n_post + 12
    power = float(np.mean(_levels(cfg) ** 2))
    noise_var = cfg.rx.noise_rms ** 2 + adc_noise_sigma(cfg) ** 2

    def cursors(shift: int) -> np.ndarray:
        return channel_cursors(pulse_pre, osr, n_pre_c, n_post_c, peak_idx=peak + shift)

    def optimum(shift: int) -> np.ndarray:
        return mmse_ffe(cursors(shift), n_pre_c, n_taps, fcfg.n_pre, noise_var=noise_var / power,
                        normalize=False, free_post=n_dfe)

    wp = optimum(1) - optimum(-1)
    if not np.any(wp):
        return 0.0
    m = _conv_matrix(cursors(0), n_taps)
    r = power * (m.T @ m) + noise_var * np.eye(n_taps)
    lam = float(wp @ r @ wp / (wp @ wp))
    return float(max(int(cfg.rx.adc.n_lanes), 1) * fcfg.mu * lam)


def _over_run(profs, er: ErrorResponse, sol: LoopSolution, first: int, last: int,
              n_sym: int, ui: float) -> tuple[float, ...] | None:
    """The RMS sampling error [UI] at ``_RUN_POINTS`` points spread over the
    run: the profiles' share as the loop rings up from rest
    (``ErrorResponse.run_sq_at``), scaled so its mean square is the run's,
    the rest (white jitter, the loop's own noise) as it is. Example 31's
    loop at kp_shift 8-9 goes from 0.36 to 0.77-0.90 samples over a 200k
    run, and a Gaussian of the mean read the time engine 0.3-0.5x where the
    mean over the points reads it 0.5-0.85x (ROADMAP P2 #28). None without
    a profile (nothing rings up then)."""
    if all(p is None for p in profs) or last <= first:
        return None
    pts = first + (np.arange(_RUN_POINTS) + 0.5) / _RUN_POINTS * (last - first + 1)
    ms_mean = lambda f: er.run_mean_sq(f, first, last)    # noqa: E731
    ms_at = lambda f: er.run_sq_at(f, pts)                 # noqa: E731
    mean_var = sum((p.run_untracked_sigma_s(ms_mean, n_sym, ui) / ui) ** 2
                   for p in profs if p is not None)
    at_var = sum((np.asarray(p.run_untracked_sigma_s(ms_at, n_sym, ui)) / ui) ** 2
                 for p in profs if p is not None)
    if not mean_var > 0 or not np.mean(at_var) > 0:
        return None
    rest = max(sol.sigma_ui ** 2 - mean_var, 0.0)
    return tuple(float(v) for v in np.sqrt(rest + at_var * (mean_var / np.mean(at_var))))


def _warn_if_run_short_of_steady(sol: LoopSolution, cfg: LinkConfig) -> None:
    """The smear is what a run of ``sim.n_symbols`` leaves, the loop starting
    from rest -- what the time engine's run of that length shows. An
    underdamped loop rings up to more than that only after ``~Q / (pi f)``;
    when its steady state (``sigma_steady_ui``) is well above the run's the
    BER here is the run's, and a longer run -- or the link in service --
    is worse."""
    steady = sol.sigma_steady_ui
    if steady is None or steady <= _RUN_STEADY_WARN * sol.sigma_ui:
        return
    import warnings

    warnings.warn(
        f"the CDR loop rings: its sampling error settles at {steady:.4f} UI RMS, "
        f"{steady / max(sol.sigma_ui, 1e-12):.2f}x the {sol.sigma_ui:.4f} UI a run of "
        f"{int(cfg.sim.n_symbols)} symbols from rest leaves (its jitter peaking takes longer "
        "than the run to build) -- this BER is the run's, like the time engine's; a longer "
        "run and the link in service see more. Lower kp_shift (more damping) or run longer",
        stacklevel=3)


_BB_DAMPING_WARN = 0.25


def _warn_if_underdamped(sol: LoopSolution) -> None:
    """A bang-bang loop's linearised gain falls as its error grows, and its
    damping with it (``cdr.linear.loop_damping``): below about 0.25 the
    kernel rings up and slips cycles -- on the NRZ 16G preset it held at
    0.34 (kp_shift 7) and slipped at 0.17 (kp_shift 8: 0.46 UI RMS of
    phase, BER 2.2x this engine's), which the fixed point, converging to a
    finite error, does not show (ROADMAP P2 #32)."""
    zeta = sol.damping
    if zeta is None or zeta >= _BB_DAMPING_WARN:
        return
    import warnings

    warnings.warn(
        f"the bang-bang loop's damping ratio is {zeta:.2f} (kp sqrt(k_pd / ki) / 2 at its "
        f"linearised gain {sol.k_pd:.2f}/UI), below {_BB_DAMPING_WARN}: the kernel slipped "
        "cycles at 0.17 and held at 0.34 (NRZ 16G preset, kp_shift 8 / 7); this engine's "
        "loop model converges to a finite error here and its BER is optimistic -- lower "
        "kp_shift (a larger proportional step) or raise ki_shift", stacklevel=3)


def _warn_if_slew_limited(prof, loop: LoopParams, f_lo: float, sol: LoopSolution,
                          cfg: LinkConfig) -> None:
    """A bang-bang loop moves at most ``kp`` per update on half the updates
    (transitions); a Mueller-Muller loop is linear but clamps. The wander the
    loop *tries* to follow is the profile inside its bandwidth, so the rate is
    integrated up to there -- the fast part of a 1/f^2 profile is left on the
    sampler, not chased. If that rate is a sizeable part of the slew limit the
    kernel slips cycles and the linear model is not describing what it does.
    Measured on the 16 GBd NRZ cross-check link: locked up to ~0.3 of the
    limit, slipping observed from ~0.4; the warning starts at 0.3 for margin."""
    import warnings

    rate = prof.rms_rate_ui_per_s(f_lo, sol.bandwidth_hz)      # s/s = UI/UI
    rate_per_update = rate * cfg.symbol_rate / loop.f_update   # UI per loop update
    limit = loop.kp_ui * 0.5
    if rate_per_update > 0.3 * limit:
        warnings.warn(
            f"tx.clock profile wanders at {rate_per_update:.2e} UI per CDR update (RMS, "
            f"inside the {sol.bandwidth_hz / 1e6:.1f} MHz loop bandwidth) against a loop "
            f"that can follow {limit:.2e}: the time-domain CDR is in or near its "
            "slew-limited regime, where the statistical engine's linearised loop model "
            "does not apply", stacklevel=3)
