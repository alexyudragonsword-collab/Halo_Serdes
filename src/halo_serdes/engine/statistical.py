"""Statistical (StatEye-style) BER engine.

Under the LTI + stationary-noise assumption, the slicer-input distribution is
computed *exactly* (to bin resolution) by convolving the per-cursor symbol
distributions of the equalized pulse response:

    ISI PDF = conv over cursors k != main of  (1/M) sum_m delta(v - c_k a_m)

then convolved with the analytic Gaussian noise kernel, evaluated at every
sampling phase within the UI. BER(phase, threshold) follows from the CDF —
extrapolation to arbitrarily low BER without Monte-Carlo, the capability none
of the three reference libraries has.

Non-LTI approximations (each cross-checked against the time engine):
- DFE: ideal cancellation of the covered postcursors (weights assumed exact),
  with its error propagation on top (plain slicer path): a wrong decision
  leaves h1 x the level step on the next sample; p = the SER with the input
  shifted that way, and the SER s becomes s / (1 - p + s), the two-state
  chain's (``extras['dfe_ep_factor']``; first tap only -- the 1-tap
  Gilbert model, cairn/多lane建模.md §2). Without it the ADC receiver with
  its DFE engaged read 0.35-0.55x the time engine; with it 0.81-1.25x;
- FFE: noise enhancement sigma_eq = sigma * ||w||_2;
- ADC receiver: the ADC's noise, fullscale^2/12 * 2^(-2 ENOB) (bare
  quantisation without an ENOB), joins the receiver noise at the FFE input
  (``adc_noise_sigma``); clipping and TI lane mismatch are not modelled;
- sampling jitter: BER(phi) averaged over a Gaussian sampling instant on
  the phase axis: the receiver clock's RJ, and on a bang-bang loop -- which
  hunts on any clock -- its own wander, white clocks included (until
  2026-10-15 a white clock had no loop model, and the mixed-signal reading
  was 0.1-0.7x the time engine's with transmit RJ, ROADMAP P2 #27). The
  loop moves faster than the decision feedback adapts, so under the smear
  the DFE stays at the taps of the phase read and the postcursors it misses
  elsewhere stay ISI; the bathtub (``ber_phi``) re-adapts at every phase.
  Where the smear is under two samples that curve is also taken between
  samples (the pulse interpolated): a steep bathtub read linearly between
  them came out 1.6-3.6x high half a sample off the lock. The PAM4
  slicer's thresholds stay with the taps, as the time engine sets its levels
  once: re-placed at each phase's own main cursor (until 2026-10-15) the
  curve read the time engine frozen off the lock 0.70-0.74x on example 31
  and 0.42-0.79x on the PAM4 32G preset, held 0.95-1.20x (ROADMAP P2 #29).
  They sit at the main cursor where the receiver set its levels: an ADC
  receiver's LMS FFE pulls its output to them where it samples, but the
  mixed-signal time engine takes them at the pulse peak, two samples before
  its bang-bang loop locks on the PAM4 32G preset -- held at the lock that
  preset read 0.70-0.95x the time engine, at the peak 0.94-1.15x (ROADMAP
  P2 #30; whether that receiver should adapt its levels is ROADMAP P2 #31).
  A lane read at an offset (``phase_ui``) has them adapted there, as
  ``analysis.skew`` takes its equalisers. An ADC receiver's LMS FFE takes up part
  of a profile clock's slow wander (the coloured-clock entry): example 31's
  link over kp_shift 4-9, two clocks and the LMS step 0-1e-2 reads 0.68-1.6x
  against 12 seeds, the high end on 12-18 errors (the FFE left out, as until
  then, ~12x at mu 1e-2, kp_shift 9).
  The transmitter's white RJ is not a sampling shift -- each edge moves on
  its own -- and comes in as noise that follows the transitions
  (``tx_edge_jitter``): binned by the sampled symbol's neighbours, its own
  two edges exact, the rest linearised, per phase; it also randomises a
  bang-bang detector's edges. White SJ and DCD are not modelled, nor an ADC
  receiver's Mueller-Muller loop on white clocks (its own noise 0.0001-0.006
  UI on the presets). The Gaussian is the remaining approximation: on the
  NRZ 16G preset's steep bathtub the time engine's own phase distribution
  reads 1.15-1.4x what a Gaussian of its sigma does;
- receive-side partial response (``cfg.pr``): the controlled cursor (the
  first postcursor of the FFE-shaped pulse, whatever it is at each phase)
  leaves the ISI PDF; with a sequence detector it joins the trellis cursors
  [1, alpha, r...], without one the per-symbol decision cancels it (below). The
  DFE then starts after it. With a sequence detector the BER is not the
  plain MLSD's minimum-distance gain but a union bound over the alternating error
  events, in the noise the shaping FFE coloured, each event weighted by its
  share of what the detector decides among all the events that hold with it
  (``pr_error_events``): the minimum distance alone was 2.5-20x optimistic
  against the time engine, the plain sum up to 2.9x pessimistic (transmit
  a = 0.5), each event less its overlap with the one before 0.78-2.09x.
  Each event sees the residual ISI (what is left outside the trellis)
  projected on its own difference, as its noise is: at a single sample's
  size it read 2-12x pessimistic behind a 3-7 tap FFE, which leaves that
  residual large. Now 0.60-1.08x across receive and transmit PR, two to
  four cursors, at BER 7e-5 to 2e-2, and 0.95-1.53x behind a 3-7 tap FFE
  (where the time engine counted 28 errors or more; against the time
  engine's Viterbi on a trellis fitted where the receiver samples).
  An optical link's two binned neighbours stay a full-size shift (their
  noise is per level, not per event). On an optical link the union's
  overlap weights are sampled at the SNR of all its noise, the optical
  share averaged over levels (at the electrical share alone the events
  looked disjoint and the union became the plain sum: 1.5-3.0x pessimistic
  behind transmit PR, now 0.83-1.44x over transmit and receive PR). Each
  event still takes the noise of the level and neighbours of its first
  symbol for all its samples; averaging every sample's own, which the
  noise really does along an event, would lower these 0.6-0.8x (offline).
  The per-symbol decisions the LMS and
  the CDR read (and, without a sequence detector, the detector) take the
  controlled cursors off with earlier decisions, errors included: a Markov
  chain over the last decision errors (``pr_symbol_decisions``) gives their
  SER, reported as ``extras['ser_slicer']``; as an ideal tap it was 2.7-4.1x
  optimistic, now 0.8-1.6x. Its effect on the LMS and the CDR is not modelled:
  the MM CDR reads the residual those decisions leave, and once
  ``extras['pr_loop_load']`` (their SER x the controlled cursors' energy)
  passes 0.09-0.14 the time engine's loop cycle-slips -- warned from 0.08;
- sampling phase: each receiver is read where its loop locks, not at the
  bathtub minimum (``extras['ber_min_phase']`` keeps the minimum). The
  mixed-signal bang-bang loop settles where its edge samples balance
  (``cdr.linear.lock_offset_samples``); with a DFE that is 0.1-0.2 UI and up
  to 8x in BER from the minimum. The ADC's Mueller-Muller loop locks where the
  pre-FFE pulse has h(-1) = h(+1) on raw samples, and on equalised samples
  settles with the LMS FFE on the pre-FFE pulse peak; on a narrow bathtub
  (example 32's CPO with a host FFE through a c = 0.5 laser) that is one
  sample and 3x from the minimum (until 2026-10-09 the ADC was read at the
  minimum). ``phase_ui`` reads a set offset from the lock point instead (a
  lane on another lane's clock, ``analysis.skew``); the clock jitter smear
  is still the lane's own loop's;
- coloured clock (``tx.clock.kind = "profile"``): the CDR is linearised
  (``cdr/linear.py``) and the smear sigma is the profile power the loop does
  not track plus the jitter it acquires from its own detector noise -- the
  profile as a run of N symbols holds it, on the lines ``k / (N UI)``, the
  loop starting from rest. An underdamped loop's jitter peaking builds over
  ``~Q / (pi f)``; example 31's at kp_shift 8-9 (``|E|`` 8-16x near 1.1 MHz)
  has not rung up within 200k symbols, its steady state reading the time
  engine's 16-seed RMS 1.34-1.70x where the run reads it 0.98-0.99x. Its
  error grows over the run, so the read is the mean over points of it
  (``LoopSolution.sigma_over_run_ui``); the steady state is reported
  (``sigma_steady_ui``) and warned when it is over 1.25x the run's. An ADC
  receiver's LMS FFE moves its taps along their timing derivative and so
  follows the offset itself, ``n_lanes * mu * lambda'`` of what is left per
  update (``_ffe_lms_track``; 2.4e-5 on example 31 at mu 1e-3, the time
  engine's CDR error fitting 3e-5): with the detector on its output a
  second tracking path beside the loop, on raw samples a high-pass after it
  (``LoopParams.ffe_track``). Without it the model read the CDR error up to
  1.3x high at mu 1e-2; with it 0.93-1.01x over kp_shift 4-9, two clocks
  and mu 0-1e-2 (ROADMAP P2 #29). The bang-bang detector's gain is set by the
  jitter at its input, which here is the untracked clock, the loop's own
  hunting, and AWGN + ISI at the edge sample converted through the edge
  slope of the pre-DFE pulse (first order; a lossy channel's data-dependent
  edge shape is only in it as variance). A receiver clock (``rx.clock``)
  rides through the same error response, since the loop tracks the
  difference of the two clocks; on the bang-bang loop that also means the
  bandwidth shrinks with the *total* input, so two clocks' residues do not
  simply add there (they do on the linear Mueller-Muller loop). The
  Mueller-Muller detector on equalised samples reads the slicer input, the
  DFE's lock-point postcursors taken out; its noise is the density at low
  frequency, white for the receiver's share and, for a pattern that repeats
  (``sim.pattern`` PRBS-N short enough), lines at multiples of its
  repetition rate for the data's -- so the self-noise follows the pattern
  the time engine runs, a tenth on PRBS13Q of what random data gives. A loop
  driven past its slew limit slips cycles, as does a bang-bang loop whose
  detector sees more than ~0.1 UI of phase error; neither is in a linear
  model and both raise a warning. Nor is a Mueller-Muller timing function
  that turns over close to the lock point: past the turnover the pull back
  weakens and past its next zero the loop runs away for good, so the
  turnover's distance (``LoopSolution.hold_range_ui``) is compared with the
  loop's own wander (``sigma_wander_ui``: its detector noise and the clock
  it follows late inside its bandwidth) and warned below 8x -- on the
  fingerprint's 53 Gb/s link, whose function turns over 0.026 UI out, the
  time engine lost lock at 2.8-6.9x and never from 8.6x; the ADC presets sit
  at 15-33x on random symbols. An ADC receiver on white clocks has no loop
  model and so no such check. A bang-bang detector's transitions are taken
  by class (``_bb_transitions``): PAM4's sign changes between unequal
  magnitudes cross 0.14-0.15 UI off the lock point and bring noise, not
  gain -- taken at the lock the hunting read 0.65-0.72x the kernel's. That
  noise is set by the data, +1 from the larger magnitude to the smaller and
  -1 back, and consecutive transitions share a symbol: a loop sees half its
  power at low frequency on random data and lines with nothing at zero
  frequency on a repeating pattern (``bb_data_noise``). Counted white, the
  hunting on the PAM4 32G preset read the kernel's 1.11-1.23x, now
  0.97-1.01x (ROADMAP P2 #30);
- crosstalk: aggressor cursor sets convolved in as independent stationary
  interference, read where the time engine's victim samples them -- on the
  victim's sampling grid at each phase, the aggressors launching on the
  victim's symbol grid delayed by their ``skew_s`` (synchronous lanes), and
  through the FFE on an ADC receiver (the time engine adds them before the
  ADC). Cursors below 1e-4 of an aggressor's largest are dropped.
  Quasi-synchronous lanes (``ppm`` != 0: the aggressor's phase drifting
  through the UI) are averaged over the phase -- the time engine holds their
  symbols at their own rate -- which assumes the run sweeps whole UIs
  (warned when it sweeps less than one). Until 2026-10-13 every aggressor was read at its own peak,
  all at their worst phase at once: 1.2-41x against the time engine as the
  skew moved (cairn/多lane建模.md §3);
- optical topology with a large-signal E/O curve (``li_compression`` > 0):
  the curve's steady-state levels stand in for the transmitted ones and the
  chain stays linear, plus a per-phase, per-bin shift for how the curve
  bends ISI (``optical_stage.curve_pattern_offsets``: the curve sits after
  the E/O dynamics, so it bends a waveform that already carries the
  neighbours). The bins are the optical noise's (the symbol and its two
  nearest neighbours), which carry 92-96 % of that bending; the rest enters
  as variance. Levels alone read 0.30-0.40x of the time engine at c = 0.5
  on EML and NRZ links; with the shift, 0.86-1.28x over c = 0..0.5;
- Tx DAC (``tx.dac_bits``): quantisation and static INL become white noise
  per UI at the DAC output, sigma_q^2 = LSB^2 / 12 + E[INL^2] LSB^2, carried
  to the slicer through the Tx-to-slicer symbol response (every cursor's
  square, so the channel's attenuation and the FFE's noise gain are in it).
  The error is really a deterministic function of the DAC input; it looks
  white once an FFE spreads the input over many codes. Without a Tx FFE the
  four PAM4 levels sit on four fixed codes and the "noise" is a fixed level
  offset this engine averages instead;
- Tx driver pole (``tx.bw``): LTI, so not an approximation -- it is in the
  pulse through ``TxPipeline.equivalent_symbol_response`` and in the DAC
  error's path through ``after_dac_response``, the same sampled impulse the
  receivers' pulse analysis uses;
- Tx driver nonlinearity (``tx.drv_nl``): not modelled here, the time
  engine's alone, with a warning (outside the 2x cross-check);
- optical topology (``cfg.topology``): the photodiode's shot and RIN noise
  depend on the optical power of the level being received, so the Gaussian
  kernel becomes one kernel per level, each at the *nominal* level power
  (ISI moves the instantaneous power around that; the time engine follows
  the waveform, this engine does not -- the 2x cross-check is what bounds the
  difference). Which neighbours were sent moves the light along the receive
  filter's memory, so each level's noise is a scale mixture, wider in the
  tails than one matched-variance Gaussian (0.65-0.76x optimistic at ER
  6-7.5 dB). The two nearest neighbours are therefore binned: per level and
  per neighbour pattern one kernel (``slicer_sigma_binned``), their cursors a
  fixed shift instead of random ISI; the rest stays a single variance.
  Electrical links keep the single kernel, byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from ..afe import Ctle
from ..cdr.linear import (
    ErrorResponse, LoopParams, LoopSolution, bb_loop_fixed_point, error_response, lock_offset_samples,
    mm_hold_range_ui, mm_loop_solution, mm_pd_statistics,
)
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.mapping import GRAY_BIT_DISTANCE
from ..core.sampler import baud_samples, upsampled_taps
from ..core.waveform import Waveform
from ..dsp.mlsd import mlse_min_distance_sq
from ..tx.pipeline import TxPipeline
from .static_link import _levels, make_pattern


@dataclass
class StatResult:
    ber_phi: np.ndarray          # BER vs sampling-phase offset (osr points)
    ser_phi: np.ndarray
    best_phi: int                # index into the phase axis where the receiver samples (0 = pulse peak)
    ber: float                   # there; the bathtub's floor is extras['ber_min_phase']
    ser: float
    eye_pdf: np.ndarray          # (v_bins, osr) slicer-input PDF vs phase
    v_centers: np.ndarray
    phi_ui: np.ndarray           # phase axis in UI relative to pulse peak
    extras: dict = field(default_factory=dict)


#: Cursor offsets whose symbols the optical noise is binned on: the next
#: symbol sent (-1) and the previous one (+1). They move the most light
#: along the receive filter's memory.
_BINNED_NEIGHBOURS = (-1, 1)


def _shift_add(dst: np.ndarray, src: np.ndarray, shift_bins: float, weight: float) -> None:
    """dst += weight * src shifted by fractional bins (linear interpolation,
    edge clipping — no wraparound)."""
    i0 = int(np.floor(shift_bins))
    frac = shift_bins - i0
    for off, w in ((i0, (1.0 - frac) * weight), (i0 + 1, frac * weight)):
        if w == 0.0:
            continue
        if off >= 0:
            n = src.size - off
            if n > 0:
                dst[off:] += w * src[:n]
        else:
            n = src.size + off
            if n > 0:
                dst[:n] += w * src[-n:]


def isi_pdf(cursor_amps: np.ndarray, levels_norm: np.ndarray,
            v_centers: np.ndarray) -> np.ndarray:
    """Distribution of sum_k c_k * a_k over equiprobable symbol levels."""
    dv = v_centers[1] - v_centers[0]
    pdf = np.zeros(v_centers.size)
    pdf[v_centers.size // 2] = 1.0  # delta at 0 (grid is symmetric)
    m = levels_norm.size
    for c in cursor_amps:
        if abs(c) < dv * 1e-6:
            continue
        new = np.zeros_like(pdf)
        for a in levels_norm:
            _shift_add(new, pdf, c * a / dv, 1.0 / m)
        pdf = new
    return pdf


def _pr_patterns(families: tuple[str, ...], max_len: int) -> list[np.ndarray]:
    """The error patterns ``pr_error_events`` runs over, in its order: per
    family, lengths 1..max_len (a constant run of 1 is the alternating one)."""
    return [np.where(np.arange(n) % 2 == 0, 1.0, -1.0) if fam == "alternating" else np.ones(n)
            for fam in families for n in range(1, max_len + 1)
            if not (fam == "constant" and n == 1)]


def pr_event_differences(cursors: np.ndarray, families: tuple[str, ...] = ("alternating",),
                         max_len: int = 12, state_memory: int | None = None) -> list[np.ndarray]:
    """The difference each of ``pr_error_events``' events makes to the
    detector's expected samples (pattern * cursors, cut where a reduced-state
    trellis merges), in its order and in units of the main cursor."""
    h = np.asarray(cursors, dtype=float)
    out = []
    for e in _pr_patterns(families, max_len):
        dd = np.convolve(e, h)
        if state_memory is not None:
            dd = dd[:e.size + state_memory]
        out.append(dd)
    return out


def pr_error_events(cursors: np.ndarray, noise_acf: np.ndarray, n_levels: int,
                    precoded: bool, max_len: int = 12,
                    snr: float | None = None,
                    families: tuple[str, ...] = ("alternating",),
                    n_samples: int = 1000, seed: int = 0,
                    state_memory: int | None = None) -> list[tuple[float, float]]:
    """(distance gain, weight) per error event of a sequence detector on a
    partial-response target, for a union bound.

    The minimum distance alone is not enough here, for two reasons the time
    engine shows. The FFE that shapes the pulse into [1, alpha] colours the
    noise (rho1 = -0.25, rho2 = -0.31 for [1, 1] on example 18's channel),
    and the Viterbi metric is Euclidean, so an event d is missed with
    Q(|d|^2 / (2 sigma sqrt(d' R d))), not Q(|d| / 2 sigma). And with alpha
    near 1 every alternating event (+1, -1, +1, ...) of any length L is near
    the minimum distance, so they add up. Events are one level step per
    symbol, alternating in sign; the data supports one of length L with
    probability ((M - 1) / M)^(L - 1) relative to a single error (which the
    per-level threshold sum already weighs). Each costs L symbol errors, or,
    precoded, as many as the mod-M sum of neighbouring errors leaves (2 for
    any alternating event).

    The gain is relative to the memoryless slicer's half-distance; with white
    noise and L = 1 it is |d|, the plain MLSD path's sqrt(d_min^2).

    Without ``snr`` the weight is that support times the errors: a plain sum
    over the events, which counts one detector error once for every event
    whose metric it also beats. Where the distances of many lengths sit close
    together (behind a transmit 1 + 0.5D, rho1 = -0.67, lengths 2-6 within
    10 % of each other) that is 2.5-2.9x pessimistic. So with ``snr`` (the
    half-distance over the noise sigma) each weight is the event's share of
    what the detector actually decides when its metric condition holds: the
    competing events are all of them -- every length, both families, and
    every start that overlaps (an error of length 4 contains the opposite-
    sign one of length 3 that starts a symbol later, which the per-start
    sum would count again) -- and the detector takes the one that beats the
    correct path by the most. Sampling the noise given event j's condition
    (and the symbols given its moves are possible), its weight is support_j
    times the mean of errs(chosen) / (number of events whose condition holds
    and whose moves the symbols allow). Summed against each event's own
    probability, sum_j P(A_j) w_j is then the expected number of decided
    symbol errors per start, the union and not the sum: the at-least-one-event
    estimator of Owen, Maximov and Chertkov (2019), whose relative error stays
    bounded however rare the events. The ISI around the events is left out
    of the weights, as the per-level sum adds it to each P(A_j), projected on
    the event (``pr_event_differences``).

    The chained pairwise overlap this replaced (each L less its intersection
    with L - 1) read 0.78-2.09x the time engine on 16 PR links, up to 2.09x
    with a negative third cursor; this 0.67-1.55x
    (``cairn/DSP发端与PR.md`` §14), and 0.58-1.08x once each event's ISI
    was projected on it (§19).

    ``families`` adds ``"constant"`` (+1, +1, ...): with a negative
    controlled cursor after the first (b or c < 0) a run of same-sign errors is
    pulled close by it, where for a non-negative target it lies far.
    ``n_samples`` per event and ``seed`` fix the sampling (deterministic).

    ``state_memory`` K: a reduced-state (DFSE) trellis compares two paths only
    until they merge, K symbols after the event's last error, so an event of
    n symbols counts the first n + K samples of its difference
    (``dsp.mlsd.mlse_min_distance_sq``); the survivors' feedback errors are
    not in it.
    """
    from scipy.special import ndtr, ndtri

    h = np.asarray(cursors, dtype=float)
    rho = np.asarray(noise_acf, dtype=float) / float(noise_acf[0])
    pats = _pr_patterns(families, max_len)
    if precoded:
        errs_p = np.array([np.count_nonzero(np.convolve(e, [1.0, 1.0]).round().astype(int) % n_levels)
                           for e in pats], dtype=float)
    else:
        errs_p = np.array([e.size for e in pats], dtype=float)
    supp_p = np.array([((n_levels - 1) / n_levels) ** (e.size - 1) for e in pats])
    # every pattern at every start that can overlap one starting at 0 (both
    # signs; at 0 the per-level sum covers the sign), in one noise window
    nb = max_len - 1 if snr is not None else 0
    size = 2 * nb + max_len + h.size - 1
    lag = np.abs(np.arange(size)[:, None] - np.arange(size)[None, :])
    r_mat = np.where(lag < rho.size, rho[np.minimum(lag, rho.size - 1)], 0.0)
    start, sign, pat = [], [], []
    for k in range(len(pats)):
        for st in range(-nb, nb + 1):
            for sg in (1.0, -1.0):
                if st == 0 and sg < 0.0:
                    continue
                start.append(st)
                sign.append(sg)
                pat.append(k)
    start, sign, pat = np.array(start), np.array(sign), np.array(pat)
    d = np.zeros((pat.size, size))
    for i in range(pat.size):
        dd = np.convolve(sign[i] * pats[pat[i]], h)
        if state_memory is not None:
            dd = dd[:pats[pat[i]].size + state_memory]
        d[i, nb + start[i]: nb + start[i] + dd.size] = dd
    s = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", d, r_mat, d), 1e-300))
    g = np.einsum("ij,ij->i", d, d) / s
    base = np.flatnonzero(start == 0)                    # one per pattern, in order
    if snr is None:
        return [(float(g[i]), float(supp_p[pat[i]] * errs_p[pat[i]])) for i in base]

    # whitened unit directions: event i's statistic is a_i . w, w ~ N(0, I)
    ev, evec = np.linalg.eigh(r_mat)
    a = (d @ (evec * np.sqrt(np.clip(ev, 0.0, None)))) / s[:, None]
    t = g * snr                                          # its condition: a_i . w > t_i
    errs = errs_p[pat]
    n_sym = 2 * nb + max_len                             # symbols the events can move
    up = np.zeros((pat.size, n_sym))
    dn = np.zeros((pat.size, n_sym))
    for i in range(pat.size):
        e = sign[i] * pats[pat[i]]
        cols = nb + start[i] + np.arange(e.size)
        up[i, cols[e > 0]] = 1.0
        dn[i, cols[e < 0]] = 1.0
    rng = np.random.default_rng(seed)
    out = []
    for j in base:
        # the events that can hold together with j: given j's statistic at
        # its threshold, the rest are left out once below 1e-6
        rho_j = a @ a[j]
        q = np.sqrt(np.maximum(1.0 - rho_j ** 2, 1e-12))
        keep = ndtr(-(t - rho_j * (t[j] + 1.0 / max(t[j], 1.0))) / q) > 1e-6
        keep[j] = True
        idx = np.flatnonzero(keep)
        jj = int(np.flatnonzero(idx == j)[0])
        # the noise given j's condition: its statistic from the normal tail
        # beyond t_j, the rest of w as it is
        z = -ndtri(np.maximum(rng.random(n_samples) * ndtr(-t[j]), 1e-300))
        w = rng.standard_normal((n_samples, a.shape[1]))
        w += np.outer(z - w @ a[j], a[j])
        stat = w @ a[idx].T
        # symbols uniform, given the moves j makes are possible
        lo = np.where(dn[j] > 0, 1, 0)
        hi = np.where(up[j] > 0, n_levels - 1, n_levels)
        sym = lo + (rng.random((n_samples, n_sym)) * (hi - lo)).astype(int)
        blocked = ((sym >= n_levels - 1).astype(float) @ up[idx].T
                   + (sym <= 0).astype(float) @ dn[idx].T)
        hold = (stat > t[idx]) & (blocked == 0)
        hold[:, jj] = True
        # the detector's choice: the path that beats the correct one by the
        # most, metric difference 2 sigma_d s_i (stat_i - t_i)
        chosen = np.argmax(np.where(hold, s[idx] * (stat - t[idx]), -np.inf), axis=1)
        share = errs[idx][chosen] / hold.sum(axis=1)
        out.append((float(g[j]), float(supp_p[pat[j]] * share.mean())))
    return out


def pr_symbol_decisions(pdf_lv: list, v_centers: np.ndarray, lv: np.ndarray,
                        head, composite: bool, precoded: bool) -> tuple[float, float]:
    """(SER, bit errors per symbol) of the per-symbol decisions a receive
    partial-response target leaves the LMS and the CDR, or the detector
    itself when there is no sequence detector.

    Those decisions take the controlled cursors off with the receiver's own
    previous decisions (sample - a x_{k-1} - b x_{k-2}), so they are a DFE
    on those cursors, errors and all: one wrong decision leaves a times its
    error on the next sample, and at a = 1 that is a whole level step. Taken
    as an ideal tap the time engine's figure was 2.7x (a = 0.5) to 4.1x
    (a = 1) above this engine's. Here the errors are a Markov chain: the
    state is the last one or two decision errors (in level steps), the next
    error's distribution is the per-level tail of the ISI + noise PDF shifted
    by what the state leaves, averaged over a uniform symbol, and the SER is
    its stationary rate.

    ``composite`` is precoded 1 + D at a = 1 as the kernel runs it: the
    sample is sliced to the 2N - 1 levels x_k + x_{k-1} and the user symbol
    is that mod N, so an error does not propagate; only the composite levels'
    unequal occupancy (inner ones are hit more often) differs from a plain
    slicer. ``precoded`` without ``composite`` scores user symbols
    (x_k + x_{k-1}) mod N off a propagating chain.

    ``pdf_lv`` is the ISI + noise PDF per transmitted level (on
    ``v_centers``) and ``lv`` the levels at the slicer; the levels are taken
    as evenly spaced. Bits per wrong symbol follow the checker: the Hamming
    distance of the two symbols' Gray codes (``GRAY_BIT_DISTANCE``).
    """
    n = lv.size
    order = np.argsort(lv)
    ls = lv[order]
    sym = order                                  # sorted index -> symbol index
    step = float(np.mean(np.diff(ls))) if n > 1 else 1.0
    cdf = [np.cumsum(pdf_lv[int(o)]) for o in order]

    def decide(j: int, levels: np.ndarray, center: float) -> np.ndarray:
        """P(decision = k) per slicer level, the sample at ``center`` with
        sorted symbol j's ISI + noise around it (same bins as the slicer
        tails in run_statistical)."""
        edges = 0.5 * (levels[1:] + levels[:-1]) - center
        i = np.searchsorted(v_centers, edges) - 1
        f = np.where(i >= 0, cdf[j][np.clip(i, 0, v_centers.size - 1)], 0.0)
        return np.diff(np.concatenate([[0.0], f, [1.0]]))

    def bits(u: int, v: int) -> int:
        return int(GRAY_BIT_DISTANCE[int(u), int(v)])

    if composite:
        comp = 2 * ls[0] + step * np.arange(2 * n - 1)
        # composite index in symbol order (an inverting slicer reverses it)
        c_sym = np.arange(2 * n - 1) if sym[0] == 0 else np.arange(2 * n - 2, -1, -1)
        ser = nbe = 0.0
        for j in range(n):                       # current symbol, sorted
            for i in range(n):                   # previous one
                c = j + i
                p = decide(j, comp, comp[c])
                ser += (1.0 - p[c]) / n ** 2
                for k in range(2 * n - 1):
                    if k != c:
                        nbe += p[k] * bits(c_sym[c] % n, c_sym[k] % n) / n ** 2
        return float(ser), float(nbe)

    h = np.asarray(head, dtype=float)[1:]
    depth = h.size
    vals = np.arange(-(n - 1), n)                # decision error = sent - decided index
    states = [()] if depth == 0 else [tuple(int(v) for v in t) for t in np.array(
        np.meshgrid(*([vals] * depth), indexing="ij")).reshape(depth, -1).T]
    index = {st: k for k, st in enumerate(states)}
    trans = np.zeros((len(states), len(states)))
    p_err = np.zeros(len(states))
    b_err = np.zeros(len(states))
    for k, st in enumerate(states):
        offset = step * float(np.dot(h, st)) if depth else 0.0
        dist = np.zeros(vals.size)               # P(error = e) at this state
        for j in range(n):
            p = decide(j, ls, ls[j] + offset)
            for d in range(n):
                dist[j - d + n - 1] += p[d] / n
                if not precoded and j != d:
                    b_err[k] += p[d] * bits(sym[j], sym[d]) / n
        for e, pe in zip(vals, dist):
            nxt = ((int(e),) + st[:-1]) if depth else ()
            trans[k, index[nxt]] += pe
            if precoded:
                # the user symbol (x_k + x_{k-1}) mod N is wrong unless the
                # two errors cancel mod N; bits over a uniform user symbol
                delta = (int(e) + (st[0] if depth else 0)) % n
                if delta:
                    p_err[k] += pe
                    b_err[k] += pe * float(np.mean([bits(u, (u - delta) % n) for u in range(n)]))
            elif e:
                p_err[k] += pe
    # stationary distribution of the error chain
    a_mat = trans.T - np.eye(len(states))
    a_mat[-1] = 1.0
    rhs = np.zeros(len(states))
    rhs[-1] = 1.0
    pi = np.clip(np.linalg.solve(a_mat, rhs), 0.0, None)
    pi = pi / pi.sum()
    return float(pi @ p_err), float(pi @ b_err)


def gaussian_kernel(sigma: float, dv: float, n_sigma: float = 8.0) -> np.ndarray:
    if sigma <= 0:
        return np.array([1.0])
    half = max(1, int(np.ceil(n_sigma * sigma / dv)))
    x = np.arange(-half, half + 1) * dv
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def adc_noise_sigma(cfg: LinkConfig) -> float:
    """RMS noise of the ADC at its input [V]: the n-bit quantiser's
    step^2 / 12, plus the excess that ``afe.adc.TiAdc`` adds to realise the
    configured ENOB, which together are fullscale^2 / 12 * 2^(-2 ENOB). Clipping
    and lane mismatch are not in it."""
    acfg = cfg.rx.adc
    bits = acfg.enob if acfg.enob is not None and acfg.enob < acfg.n_bits else acfg.n_bits
    return float(acfg.fullscale / np.sqrt(12.0) * 2.0 ** (-bits))


def _dac_cursors(h: np.ndarray, dt: float, osr: int) -> np.ndarray:
    """The symbol response from the DAC output to the slicer, one sample per
    UI at the main cursor's phase."""
    from ..core.sampler import baud_samples

    p = pulse_from_impulse(Waveform(h, dt), osr).y
    peak = int(np.argmax(np.abs(p)))
    return baud_samples(p, osr, peak % osr)


def tx_noise_covariance(tx_pipe, h: np.ndarray, dt: float, osr: int,
                        n_taps: int) -> np.ndarray | None:
    """Covariance over an FFE's ``n_taps`` inputs of the transmitter's own
    noise (``tx.noise_rms``), white per UI at the DAC output and coloured by
    everything up to the FFE (``h``: channel, CTLE, VGA, the optical path;
    the driver pole is added here): sigma^2 times the autocorrelation of the
    DAC-to-FFE symbol response, as a Toeplitz matrix. None without it. The
    DAC's quantisation is left out on purpose -- the start-up solves of
    links with a DAC stay what they were."""
    if tx_pipe.noise_rms == 0.0:
        return None
    drv = tx_pipe.after_dac_response(osr)
    d = _dac_cursors(h if drv is None else np.convolve(h, drv), dt, osr)
    acf = np.correlate(d, d, "full")[d.size - 1:]
    acf = np.pad(acf, (0, max(0, n_taps - acf.size)))[:n_taps]
    lag = np.abs(np.subtract.outer(np.arange(n_taps), np.arange(n_taps)))
    return tx_pipe.noise_rms ** 2 * acf[lag]


#: the edges whose displacement the bins treat exactly: the sampled symbol's
#: own two (the one that starts it, the one that ends it)
_EXACT_EDGES = (0, 1)


@dataclass(frozen=True)
class TxEdgeJitter:
    """The transmitter's white edge jitter at one sampling phase, per level and
    per pattern of the binned neighbours (``tx_edge_jitter``).

    ``sigma``: its Gaussian equivalent over every edge, linearised -- for what
    wants one number (the PR union bound's SNR, the voltage grid).
    ``rest``: the sigma of what is left once the sampled symbol's own two
    edges are taken out: every other edge, linearised, and the share of
    those two edges' steps the bins do not fix (a Tx FFE's).
    ``step``: the two own edges' steps given the bin, ``(..., 2)``.
    ``phi`` / ``weight``: the change at the sample per unit step for each
    displacement on a Gaussian grid, and the grid's weights.
    """

    sigma: np.ndarray
    rest: np.ndarray
    step: np.ndarray
    phi: np.ndarray
    weight: np.ndarray

    def kernel(self, combo: tuple, dv: float, scale: float = 1.0) -> tuple[np.ndarray, int]:
        """PDF on a ``dv`` grid of the two own edges' contribution for one bin,
        voltages divided by ``scale``: ``(pdf, lo)`` with ``pdf[0]`` at bin ``lo``."""
        out, lo = np.ones(1), 0
        for e in range(self.phi.shape[0]):
            x = self.step[combo + (e,)] * self.phi[e] / (scale * dv)
            if not np.any(x):
                continue
            k_lo = int(np.floor(x.min()))
            pos = x - k_lo
            i0 = np.floor(pos).astype(np.int64)
            frac = pos - i0
            pdf = np.zeros(int(i0.max()) + 2)
            np.add.at(pdf, i0, self.weight * (1.0 - frac))
            np.add.at(pdf, i0 + 1, self.weight * frac)
            out, lo = np.convolve(out, pdf), lo + k_lo
        return out, lo


def tx_edge_jitter(cfg: LinkConfig, tx_pipe, h: np.ndarray, osr: int, offset: int = 0,
                   neighbours: tuple[int, ...] = _BINNED_NEIGHBOURS,
                   n_pre: int = 24, n_post: int = 64, n_sigma: float = 8.0,
                   n_eps: int = 321) -> TxEdgeJitter | None:
    """Slicer noise of the transmitter's white edge jitter (``tx.clock.rj_ui``)
    per level and per pattern of the symbols at ``neighbours``, as
    ``slicer_sigma_binned`` lays them out; None without it.

    Each edge moves on its own: edge ``k`` displaced by ``eps_k`` adds
    ``(b_k - b_(k-1)) (s(t - kT - eps_k) - s(t - kT))`` at the sample, ``s``
    the step response of everything after the DAC (``h``: driver pole,
    channel, receiver, FFE). That is not a shift of the sampling instant --
    a common shift has, linearised, the cross terms ``-h_k h_(k+1)``, which
    nearly cancel it behind a lossy channel -- and it follows the
    transitions: zero across a run, largest on a full-swing step next to
    the symbol sampled. So the bins fix that symbol's two edges, and those
    two are taken exactly: on a fast channel the step rises within a
    fraction of a UI, and a 3-sigma displacement drags its steep part onto
    the sample. Linearised they read the NRZ 16G preset's errors 29x low at
    0.09 UI (a Monte-Carlo with the exact steps, 5.8e-5, matched the time
    engine's 6.7e-5); exact on those two and linear on the rest matched the
    all-exact one. One variance over all patterns read the 224G stress
    link 5x low (ROADMAP P2 #25). ``b`` are the transmitter's levels after
    its PR filter and FFE. ``offset``: the sample read, in samples from the
    peak of ``h``'s pulse -- the noise grows toward the edges, which is
    what the phase smear it replaces stood for.
    """
    rj = cfg.tx.clock.rj_ui
    if rj <= 0.0:
        return None
    p = pulse_from_impulse(Waveform(h, cfg.dt), osr).y
    pk = int(np.argmax(np.abs(p))) + int(offset)
    # edge n + m reaches symbol n's sample at pk - m UI (the earlier edges,
    # m < 0, carry the channel's tail); linearised, a displacement of
    # eps = rj UI = rj osr dt scales the per-sample impulse by rj osr
    ms = np.arange(-n_post - 1, n_pre + 2)
    idx = pk - ms * osr
    hp = np.concatenate([h, [0.0]])

    def h_at(i: np.ndarray) -> np.ndarray:
        out = np.zeros(i.shape)
        inside = (i >= 0) & (i < hp.size)
        out[inside] = hp[i[inside]]
        return out

    # an edge drawn later moves the sample after its boundary, an earlier
    # one the sample before (jittered_zoh's area-conserving fill): the
    # slope is h on one side, the next sample's on the other
    g2 = 0.5 * (h_at(idx) ** 2 + h_at(idx + 1) ** 2) * (rj * osr) ** 2
    # the transmitter's per-UI response: b_n = sum_j t_j a_(n-j), and the
    # step at edge n + m is sum_c d_(m+c) a_(n-c) with d_j = t_j - t_(j-1)
    span = 16
    imp = np.zeros(2 * span + 1)
    imp[span] = 1.0
    t = tx_pipe.ffe(tx_pipe.pr_filter(imp))
    d = np.diff(np.concatenate([[0.0], t, [0.0]]))      # d[i] is d_j for j = i - span
    lv = np.asarray(tx_pipe.levels(np.arange(2 ** cfg.bits_per_symbol)), dtype=np.float64)
    mu, var_a = float(lv.mean()), float(lv.var())
    offs = (0,) + tuple(neighbours)                      # the symbols each bin fixes

    def d_at(j: np.ndarray) -> np.ndarray:
        i = j + span
        out = np.zeros(j.shape)
        inside = (i >= 0) & (i < d.size)
        out[inside] = d[i[inside]]
        return out

    coef = np.stack([d_at(ms + c) for c in offs], axis=1)   # (edges, fixed symbols)
    q = float(np.sum(d ** 2))
    floor = var_a * float(np.sum(g2 * (q - np.sum(coef ** 2, axis=1))))
    exact = np.isin(ms, _EXACT_EDGES)
    shape = (lv.size,) * len(offs)
    var_all, var_rest = np.zeros(shape), np.zeros(shape)
    step = np.zeros(shape + (len(_EXACT_EDGES),))
    for combo in np.ndindex(*shape):
        mean_step = coef @ (lv[list(combo)] - mu)        # each edge's step given the bin
        lin = g2 * mean_step ** 2
        var_all[combo] = floor + float(np.sum(lin))
        var_rest[combo] = floor + float(np.sum(lin[~exact]))
        step[combo] = [mean_step[ms == m][0] for m in _EXACT_EDGES]
    # the two own edges, exactly: the step response at the sample, displaced
    s = np.cumsum(h)
    sig = rj * osr                                        # displacement sigma [samples]
    eps = np.linspace(-n_sigma, n_sigma, n_eps) * sig
    w = np.exp(-0.5 * (eps / sig) ** 2)
    w /= w.sum()
    grid = np.arange(s.size)
    phi = np.stack([np.interp(pk - m * osr - eps, grid, s, left=0.0, right=s[-1])
                    - np.interp(pk - m * osr, grid, s, left=0.0, right=s[-1]) for m in _EXACT_EDGES])
    return TxEdgeJitter(sigma=np.sqrt(var_all), rest=np.sqrt(var_rest), step=step, phi=phi, weight=w)


def _convolve_at(pdf: np.ndarray, ker: np.ndarray, lo: int) -> np.ndarray:
    """``pdf`` convolved with a kernel whose first bin sits at offset ``lo``,
    on ``pdf``'s grid (direct: an FFT's round-off would floor the tails)."""
    full = np.convolve(pdf, ker)
    out = np.zeros(pdf.size)
    a, b = max(0, lo), min(pdf.size, lo + full.size)
    if b > a:
        out[a:b] = full[a - lo: b - lo]
    return out


def dac_noise_at_slicer(sigma_q: float, h: np.ndarray, dt: float, osr: int) -> float:
    """sigma at the slicer of a white per-UI error of ``sigma_q`` added at the
    DAC output: sqrt(sum_k p_k^2) sigma_q with p_k the symbol response from the
    DAC to the slicer at the main cursor's phase."""
    return float(sigma_q * np.sqrt(np.sum(_dac_cursors(h, dt, osr) ** 2)))


#: PR loop load (per-symbol decision SER x sum c_k^2) from which run_statistical
#: warns that the time engine's MM CDR may slip (measured 0.09-0.14, never below)
_PR_LOOP_LOAD_WARN = 0.08

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


def run_statistical(cfg: LinkConfig, channel: ChannelModel | None = None,
                    ffe_taps: np.ndarray | None = None, ffe_pre: int = 0,
                    v_bins: int = 4096, n_pre: int = 24, n_post: int = 64,
                    xtalk_pulses: list[Waveform] | None = None,
                    level_sigma: np.ndarray | None = None, xtalk=None,
                    phase_ui: float | None = None) -> StatResult:
    """``level_sigma``: extra noise sigma at the slicer per transmitted level
    (ascending), added in quadrature to the receiver noise; derived from
    ``channel.optical`` when None. Explicit for studies and for pinning that
    equal kernels reproduce the single-kernel engine.

    ``phase_ui``: sample that far from the receiver's lock point [UI] -- a
    lane whose clock comes from another lane's loop (``analysis.skew``).
    ``ber`` / ``ser`` are read there and the phase axis is centred on it,
    so the main cursor is the one sampled there even when an equaliser
    has made a neighbouring one larger. None: the lock point, on the axis
    centred on the pulse peak (as before the parameter existed).

    Crosstalk: ``cfg.crosstalk`` unless ``xtalk`` (``XtalkAggressor`` list)
    or ``xtalk_pulses`` (coupling pulses of synchronous aggressors) replaces
    it. A quasi-synchronous aggressor (``ppm`` != 0) is averaged over the
    phase it drifts through."""
    osr = cfg.osr
    if xtalk is not None and xtalk_pulses is not None:
        raise ValueError("pass xtalk (aggressors) or xtalk_pulses, not both")
    if xtalk_pulses is not None:
        if cfg.crosstalk.active:
            import warnings

            warnings.warn("xtalk_pulses replaces cfg.crosstalk; the config's aggressors are ignored",
                          stacklevel=2)
        x_in = [(xp, 0.0) for xp in xtalk_pulses]
    else:
        from ..channel.crosstalk import engine_aggressors

        x_in = [(a.pulse(osr), a.ppm) for a in engine_aggressors(cfg, xtalk)]
    sweep = min((abs(ppm) for _, ppm in x_in if ppm), default=0.0) * 1e-6 * cfg.sim.n_symbols
    if 0.0 < sweep < 1.0:
        import warnings

        warnings.warn(f"a quasi-synchronous aggressor drifts {sweep:.2f} UI over sim.n_symbols: "
                      "the time engine sees part of the phase range this engine averages over",
                      stacklevel=2)
    if channel is None:
        channel = ChannelModel.from_config(cfg)
    pattern_sigma = None

    # --- equalized pulse response: Tx FIR (x) channel (x) CTLE [(x) FFE] ---
    ch_rs = channel.response_set(cfg.dt)
    h = ch_rs.h.y
    if cfg.rx.ctle.enable:
        ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
        nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
        f = np.fft.rfftfreq(nfft, d=cfg.dt)
        h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: 2 * h.size]
    h = h * cfg.rx.vga_gain
    curve_mu = curve_var = None
    if getattr(channel, "optical", None) is not None:
        # same two-stage split as the time engine (engine/optical_stage.py)
        from .optical_stage import (
            curve_pattern_offsets, slicer_sigma_binned, slicer_sigma_per_level, split_impulses,
        )

        h1, h2 = split_impulses(cfg, channel)
        h = np.convolve(h1, h2)
        if level_sigma is None:
            level_sigma = slicer_sigma_per_level(cfg, channel, h1, h2, ffe_taps)
            pattern_sigma = slicer_sigma_binned(cfg, channel, h1, h2, ffe_taps,
                                                neighbours=_BINNED_NEIGHBOURS)
            if channel.optical.curve is not None:
                # the curve's bending of ISI, per phase and per the same bins
                curve_mu, curve_var = curve_pattern_offsets(cfg, channel, ffe_taps,
                                                            neighbours=_BINNED_NEIGHBOURS)
        elif channel.optical.curve is not None:
            import warnings

            warnings.warn(
                "optical topology with a large-signal E/O curve (li_compression > 0) and an "
                "explicit level_sigma: the curve's bending of ISI is carried by the "
                "pattern-binned kernels, which level_sigma bypasses -- only its "
                "steady-state levels are modelled", stacklevel=2)
    tx_pipe = TxPipeline.from_config(cfg)
    if cfg.tx.drv_nl != "none":
        import warnings

        warnings.warn(
            f"tx.drv_nl={cfg.tx.drv_nl!r}: the driver's compression is not in the "
            "statistical engine (it is not LTI) and the result is outside the 2x "
            "cross-check (invariant #3) -- use the time engine", stacklevel=2)
    # the DAC's error enters after the Tx FFE, before the driver pole
    drv_resp = tx_pipe.after_dac_response(osr)
    h_after_dac = h if drv_resp is None else np.convolve(h, drv_resp)
    h_chan = h
    tx_resp = tx_pipe.equivalent_symbol_response(osr)
    if tx_resp is not None:
        h = np.convolve(h, tx_resp)
    noise_sigma = cfg.rx.noise_rms
    if cfg.rx.arch == "adc_dsp":
        # the ADC's own noise, white per sample like the receiver's and at the
        # same node (the FFE input): quantisation, plus the excess that
        # brings it to the configured ENOB -- fs^2/12 * 2^(-2 ENOB) in all
        noise_sigma = float(np.hypot(noise_sigma, adc_noise_sigma(cfg)))
    h_pre_ffe = h
    if ffe_taps is not None and len(ffe_taps) > 1:
        h = np.convolve(h, upsampled_taps(ffe_taps, osr))
        noise_sigma = noise_sigma * float(np.linalg.norm(ffe_taps))
        h_after_dac = np.convolve(h_after_dac, upsampled_taps(ffe_taps, osr))
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
    # white per-UI error at the DAC output -- the DAC's equivalent noise and
    # the transmitter's own (tx.noise_rms) -- reaches the slicer through
    # everything after the DAC, shaped as the signal is
    rx_sigma = noise_sigma
    tx_cursors = None
    if tx_pipe.tx_sigma > 0.0:
        tx_cursors = _dac_cursors(h_after_dac, cfg.dt, osr)
        noise_sigma = float(np.hypot(noise_sigma, float(tx_pipe.tx_sigma * np.sqrt(np.sum(tx_cursors ** 2)))))
    if level_sigma is not None:
        level_sigma = np.asarray(level_sigma, dtype=np.float64)

    swing = cfg.tx.swing / 2.0  # symbol amplitude scale (levels_norm in [-1,1])
    levels_norm = _levels(cfg) / swing  # {-1,1} or {-1,-1/3,1/3,1}*rlm

    peak = int(np.argmax(np.abs(pulse.y)))
    pr_active = cfg.pr.active
    has_ffe = ffe_taps is not None and len(ffe_taps) > 1
    lock_pos = None
    if cfg.rx.arch in ("mixed_signal", "adc_dsp"):
        lock_pos = _lock_position(cfg, h_pre_ffe, h_chan, tx_pipe, has_ffe, ffe_pre)
    if phase_ui is not None and lock_pos is not None:
        # a sampling point set from outside: the grid is centred on it, as
        # for a PR target below, so the labels stay put at any offset
        lock_pos = lock_pos + float(phase_ui) * osr
        peak = int(np.round(lock_pos))
    elif pr_active and has_ffe and lock_pos is not None:
        # equalised to [1, a(, b(, c))] the pulse has comparable cursors and
        # its largest sample falls between them, up to half a UI after the
        # main one; the phase grid (+-half a UI) is centred where the
        # receiver samples instead. Centred on the largest sample it held the
        # lock point at its edge (a = 1) or not at all (a = 1.1: read the
        # next cursor, BER 2.7 against the time engine's 6e-4).
        peak = int(np.round(lock_pos))
    else:
        for _ in range(len(cfg.pr.target) - 1 if pr_active else 0):
            # equalised to [1, a(, b(, c))] the pulse has comparable cursors (equal
            # at a = 1) and nothing before them: the main one is the earliest
            if peak >= osr and abs(pulse.y[peak - osr]) >= 0.5 * abs(pulse.y[peak]):
                peak -= osr
    n_dfe = cfg.rx.dfe.n_taps
    n_t = len(cfg.pr.target)
    mlsd_mem = cfg.rx.mlsd.memory if cfg.rx.mlsd.kind != "none" else 0
    # a reduced-state trellis (mlsd.state_memory < its length) is scored by
    # the distance its paths accrue before they merge
    k_state = cfg.rx.mlsd.state_memory
    if k_state is not None and k_state >= n_t - 1 + mlsd_mem:
        k_state = None

    # phase axis: one UI centered on the pulse peak
    phi_offsets = np.arange(osr) - osr // 2

    # the transmitter's white edge jitter is noise that follows the
    # transitions, per phase (tx_jitter_sigma_binned), binned like the
    # optical noise; only the receiver's clock is left to smear the phase axis
    tx_jit_phi = None
    edge_jit = None
    jit_span = 0.0
    pattern_base = pattern_sigma
    if cfg.tx.clock.rj_ui > 0.0:
        at_peak = peak - int(np.argmax(np.abs(pulse.y)))
        tx_jit_phi = [tx_edge_jitter(cfg, tx_pipe, h_after_dac, osr, offset=at_peak + int(off))
                      for off in phi_offsets]
        # the voltage grid is sized on the UI around the pulse peak wherever
        # the phase axis is centred, so a reading set from outside
        # (phase_ui) lands on the same grid as the bathtub it is read from
        around_peak = tx_jit_phi if at_peak == 0 else [
            tx_edge_jitter(cfg, tx_pipe, h_after_dac, osr, offset=int(off)) for off in phi_offsets]
        jit_span = float(max(t.sigma.max() for t in around_peak))
        if pattern_base is None:
            lv0 = np.zeros(levels_norm.size) if level_sigma is None else level_sigma
            pattern_base = np.broadcast_to(lv0[:, None, None], tx_jit_phi[0].sigma.shape)

    # voltage grid sized to worst-case ISI + noise
    span = float(np.abs(pulse.y).max()) * swing * 2.5 + 8 * noise_sigma + 1e-6
    if level_sigma is not None:
        span += 8 * float(level_sigma.max())
    span += 8 * jit_span
    if curve_mu is not None:
        span += float(np.abs(curve_mu).max()) + 8 * float(np.sqrt(curve_var.max()))
    v_centers = np.linspace(-span, span, v_bins)
    dv = v_centers[1] - v_centers[0]
    noise_k = gaussian_kernel(noise_sigma, dv)

    eye_pdf = np.zeros((v_bins, osr))
    ser_phi = np.zeros(osr)
    ber_phi = np.zeros(osr)
    ser_sym_phi = np.zeros(osr)
    ep_factor_phi = np.ones(osr)
    ber_sym_phi = np.zeros(osr)
    head_energy_phi = np.zeros(osr)       # the controlled cursors' energy, sum c_k^2 (k >= 1)
    # precoded 1 + D at a = 1 slices composite levels in the kernel (no
    # propagation); the same condition as engine.timedomain's pr_mode 2
    pr_composite = (pr_active and cfg.precode and n_t == 2 and cfg.pr.alpha == 1.0
                    and cfg.pr.adapt == "none")

    n_levels = levels_norm.size
    bits_per_sym = cfg.bits_per_symbol
    # noise autocorrelation at the slicer, symbol spacing: white at the FFE
    # input (the receiver noise is band-limited to the baud rate), coloured
    # by the taps; only the partial-response union bound reads it
    noise_acf = np.ones(1)
    if pr_active and mlsd_mem > 0:
        if ffe_taps is not None and len(ffe_taps) > 1:
            w = np.asarray(ffe_taps, dtype=float)
            noise_acf = np.correlate(w, w, "full")[w.size - 1:]
        if tx_cursors is not None:
            # the transmitter's share is coloured by the whole chain after the
            # DAC, the FFE included: the cursors' own autocorrelation, weighted
            # by the two shares' variances at the slicer
            acf_rx = noise_acf / noise_acf[0] * rx_sigma ** 2
            acf_tx = np.correlate(tx_cursors, tx_cursors, "full")[tx_cursors.size - 1:] * tx_pipe.tx_sigma ** 2
            n = max(acf_rx.size, acf_tx.size)
            noise_acf = np.pad(acf_rx, (0, n - acf_rx.size)) + np.pad(acf_tx, (0, n - acf_tx.size))

    # the phase the receiver samples goes first: the PR union bound's overlap
    # weights are sampled there once (pr_error_events) and carried to the
    # other phases as ratios to the plain sum's -- per phase they would cost
    # the bathtub's flanks 16x as much for little
    i_lock = osr // 2
    if lock_pos is not None:
        i_lock = int(np.argmin(np.abs(phi_offsets - ((lock_pos - peak + osr / 2) % osr - osr / 2))))
    # crosstalk at the slicer: the coupling pulses through what follows the
    # node the time engine adds them at (the FFE on an ADC receiver), on the
    # victim pulse's time origin -- both start at their symbol's launch
    x_slicer, x_drift = [], []
    for xp, ppm in x_in:
        xy = np.asarray(xp.y, dtype=np.float64)
        if ffe_taps is not None and len(ffe_taps) > 1:
            xy = np.convolve(xy, upsampled_taps(ffe_taps, osr))
        if ppm == 0.0:
            x_slicer.append(xy)
            continue
        # quasi-synchronous: the victim's instants fall on every phase of the
        # aggressor's pulse in turn, equally often over a run -- its cursors
        # at each phase, and the ISI PDF averaged over them (the same for
        # every phase of the victim's, so taken once)
        sets = []
        for ph in range(osr):
            xc = baud_samples(xy, osr, ph) * swing      # cursors of a response, not symbols
            sets.append(xc[np.abs(xc) >= 1e-4 * np.abs(xc).max()] if xc.size else xc)
        x_drift.append((sets, np.mean([isi_pdf(xc, levels_norm, v_centers) for xc in sets], axis=0)))
    drift_projected = {}
    union_ratio = None
    # sampling-jitter smearing on the phase axis: what the CDR leaves of the
    # clocks plus the loop's own noise -- see _sampling_jitter_ui and the
    # assumption list above. Taken before the phases, which it decides.
    # an ADC detector on the equalised samples reads the slicer input, after
    # the decision feedback (a PR target's controlled cursors, then the DFE)
    n_feedback = ((n_t - 1 if pr_active else 0) + n_dfe
                  if cfg.rx.arch == "adc_dsp" and cfg.mm_pd_input == "ffe" else 0)
    pulse_pre = pulse_from_impulse(Waveform(h_pre_ffe, cfg.dt), osr)
    sigma_ui, loop_sol = _sampling_jitter_ui(
        cfg, pulse if cfg.mm_pd_input == "ffe" else pulse_pre, levels_norm, swing,
        cfg.rx.noise_rms, n_feedback=n_feedback,
        ffe_track=_ffe_lms_track(cfg, pulse_pre, n_dfe) if has_ffe else 0.0,
        loop=not (cfg.rx.arch == "mixed_signal" and has_ffe))
    # The smear is a sampling instant that moves (the loop's hunting and the
    # clock it leaves, the receiver's RJ), faster than the decision feedback
    # adapts: the receiver's DFE stays where it adapted, at the phase it is
    # read at. So the phases the smear reaches are taken a second time with
    # that phase's taps (``fixed``), the residual postcursors left as ISI; the
    # bathtub (ber_phi) stays the static one, its DFE adapted at every phase.
    # Re-adapting under the smear read the NRZ 16G preset 1.1-1.4x low against
    # the time engine (ROADMAP P2 #27). Where the smear is under two samples
    # the curve is also taken between them (``frac``, the pulse interpolated):
    # read linearly between samples a steep, convex curve came out 1.6-3.6x
    # high half a sample off the lock -- most of what example 31's profile
    # clocks read high with the taps held (ROADMAP P2 #28). The slicer's
    # thresholds are held with the taps (P2 #29).
    held = sigma_ui > 0
    # the phase whose main cursor sets the slicer's levels: the mixed-signal
    # time engine takes them once, at the pulse peak, where its bang-bang
    # loop does not sample (the PAM4 32G preset locks 2 samples after it);
    # an ADC receiver's LMS FFE pulls its output to them where it samples,
    # as does a lane read at an offset with its equalisers adapted there.
    # Held at the lock instead, the PAM4 32G preset read 0.70-0.95x the time
    # engine, at the peak 0.94-1.15x (ROADMAP P2 #30)
    i_level = (int(np.argmin(np.abs(phi_offsets)))
               if cfg.rx.arch == "mixed_signal" and phase_ui is None else i_lock)
    # something held under the smear: DFE taps, or PAM4's thresholds
    holds = n_dfe > 0 or levels_norm.size > 2

    def as_computed(i: int) -> bool:
        """The phase as first computed is already what the smear holds there."""
        return not holds or (i == i_lock and i_level == i_lock)

    smears = (tuple(loop_sol.sigma_over_run_ui)
              if loop_sol is not None and loop_sol.sigma_over_run_ui else (sigma_ui,))
    sub = 4 if sigma_ui * osr < 1.0 else 2 if sigma_ui * osr < 2.0 else 1
    fine_span = (int(min(osr // 2, np.ceil(8.0 * max(smears) * osr)))
                 if held and (holds or sub > 1) else 0)
    lo_fix, hi_fix = max(i_lock - fine_span, 0), min(i_lock + fine_span, osr - 1)
    tasks = [(i_lock, False, 0.0)] + [(i, False, 0.0) for i in range(osr) if i != i_lock]
    if fine_span:
        for i in range(lo_fix, hi_fix + 1):
            for j in range(sub if i < hi_fix else 1):
                frac = j / sub
                if frac > 0.5:          # per-phase quantities from the nearest sample
                    pi, frac = i + 1, frac - 1.0
                else:
                    pi = i
                if frac == 0.0 and as_computed(pi):
                    continue            # the phase as computed is that point
                tasks.append((pi, True, frac))
    fine_vals: dict[float, tuple[float, float]] = {}
    lock_dfe: dict[int, float] = {}
    main_phi: dict[int, float] = {}
    for pi, fixed, frac in tasks:
        off = phi_offsets[pi]
        if tx_jit_phi is not None:
            edge_jit = tx_jit_phi[pi]
            pattern_sigma = np.sqrt(pattern_base ** 2 + edge_jit.sigma ** 2)
        # cursors at this phase (volts, per unit symbol level)
        idx = peak + off + np.arange(-n_pre, n_post + 1) * osr
        if frac:
            c = _pulse_at(pulse.y, idx + frac)
        else:
            valid = (idx >= 0) & (idx < pulse.y.size)
            c = np.zeros(idx.size)
            c[valid] = pulse.y[idx[valid]]
        c = c * swing
        main = c[n_pre]
        if not fixed:
            main_phi[pi] = main
        # where the slicer's thresholds sit: at the main cursor of the phase
        # that set the levels when the smear holds the receiver (fixed), else
        # at this one. Re-placed at each phase they read the time engine
        # frozen off the lock 0.42-0.79x on PAM4 (ROADMAP P2 #29)
        thr_main = main_phi[i_level] if fixed else main
        isi = np.delete(c, n_pre)
        # a partial-response target's controlled cursor is signal, not ISI:
        # the sequence detector resolves it (or, without one, the per-symbol
        # decision subtracts it, pr_symbol_decisions)
        head = [1.0]
        if pr_active:
            isi = isi.copy()
            for j in range(n_t - 1):
                head.append(isi[n_pre + j] / main if main else 0.0)
                isi[n_pre + j] = 0.0
        # ideal DFE removes the n_dfe postcursors after the target
        # (with ``fixed``, the taps of the phase read: the residual stays)
        h_dfe1 = 0.0
        if n_dfe > 0:
            isi_list = list(isi)
            for d in range(n_dfe):
                pos = n_pre + n_t - 1 + d  # index into isi (post side starts at n_pre)
                if pos < len(isi_list):
                    if pi == i_lock and not fixed:
                        lock_dfe[pos] = float(isi_list[pos])
                    tap = lock_dfe[pos] if fixed else float(isi_list[pos])
                    if d == 0:
                        h_dfe1 = tap
                    isi_list[pos] = float(isi_list[pos]) - tap if fixed else 0.0
            isi = np.asarray(isi_list)

        # --- optional MLSD over the residual the DFE left behind ---
        # Textbook MLSE model (matched-filter bound): the detector *resolves*
        # the postcursors in its trellis, so they stop acting as interference
        # (dropped from the ISI PDF) and instead contribute energy — the
        # minimum error-event distance grows from |main| to sqrt(d_min^2),
        # which is applied here as an equivalent noise reduction. This is the
        # closed-form form of the planned MLSD gain table; the time engine is
        # the cross-check (extras['ser_slicer'] vs ser).
        g_mlsd = 1.0
        if mlsd_mem > 0:
            res = []
            isi_list = list(isi)
            for d in range(mlsd_mem):
                pos = n_pre + n_t - 1 + n_dfe + d   # postcursors after the DFE's
                if pos < len(isi_list):
                    res.append(isi_list[pos] / main if main else 0.0)
                    isi_list[pos] = 0.0       # resolved, not interference
            isi = np.asarray(isi_list)
            if res or pr_active:
                g = np.sqrt(mlse_min_distance_sq(
                    np.concatenate([head, np.asarray(res)]), state_memory=k_state))
                g_mlsd = max(g, 1e-12)
        # error events the per-level sum runs over: one, unless a sequence
        # detector works a partial-response target (pr_error_events)
        events = [(g_mlsd, 1.0)]
        ev_diffs = None
        if pr_active and mlsd_mem > 0:
            half_gap = 0.5 * abs(main) * float(np.min(np.diff(np.sort(levels_norm))))
            fams = (("alternating", "constant") if min(cfg.pr.target[2:], default=0.0) < 0.0
                    else ("alternating",))
            cur = np.concatenate([head, np.asarray(res)])
            events = pr_error_events(cur, noise_acf, n_levels, cfg.precode, families=fams,
                                     state_memory=k_state)
            # the overlap weights are sampled at the link's SNR: all of its
            # noise, the optical share (per level, here averaged) included.
            # With the receiver's electrical noise alone an optical link read
            # as noiseless, the events as disjoint and the union as the plain
            # sum -- 1.5-3.0x pessimistic behind transmit PR
            # (cairn/DSP发端与PR.md §20)
            sigma_union = noise_sigma
            if pattern_sigma is not None:
                var_opt = float(np.mean(pattern_sigma ** 2))
                if curve_var is not None:
                    var_opt += float(np.mean(curve_var[pi]))
                sigma_union = float(np.sqrt(noise_sigma ** 2 + var_opt))
            elif level_sigma is not None:
                sigma_union = float(np.sqrt(noise_sigma ** 2 + np.mean(level_sigma ** 2)))
            if sigma_union > 0:
                if union_ratio is None:
                    union = pr_error_events(cur, noise_acf, n_levels, cfg.precode,
                                            snr=half_gap / sigma_union, families=fams,
                                            state_memory=k_state)
                    union_ratio = [u[1] / e[1] if e[1] > 0 else 0.0
                                   for u, e in zip(union, events)]
                events = [(g_e, w_e * r) for (g_e, w_e), r in zip(events, union_ratio)]
            ev_diffs = pr_event_differences(cur, fams, state_memory=k_state)

        if pattern_sigma is not None:
            # the binned neighbours leave the random ISI and become a shift
            # (with a PR sequence detector a full-size one for every event:
            # their kernels are per level, so they are not projected on it)
            pos = [n_pre + j if j < 0 else n_pre + j - 1 for j in _BINNED_NEIGHBOURS]
            c_bin = np.array([isi[p] for p in pos])
            isi = isi.copy()
            isi[pos] = 0.0
        pdf = isi_pdf(isi, levels_norm, v_centers)
        xcs = []
        for xy in x_slicer:
            # every one of the aggressor's cursors the victim's sampling
            # instants at this phase fall on (its data is independent of the
            # victim's, so which one is "cursor 0" does not matter)
            xc = xy[np.arange((peak + off) % osr, xy.size, osr)] * swing
            if xc.size:
                xc = xc[np.abs(xc) >= 1e-4 * np.abs(xc).max()]
            xcs.append(xc)
            pdf = np.convolve(pdf, isi_pdf(xc, levels_norm, v_centers), mode="same")
        for _, x_pdf in x_drift:
            pdf = np.convolve(pdf, x_pdf, mode="same")
        pdf_isi = pdf

        def projected_isi(d_ev: np.ndarray) -> np.ndarray:
            """The ISI PDF error event d_ev sees. The detector takes it when
            d . (noise + ISI) > |d|^2 / 2, so in the slicer's units (threshold
            half a level gap) its ISI is d . ISI / |d|^2: the residual cursors
            correlated with d, sum_j a_j sum_k d_k c_(k-j) / |d|^2 -- shrunk by
            about the event's distance, as its noise is (by g_ev). Left at the
            single sample's ISI it read 2-12x pessimistic behind a 3-7 tap FFE,
            where the residual is not small (cairn/DSP发端与PR.md §19)."""
            dd = float(np.dot(d_ev, d_ev))
            c_full = np.insert(isi, n_pre, 0.0)           # symbol order, main slot empty
            pdf = isi_pdf(np.correlate(d_ev, c_full, "full") / dd, levels_norm, v_centers)
            for xc in xcs:
                pdf = np.convolve(pdf, isi_pdf(np.correlate(d_ev, xc, "full") / dd,
                                               levels_norm, v_centers), mode="same")
            for k, (sets, _) in enumerate(x_drift):
                key = (k, d_ev.tobytes())
                if key not in drift_projected:   # independent of the victim's phase
                    drift_projected[key] = np.mean(
                        [isi_pdf(np.correlate(d_ev, xc, "full") / dd, levels_norm, v_centers)
                         for xc in sets], axis=0)
                pdf = np.convolve(pdf, drift_projected[key], mode="same")
            return pdf

        def noisy(g_ev: float, pdf_base: np.ndarray | None = None) -> list:
            """ISI + noise PDF per transmitted level, the noise divided by g_ev."""
            pdf = pdf_isi if pdf_base is None else pdf_base
            # noise kernel: one for an electrical link; one per *transmitted*
            # level when the noise follows the level (an inverting channel flips
            # the slicer order, not which symbol carried the most light)
            if pattern_sigma is not None:
                pdf_lv = []
                n_pat = n_levels ** len(_BINNED_NEIGHBOURS)
                for k in range(n_levels):
                    acc = np.zeros(v_bins)
                    for combo in np.ndindex(*pattern_sigma.shape[1:]):
                        shift = float(np.dot(c_bin, levels_norm[list(combo)]))
                        if edge_jit is None:
                            var = noise_sigma ** 2 + pattern_sigma[(k,) + combo] ** 2
                        else:
                            # the own two edges come in as their exact kernel below
                            var = (noise_sigma ** 2 + pattern_base[(k,) + combo] ** 2
                                   + edge_jit.rest[(k,) + combo] ** 2)
                        if curve_mu is not None:
                            shift += float(curve_mu[(pi, k) + combo])
                            var += float(curve_var[(pi, k) + combo])
                        shifted = np.zeros(v_bins)
                        _shift_add(shifted, pdf, shift / dv, 1.0)
                        nk = gaussian_kernel(np.sqrt(var) / g_ev, dv)
                        blurred = np.convolve(shifted, nk, mode="same") if nk.size > 1 else shifted
                        if edge_jit is not None:
                            ek, e_lo = edge_jit.kernel((k,) + combo, dv, g_ev)
                            blurred = _convolve_at(blurred, ek, e_lo)
                        acc += blurred / n_pat
                    pdf_lv.append(acc / max(acc.sum(), 1e-300))
            elif level_sigma is None:
                nk = noise_k if g_ev == 1.0 else gaussian_kernel(noise_sigma / g_ev, dv)
                if nk.size > 1:
                    pdf = np.convolve(pdf, nk, mode="same")
                pdf = pdf / max(pdf.sum(), 1e-300)
                pdf_lv = [pdf] * n_levels
            else:
                pdf_lv = []
                for sk in level_sigma:
                    nk = gaussian_kernel(np.sqrt(noise_sigma ** 2 + sk ** 2) / g_ev, dv)
                    pk = np.convolve(pdf, nk, mode="same") if nk.size > 1 else pdf
                    pdf_lv.append(pk / max(pk.sum(), 1e-300))
            return pdf_lv

        ser = 0.0
        nbe = 0.0
        pdf_eye = None
        for i_ev, (g_ev, w_ev) in enumerate(events):
            pdf_lv = noisy(g_ev, None if ev_diffs is None else projected_isi(ev_diffs[i_ev]))

            # tail CDFs per level
            cdf_up = [np.cumsum(p[::-1])[::-1] for p in pdf_lv]   # P(x >= v)
            cdf_dn = [np.cumsum(p) for p in pdf_lv]               # P(x <= v)

            def tail_ge(v: float, j: int) -> float:
                i = int(np.searchsorted(v_centers, v))
                return float(cdf_up[j][i]) if i < v_bins else 0.0

            def tail_le(v: float, j: int) -> float:
                i = int(np.searchsorted(v_centers, v)) - 1
                return float(cdf_dn[j][i]) if i >= 0 else 0.0

            # SER/BER over levels: distance to adjacent thresholds = |main|*gap/2
            lv = levels_norm * main
            order = np.argsort(lv)
            lv_sorted = lv[order]
            lt = (levels_norm * thr_main)[order]
            for j in range(n_levels):
                p_err_up = p_err_dn = 0.0
                if j < n_levels - 1:
                    thr = (lt[j] + lt[j + 1]) / 2.0
                    p_err_up = tail_ge(thr - lv_sorted[j], int(order[j]))
                if j > 0:
                    thr = (lt[j - 1] + lt[j]) / 2.0
                    p_err_dn = tail_le(thr - lv_sorted[j], int(order[j]))
                ser += w_ev * (p_err_up + p_err_dn) / n_levels
                nbe += w_ev * (p_err_up + p_err_dn) / n_levels  # Gray: adjacent = 1 bit
            if pdf_eye is None:
                pdf_eye = pdf_lv if ev_diffs is None else noisy(g_ev)
        pdf_lv = pdf_eye
        if n_dfe > 0 and not pr_active and mlsd_mem == 0 and h_dfe1 != 0.0:
            # DFE error propagation (the "ideal DFE" above feeds back correct
            # decisions). A wrong decision -- one level off, nearly always --
            # leaves h1 x the level step on the next sample; the chance that
            # one goes wrong too is the SER with the slicer input shifted
            # that way, averaged over the next symbol's level and the sign.
            # Errors then follow the two-state (Gilbert) chain: a symbol
            # after a correct one fails with s, the ideal-DFE SER, after a
            # wrong one with p, so it is wrong s / (1 - p + s) of the time --
            # s / (1 - p) while errors are rare, never past 1 on a closed
            # eye. Each one bit (Gray). First DFE tap only. With the ADC's
            # DFE engaged (ROADMAP P1 #21; tap 0.57) the time engine read
            # 1.8-2.9x this engine's ideal-DFE BER.
            step = float(np.min(np.diff(np.sort(levels_norm)))) * h_dfe1
            p_ep = 0.0
            for j in range(n_levels):
                for sgn in (1.0, -1.0):
                    p_j = 0.0
                    if j < n_levels - 1:
                        thr = (lt[j] + lt[j + 1]) / 2.0
                        p_j += tail_ge(thr - lv_sorted[j] - sgn * step, int(order[j]))
                    if j > 0:
                        thr = (lt[j - 1] + lt[j]) / 2.0
                        p_j += tail_le(thr - lv_sorted[j] - sgn * step, int(order[j]))
                    p_ep += p_j / (2 * n_levels)
            p_ep = min(p_ep, 0.99)
            ep = 1.0 / (1.0 - p_ep + min(ser, 1.0))
            if not fixed:
                ep_factor_phi[pi] = ep
            ser *= ep
            nbe *= ep
        if fixed:
            ser_v, ber_v = ser, nbe / bits_per_sym
            if pr_active and mlsd_mem == 0:
                s_sym, b_sym = pr_symbol_decisions(
                    pdf_lv, v_centers, levels_norm * main, head,
                    composite=pr_composite, precoded=cfg.precode)
                ser_v, ber_v = s_sym, b_sym / bits_per_sym
            fine_vals[float(off + frac)] = (ser_v, ber_v)
            continue
        ser_phi[pi] = ser
        ber_phi[pi] = nbe / bits_per_sym
        if pr_active:
            # the per-symbol decisions the LMS and the CDR read; without a
            # sequence detector they are the detector, error propagation and all
            s_sym, b_sym = pr_symbol_decisions(
                pdf_lv if mlsd_mem == 0 else noisy(1.0), v_centers, levels_norm * main, head,
                composite=pr_composite, precoded=cfg.precode)
            ser_sym_phi[pi], ber_sym_phi[pi] = s_sym, b_sym / bits_per_sym
            head_energy_phi[pi] = float(np.sum(np.square(head[1:])))
            if mlsd_mem == 0:
                ser_phi[pi], ber_phi[pi] = ser_sym_phi[pi], ber_sym_phi[pi]

        # marginal slicer PDF (mixture over transmitted levels) for the eye
        eye_col = np.zeros(v_bins)
        for j in range(n_levels):
            _shift_add(eye_col, pdf_lv[j], lv[j] / dv, 1.0 / n_levels)
        eye_pdf[:, pi] = eye_col

    # the curves the smear is read from: the phases as computed, before it
    ber_raw, ser_raw = ber_phi.copy(), ser_phi.copy()
    if fine_span:
        # the lock phase as computed, and (nothing to hold) the other samples too
        for i in range(lo_fix, hi_fix + 1):
            if as_computed(i):
                fine_vals[float(phi_offsets[i])] = (ser_raw[i], ber_raw[i])
        fine_x = np.array(sorted(fine_vals))
        ser_fine = np.array([fine_vals[x][0] for x in fine_x])
        ber_fine = np.array([fine_vals[x][1] for x in fine_x])
    if sigma_ui > 0:
        sig_phi = sigma_ui * osr
        k = gaussian_kernel(sig_phi, 1.0)
        pad = k.size // 2
        bp = np.pad(ber_phi, pad, mode="edge")
        ber_phi = np.convolve(bp, k, mode="valid")
        sp = np.pad(ser_phi, pad, mode="edge")
        ser_phi = np.convolve(sp, k, mode="valid")
        if pr_active:
            ser_sym_phi = np.convolve(np.pad(ser_sym_phi, pad, mode="edge"), k, mode="valid")

    best = int(np.argmin(ber_phi))
    ber, ser = float(ber_phi[best]), float(ser_phi[best])
    lock_ui = None
    if cfg.rx.arch in ("mixed_signal", "adc_dsp"):
        # Report what the receiver samples, not the best it could; the best
        # stays in extras['ber_min_phase']. Each point is found on the pre-FFE
        # pulse, then moved to the equalised pulse's phase axis (the FFE's
        # main tap delays it by ffe_pre UI).
        # - mixed-signal: the bang-bang loop settles where its edge samples
        #   balance, which is not where BER is lowest (a DFE moves the
        #   bathtub's floor 0.1-0.2 UI away). With an FFE this is the static
        #   engine, which has no loop and samples at the pre-FFE pulse peak.
        # - ADC, Mueller-Muller on the raw samples (pd_input adc): it locks
        #   where the pre-FFE pulse has h(-1) = h(+1).
        # - ADC, MM on the equalised samples (ffe): the FFE holds h(+-1) near
        #   0 at any phase, and the loop together with the LMS FFE settles
        #   on the pre-FFE pulse peak it starts from -- started 2 samples off
        #   on five PAM4 links it swings back towards it, a slowly decaying
        #   oscillation of the loop and the LMS pulling on each other
        #   (2026-10-09). Where the
        #   bathtub is narrow that is not its floor: example 32's CPO with a
        #   host FFE through a c = 0.5 laser read 0.35x the time engine at the
        #   best phase and 0.91x here.
        lag = lock_pos - peak
        lock = (lag + osr / 2) % osr - osr / 2
        ber = float(np.interp(lock, phi_offsets, ber_phi))
        ser = float(np.interp(lock, phi_offsets, ser_phi))
        if held:
            # the smear around the phase read, with its taps held there when
            # there is a DFE (that curve exists only where the smear reaches);
            # an underdamped loop's error grows over the run as it rings up,
            # so the read is the mean over the run's points (one if it does not)
            x, cb, cs = ((fine_x, ber_fine, ser_fine) if fine_span
                         else (phi_offsets, ber_raw, ser_raw))
            ber = float(np.mean([_smeared_at(cb, x, lock, sg * osr) for sg in smears]))
            ser = float(np.mean([_smeared_at(cs, x, lock, sg * osr) for sg in smears]))
        best = int(np.argmin(np.abs(phi_offsets - lock)))
        lock_ui = lock / osr
    loop_load = None
    if pr_active and cfg.rx.arch == "adc_dsp" and cfg.mm_pd_input == "ffe":
        # The Mueller-Muller CDR reads the sample less the controlled cursors
        # times the per-symbol decisions, so each wrong one leaves c_k times
        # its error in what the detector sees: power ~ SER * sum c_k^2 (in
        # level steps). Past a point the loop walks off, cycle-slipping in
        # the time engine, which this engine does not model. On six PR
        # targets (two to four cursors, precoded 1 + D, c < 0; 0.26 m,
        # 2.2-5.4 mV) the time engine slipped or read more than 2x this
        # engine's BER from 0.09-0.14 of this load, never below 0.09
        # (cairn/DSP发端与PR.md §15)
        loop_load = float(ser_sym_phi[best] * head_energy_phi[best])
        if loop_load >= _PR_LOOP_LOAD_WARN:
            import warnings

            warnings.warn(
                f"partial-response loop load {loop_load:.3f} (per-symbol decision SER "
                f"{ser_sym_phi[best]:.2g} x controlled-cursor energy {head_energy_phi[best]:.2g}) "
                f">= {_PR_LOOP_LOAD_WARN}: the Mueller-Muller CDR reads those decisions, and "
                "from 0.09-0.14 it cycle-slipped in the time engine on the PR targets measured, "
                "which this engine does not model -- its BER is optimistic here; use the time "
                "engine", stacklevel=2)
    return StatResult(
        ber_phi=ber_phi, ser_phi=ser_phi, best_phi=best,
        ber=ber, ser=ser,
        eye_pdf=eye_pdf, v_centers=v_centers,
        phi_ui=phi_offsets / osr,
        extras={"peak": peak, "noise_sigma": noise_sigma,
                "level_sigma": level_sigma,
                "jitter_sigma_ui": sigma_ui,
                "clock_loop": loop_sol,
                "lock_phase_ui": lock_ui,
                "ber_min_phase": float(ber_phi.min()),
                "dfe_ep_factor": float(ep_factor_phi[best]),
                # SER of the per-symbol PR decisions (the time engine's
                # extras['ser_slicer']); None without a PR target
                "ser_slicer": float(ser_sym_phi[best]) if pr_active else None,
                # that SER times the controlled cursors' energy: the load the
                # PR decisions put on the MM CDR (warned from 0.08; None
                # without a PR target or with the detector on raw samples)
                "pr_loop_load": loop_load})


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


def _sampling_jitter_ui(cfg: LinkConfig, pulse_pd: Waveform, levels_norm: np.ndarray,
                        swing: float, noise_sigma: float, n_feedback: int = 0,
                        loop: bool = True, ffe_track: float = 0.0
                        ) -> tuple[float, LoopSolution | None]:
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
            period_levels=None if period is None else levels_v[period])
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
        pairs = _bb_pairs(pulse_pd, cfg.osr, noise_sigma, swing, levels_norm,
                          extra_edge_ui=float(cfg.tx.clock.rj_ui))
        sol = bb_loop_fixed_point(
            lp, untracked,
            transitions=_bb_transitions(pulse_pd, cfg.osr, noise_sigma, swing, levels_norm,
                                        pairs=pairs),
            mean_output=lambda sigma_e: _bb_mean_output(pairs, levels_norm.size, sigma_e),
            period=_pattern_period(cfg))
    # the same loop once it has forgotten its start: what a longer run, and
    # the link in service, settles at
    steady = float(np.hypot(untracked(lambda f, k=sol.k_pd: error_response(f, lp, k)),
                            sol.sigma_self_ui))
    sol = replace(sol, sigma_steady_ui=steady, sigma_over_run_ui=_over_run(
        profs, ErrorResponse(lp, sol.k_pd), sol, first, last, n_sym, ui))
    _warn_if_run_short_of_steady(sol, cfg)
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

        sym = precode_1plusd(sym, 2 ** cfg.bits_per_symbol)
    return sym[:per] if np.array_equal(sym[:per], sym[per:]) else sym


def _bb_pairs(pulse_pd: Waveform, osr: int, noise_sigma: float, swing: float,
              levels_norm: np.ndarray, extra_edge_ui: float = 0.0, n_pre: int = 24,
              n_post: int = 64) -> dict[tuple[int, int], tuple[float, float]]:
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
    isi_var = swing ** 2 * float(np.mean(levels_norm ** 2)) * float(np.sum(np.square(others)))
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
                    n_post: int = 64, pairs: dict | None = None
                    ) -> tuple[tuple[float, float, float], ...]:
    """The Alexander detector's transitions by class, for ``bb_loop_fixed_point``:
    ``(probability per symbol, zero crossing relative to the lock point [UI],
    edge noise [UI])`` -- ``_bb_pairs`` grouped where they cross and how
    noisily."""
    if pairs is None:
        pairs = _bb_pairs(pulse_pd, osr, noise_sigma, swing, levels_norm, extra_edge_ui,
                          n_pre, n_post)
    n = levels_norm.size
    classes: dict[tuple[float, float], float] = {}
    for key in pairs.values():
        classes[key] = classes.get(key, 0.0) + 1.0 / n ** 2
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
