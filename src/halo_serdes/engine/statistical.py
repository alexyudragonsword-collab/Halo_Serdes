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
  its DFE engaged read 0.35-0.55x the time engine; with it 0.81-1.25x.
  Behind an FFE the noise is coloured and a first error is followed more
  often than an error the DFE made (p1 > p2, from the FFE taps' lag
  covariances), and an error deep in a chain more often than the first one
  the DFE made (p3 > p2: the noises before it are both small); the chain
  then has four states (ROADMAP P2 #37);
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
  bang-bang detector's edges. A white clock's SJ tone (either side) goes
  through the receiver's loop -- the ADC's Mueller-Muller loop too, which
  white clocks otherwise leave out (its own noise 0.0001-0.006 UI on the
  presets) -- and what is left of it, ``A |E(f)|``, is a swing of the
  sampling instant read as its arcsine on top of the smear; a bang-bang
  loop's gain and noise take the swing (it flattens the detector). DCD moves
  the transmitter's own two edges of the sampled symbol by half of it with
  their sign, exactly, and the rest linearised; a mixed-signal receiver's
  adapting levels sit at the offset it leaves and its DFE's first tap takes
  the part that follows the previous symbol (``stat_noise.dcd_absorbed``).
  Against the time engine on independent symbols (2026-11-01): the 224G ADC
  preset 0.85-1.01x under SJ, 0.79 / 0.87x under DCD (0.23-0.78x left out);
  PAM4 32G 0.61 / 0.85x under SJ, 0.30 / 0.56x under 0.12 / 0.08 UI of DCD;
  NRZ 16G 0.26-0.65x under 0.05-0.1 UI of SJ above its loop (0.04-0.27x left
  out) -- its hunting under the tone grows 11 % in the kernel, 2-4 % here,
  and the pattern-phase factor is left out where the tone swings the loop
  more than its noise -- and 0.86-0.90x under DCD (ROADMAP P2 #45). A tone
  the loop must follow faster than ~0.4 of its slew, or a phase error past
  0.1 UI RMS, slips the kernel's cycles: warned, not modelled. A bang-bang loop's phase is not Gaussian about the
  lock: its detector saturates, so beyond ~2 sigma the density's tails are
  exponential and heavier on the side that saturates first (the late side
  on the NRZ 16G preset: 3-7x a Gaussian's mass 0.23 UI out). Since
  2026-10-21 the mixed-signal read is smeared with the stationary density
  of the detector's own mean curve (``_bb_mean_curve``,
  ``cdr.linear.bb_stationary_density``), its RMS the linear model's; the
  NRZ 16G preset read 0.84 / 0.64 / 0.25x the time engine at 18 / 15 / 12
  mV with a Gaussian, 0.91 / 0.78 / 0.42x with it (ROADMAP P2 #32). What
  was left there is the free-running loop's errors sitting on the data that
  drove it late: a long run of the other level before a short one votes
  the loop late and, past the DFE's taps, pulls the short run's samples
  toward itself. Since 2026-10-24 the NRZ read with a DFE is multiplied by
  the factor of that correlation over 2^12 histories
  (``_pattern_phase_factor``, ROADMAP P3 #13); since 2026-10-26 each
  history's phase density is the loop's own vote chain at its fixed point,
  not the stationary density shifted (P3 #14), and the chain carries the
  loop's integral path, so its own marginal is the phase on random data
  (P3 #15), pushed with second-order fluxes (P3 #16): 1.35 / 1.18 / 1.05x
  at 12 / 15 / 18 mV, 0.8-1.35x at 10-6 mV, the read 0.88-1.09x the
  kernel's on independent symbols from 18 to 6 mV.
  This engine models random data: on PRBS31 the free-running kernel errs
  1.03-10x more over the same range (PRBS23 more still, PRBS13 less), the
  pattern's recurrence landing on the loop's memory;
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
  slope of the pre-DFE pulse. Gain and detector noise are both read off
  the edge samples over the configured pattern (``BbEdgeStats``: one
  period of a repeating pattern, else seeded random symbols), so neither is
  taken Gaussian: the edge's ISI, a few large cursors, has 0.86x (NRZ 16G)
  / 0.85-0.88x (PAM4 32G) a Gaussian's density at zero, and taken Gaussian
  the gain read 1.22 / 1.07x the kernel frozen at its lock -- compensated
  by a white detector noise counting the data's whole power where a slow
  loop sees its density at zero (0.6-0.8x). Now the frozen gain is 1.04 /
  0.99x the kernel's and the hunting 1.03 / 1.06 / 0.96x the time engine's
  (NRZ 16G / NRZ 28G / PAM4 32G; 1.04 / 1.03 / 1.01x before); the data
  noise is taken at each phase the loop's own jitter puts the detector
  (taken after averaging over it, the hunting read 0.78-0.91x). The lock
  point is the kernel's (+0.34 / +1.99 samples after the peak, the
  kernel's zero +0.35 / +1.99; ROADMAP P2 #38). A receiver clock (``rx.clock``)
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

Layout: this module holds ``run_statistical`` and its result; the parts it
assembles live beside it -- ``stat_pdf`` (the ISI PDFs), ``stat_noise`` (the
noise sources besides the receiver's AWGN), ``stat_pr`` (the
partial-response detector), ``stat_loop`` (where the clock-recovery loop
samples and how it wanders) and ``stat_pattern_phase`` (the pattern-phase
factor). Their names are importable from here as before the split
(2026-10-30).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np

from ..afe import Ctle
from ..cdr.linear import bb_stationary_density
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.mapping import (
    bit_distance_matrix, level_probabilities, neighbour_conditional, pair_bit_distance,
    pair_event_bits, pair_word_bits,
)
from ..core.sampler import baud_samples, upsampled_taps
from ..core.waveform import Waveform
from ..dsp.mlsd import mlse_min_distance_sq
from ..tx.pipeline import TxPipeline
from .scoring import cdr_gains
from .static_link import _levels

# The engine's parts (split out 2026-10-30), under the names callers and
# tests have always imported from here. Rebinding one here does not reach
# the part that reads it: a probe sets stat_pattern_phase._PATTERN_PHASE_TRACE.
from .stat_pdf import (  # noqa: F401
    _BINNED_NEIGHBOURS, _convolve_centred, isi_pdf, _pair_isi_pdfs, _shift_add,
)
from .stat_pr import (  # noqa: F401
    pr_error_events, pr_event_differences, _pr_patterns, pr_symbol_decisions,
)
from .stat_noise import (  # noqa: F401
    adc_noise_sigma, _convolve_at, _dac_cursors, dac_noise_at_slicer, _EXACT_EDGES,
    dcd_absorbed, gaussian_kernel, tx_edge_jitter, tx_noise_covariance, TxEdgeJitter,
)
from .stat_loop import (  # noqa: F401
    _BB_DAMPING_WARN, _bb_edge_stats, _bb_mean_curve, _bb_mean_output, _bb_pairs, _bb_transitions,
    _ffe_lms_track, _lock_position, _MM_HOLD_SIGMAS, _over_run, _pattern_period, _pulse_at,
    _RUN_POINTS, _RUN_STEADY_WARN, _sampling_jitter_ui, _smeared_at, _smeared_with,
    bb_edge_jitter_ui, sj_phase_offsets, sj_residual_ui, sj_tones, TONE_SLIP_FRACTION,
    tone_slew_fraction, tone_slew_limit_ui,
    _warn_if_mm_cannot_hold, _warn_if_run_short_of_steady, _warn_if_slew_limited, _warn_if_tone_slips,
    _warn_if_underdamped,
)
from .stat_pattern_phase import (  # noqa: F401
    _PATTERN_HISTORY, _PATTERN_PHASE_CACHE, _PATTERN_PHASE_CACHE_SIZE, _pattern_phase_factor,
    _PATTERN_PHASE_TRACE, PatternPhase,
)


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



#: PR loop load (per-symbol decision SER x sum c_k^2) from which run_statistical
#: warns that the time engine's MM CDR may slip (measured 0.09-0.14, never below)
_PR_LOOP_LOAD_WARN = 0.08


def _take(ctx: SimpleNamespace, names: tuple[str, ...]) -> list:
    """The setup's locals names (_stat_setup), for a part to unpack.
    One the setup never bound on this run's path reads None: the code that
    would read it is not on that path either (it would have raised before
    the split)."""
    d = vars(ctx)
    return [d.get(n) for n in names]


def run_statistical(cfg: LinkConfig, channel: ChannelModel | None = None,
                    ffe_taps: np.ndarray | None = None, ffe_pre: int = 0,
                    v_bins: int = 4096, n_pre: int = 24, n_post: int = 64,
                    xtalk_pulses: list[Waveform] | None = None,
                    level_sigma: np.ndarray | None = None, xtalk=None,
                    phase_ui: float | None = None, own_clock: bool = True,
                    voters: int = 1) -> StatResult:
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

    ``own_clock``: whether the lane's own data votes the loop that samples
    it. False for a lane on another lane's clock (``"master"``) or on one
    loop listening to many (``"average"``, ``engine.shared_clock``): the
    clock's phase is then independent of this lane's data, so NRZ with a
    DFE takes only the part of the pattern-phase factor that is the loop's
    own phase density (``PatternPhase.factor_marginal``), not the
    correlation; the density is the lane's own loop's, i.e. the lanes are
    taken alike.

    Crosstalk: ``cfg.crosstalk`` unless ``xtalk`` (``XtalkAggressor`` list)
    or ``xtalk_pulses`` (coupling pulses of synchronous aggressors) replaces
    it. A quasi-synchronous aggressor (``ppm`` != 0) is averaged over the
    phase it drifts through."""
    # three parts, each read straight through (2026-10-30 split; bit-identical):
    # the setup, one phase per task, the read at the lock point
    ctx = _stat_setup(cfg, channel, ffe_taps, ffe_pre, v_bins, n_pre, n_post, xtalk_pulses,
                      level_sigma, xtalk, phase_ui, own_clock, voters)
    for pi, fixed, frac in ctx.tasks:
        _stat_phase(ctx, pi, fixed, frac)
    return _stat_read(ctx)


def _stat_setup(cfg: LinkConfig, channel: ChannelModel | None = None,
                    ffe_taps: np.ndarray | None = None, ffe_pre: int = 0,
                    v_bins: int = 4096, n_pre: int = 24, n_post: int = 64,
                    xtalk_pulses: list[Waveform] | None = None,
                    level_sigma: np.ndarray | None = None, xtalk=None,
                    phase_ui: float | None = None, own_clock: bool = True,
                    voters: int = 1) -> StatResult:
    """``run_statistical`` up to its phase loop: the equalised pulse, the
    noise and its kernels, the levels, the lock point and the sampling
    jitter, the voltage grid, the phases to compute (``tasks``) and the
    arrays they fill -- every local, as a namespace the other two parts
    read (``_stat_phase``, ``_stat_read``)."""
    osr = cfg.osr
    if xtalk is not None and xtalk_pulses is not None:
        raise ValueError("pass xtalk (aggressors) or xtalk_pulses, not both")
    if xtalk_pulses is not None:
        if cfg.crosstalk.active:
            import warnings

            warnings.warn("xtalk_pulses replaces cfg.crosstalk; the config's aggressors are ignored",
                          stacklevel=3)
        x_in = [(xp, 0.0) for xp in xtalk_pulses]
    else:
        from ..channel.crosstalk import engine_aggressors

        x_in = [(a.pulse(osr), a.ppm) for a in engine_aggressors(cfg, xtalk)]
    sweep = min((abs(ppm) for _, ppm in x_in if ppm), default=0.0) * 1e-6 * cfg.sim.n_symbols
    if 0.0 < sweep < 1.0:
        import warnings

        warnings.warn(f"a quasi-synchronous aggressor drifts {sweep:.2f} UI over sim.n_symbols: "
                      "the time engine sees part of the phase range this engine averages over",
                      stacklevel=3)
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
                "steady-state levels are modelled", stacklevel=3)
    tx_pipe = TxPipeline.from_config(cfg)
    if cfg.tx.drv_nl != "none":
        import warnings

        warnings.warn(
            f"tx.drv_nl={cfg.tx.drv_nl!r}: the driver's compression is not in the "
            "statistical engine (it is not LTI) and the result is outside the 2x "
            "cross-check (invariant #3) -- use the time engine", stacklevel=3)
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
    # how often each level is sent and the bits a wrong neighbour costs: 1/N
    # and one (Gray) but for PAM6's corner32 map (core/mapping.py). The
    # aggressors send the victim's format, so their cursors' ISI PDFs take
    # the same occupancy (uniform until 2026-10-17; the pair correlation
    # between an aggressor's own neighbouring cursors is left out)
    level_probs = level_probabilities(cfg.modulation)
    bit_dist = bit_distance_matrix(cfg.modulation)
    pair_bits = pair_bit_distance(cfg.modulation)
    pair_words = pair_word_bits(cfg.modulation)
    # PAM6's pairs: the partner's level depends on this one's (an outer
    # symbol is never paired with an outer one), so the +-1 cursors' ISI is
    # per level. Taken as independent, the time engine read 1.6-2.8x below
    # this engine on the mixed-signal receiver, where those cursors carry
    # the ISI (ROADMAP P2 #35); the ADC receiver's FFE flattens them
    pair_cond = neighbour_conditional(cfg.modulation)

    peak = int(np.argmax(np.abs(pulse.y)))
    pr_active = cfg.pr.active
    has_ffe = ffe_taps is not None and len(ffe_taps) > 1
    lock_pos = None
    if cfg.rx.arch in ("mixed_signal", "adc_dsp"):
        lock_pos = _lock_position(cfg, h_pre_ffe, h_chan, tx_pipe, has_ffe, ffe_pre)
    lock_base = lock_pos
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
    if cfg.tx.clock.rj_ui > 0.0 or cfg.tx.clock.dcd_ui > 0.0:
        at_peak = peak - int(np.argmax(np.abs(pulse.y)))
        tx_jit_phi = [tx_edge_jitter(cfg, tx_pipe, h_after_dac, osr, offset=at_peak + int(off))
                      for off in phi_offsets]
        if cfg.tx.clock.dcd_ui > 0.0 and cfg.rx.arch == "mixed_signal":
            # DCD's offset is deterministic, and what the receiver adapts takes
            # part of it up: its levels each level's mean, its DFE's first tap
            # the part that follows the previous symbol (stat_noise.dcd_absorbed).
            # Left in, the NRZ 16G preset read 7x the time engine at 0.1 UI
            lv_tx = np.asarray(tx_pipe.levels(np.arange(cfg.n_levels)), dtype=np.float64)
            prev_axis = (1 + _BINNED_NEIGHBOURS.index(1)
                         if n_dfe > 0 and cfg.rx.dfe.adapt != "none" and 1 in _BINNED_NEIGHBOURS
                         else None)
            tx_jit_phi = [dcd_absorbed(t, lv_tx, level_probabilities(cfg.modulation),
                                       cfg.rx.dfe.level_adapt == "sign_sign", prev_axis)
                          for t in tx_jit_phi]
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
        x_drift.append((sets, np.mean([isi_pdf(xc, levels_norm, v_centers, level_probs)
                                       for xc in sets], axis=0)))
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
    # the detector's cursors where the loop samples (the base lock, not a
    # lane's offset read): with the feedback taken there its mean is zero
    lock_shift = (int(np.round(lock_base - int(np.argmax(np.abs(pulse.y)))))
                  if n_feedback > 0 and lock_base is not None else 0)
    sigma_ui, loop_sol = _sampling_jitter_ui(
        cfg, pulse if cfg.mm_pd_input == "ffe" else pulse_pre, levels_norm, swing,
        cfg.rx.noise_rms, n_feedback=n_feedback,
        ffe_track=_ffe_lms_track(cfg, pulse_pre, n_dfe) if has_ffe else 0.0,
        loop=not (cfg.rx.arch == "mixed_signal" and has_ffe), voters=int(voters),
        lock_shift=lock_shift)
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
    # sinusoidal jitter (either clock): what the loop leaves of each tone is a
    # swing of the sampling instant, read as its arcsine on top of the smear
    # (stat_loop.sj_phase_offsets); a third tone and on joins the Gaussian
    sj_resid = sj_residual_ui(cfg, loop_sol)
    sj_d, sj_w, sj_rest = sj_phase_offsets(sj_resid, osr)
    sj_span = float(np.max(np.abs(sj_d)))
    sj_rms = float(np.sqrt(sum(a * a / 2.0 for a, _ in sj_resid)))
    if sj_rest > 0.0:
        sigma_ui = float(np.hypot(sigma_ui, sj_rest))
    held = sigma_ui > 0 or sj_span > 0
    # the phase whose main cursor sets the slicer's levels: the mixed-signal
    # time engine takes them once, at the pulse peak, where its bang-bang
    # loop does not sample (the PAM4 32G preset locks 2 samples after it);
    # an ADC receiver's LMS FFE pulls its output to them where it samples,
    # as does a lane read at an offset with its equalisers adapted there.
    # Held at the lock instead, the PAM4 32G preset read 0.70-0.95x the time
    # engine, at the peak 0.94-1.15x (ROADMAP P2 #30). Since 2026-10-21 the
    # time engine's levels follow the sampled amplitude when they adapt
    # (DfeConfig.level_adapt, P2 #31), so the thresholds sit at the lock
    # point's main cursor again, as for the ADC receiver; and since
    # 2026-11-01 it seeds them (and the taps) at the lock in any case
    # (DfeConfig.init "lock", the default; ROADMAP P2 #45)
    adapts_levels = (cfg.rx.arch == "mixed_signal"
                     and ((cfg.rx.dfe.adapt != "none" and cfg.rx.dfe.level_adapt == "sign_sign")
                          or (cfg.rx.dfe.init == "lock" and cfg.rx.cdr.kind == "bang_bang")))
    i_level = (int(np.argmin(np.abs(phi_offsets)))
               if cfg.rx.arch == "mixed_signal" and phase_ui is None and not adapts_levels
               else i_lock)
    # something held under the smear: DFE taps, or PAM4's thresholds
    holds = n_dfe > 0 or levels_norm.size > 2

    def as_computed(i: int) -> bool:
        """The phase as first computed is already what the smear holds there."""
        return not holds or (i == i_lock and i_level == i_lock)

    smears = (tuple(float(np.hypot(sg, sj_rest)) for sg in loop_sol.sigma_over_run_ui)
              if loop_sol is not None and loop_sol.sigma_over_run_ui else (sigma_ui,))
    # A bang-bang loop's phase is not Gaussian about the lock: its detector
    # saturates, so the density's tails are exponential and asymmetric
    # (bb_stationary_density, ROADMAP P2 #32). Its mean curve is taken on the
    # raw pulse the kernel's detector reads, over the span the smear reaches.
    bb_u = bb_curve = bb_density = None
    pattern_phase = None
    if loop_sol is not None and cfg.rx.arch == "mixed_signal" and held:
        span = float(min(8.0 * max(smears) * osr, osr / 2.0))
        bb_u = np.linspace(-span, span, 1601)
        bb_curve = _bb_mean_curve(pulse_pre, osr, cfg.rx.noise_rms, swing, levels_norm, bb_u,
                                  extra_edge_ui=bb_edge_jitter_ui(cfg),
                                  level_probs=level_probabilities(cfg.modulation))
    sub = 4 if sigma_ui * osr < 1.0 else 2 if sigma_ui * osr < 2.0 else 1
    fine_span = (int(min(osr // 2, np.ceil(8.0 * max(smears) * osr + sj_span)))
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
    return SimpleNamespace(**locals())


def _stat_phase(ctx: SimpleNamespace, pi: int, fixed: bool, frac: float) -> None:
    """One entry of ``ctx.tasks``: the slicer PDF at phase ``pi`` (``frac``
    of a sample further with the pulse interpolated; ``fixed``: with the
    taps and thresholds held where the receiver adapted them), its SER /
    BER into ``ctx``'s arrays or ``fine_vals``. What it rebinds that a
    later phase or the read uses goes back into ``ctx``."""
    (ber_phi, ber_sym_phi, bit_dist, bits_per_sym, cfg, curve_mu, curve_var, drift_projected,
        dv, edge_jit, ep_factor_phi, eye_pdf, ffe_taps, fine_vals, head_energy_phi, i_level, i_lock,
        k_state, level_probs, level_sigma, levels_norm, lock_dfe, main_phi, mlsd_mem, n_dfe,
        n_levels, n_post, n_pre, n_t, noise_acf, noise_k, noise_sigma, osr, pair_bits, pair_cond,
        pair_words, pattern_base, pattern_sigma, peak, phi_offsets, pr_active, pr_composite, pulse,
        rx_sigma, ser_phi, ser_sym_phi, swing, tx_jit_phi, union_ratio, v_bins, v_centers, x_drift,
        x_slicer) = _take(ctx, ("ber_phi", "ber_sym_phi", "bit_dist", "bits_per_sym", "cfg",
        "curve_mu", "curve_var", "drift_projected", "dv", "edge_jit", "ep_factor_phi", "eye_pdf",
        "ffe_taps", "fine_vals", "head_energy_phi", "i_level", "i_lock", "k_state", "level_probs",
        "level_sigma", "levels_norm", "lock_dfe", "main_phi", "mlsd_mem", "n_dfe", "n_levels",
        "n_post", "n_pre", "n_t", "noise_acf", "noise_k", "noise_sigma", "osr", "pair_bits",
        "pair_cond", "pair_words", "pattern_base", "pattern_sigma", "peak", "phi_offsets",
        "pr_active", "pr_composite", "pulse", "rx_sigma", "ser_phi", "ser_sym_phi", "swing",
        "tx_jit_phi", "union_ratio", "v_bins", "v_centers", "x_drift", "x_slicer"))
    try:
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
        ev_pats = None
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
            ev_pats = _pr_patterns(fams, 12)

        if pattern_sigma is not None:
            # the binned neighbours leave the random ISI and become a shift
            # (with a PR sequence detector a full-size one for every event:
            # their kernels are per level, so they are not projected on it)
            pos = [n_pre + j if j < 0 else n_pre + j - 1 for j in _BINNED_NEIGHBOURS]
            c_bin = np.array([isi[p] for p in pos])
            isi = isi.copy()
            isi[pos] = 0.0
        pair_pdf = None                     # the +-1 cursors per level (PAM6 pairs)
        c_pair = None
        if pair_cond is not None and pattern_sigma is None:
            pos = [n_pre - 1, n_pre]        # c(-1), c(+1) with the main cursor removed
            c_pair = np.array([isi[q] for q in pos])
            isi_rest = isi.copy()
            isi_rest[pos] = 0.0
            pdf = isi_pdf(isi_rest, levels_norm, v_centers, level_probs)
            pair_pdf = _pair_isi_pdfs(c_pair, levels_norm, v_centers, level_probs, pair_cond)
        else:
            pdf = isi_pdf(isi, levels_norm, v_centers, level_probs)
        xcs = []
        for xy in x_slicer:
            # every one of the aggressor's cursors the victim's sampling
            # instants at this phase fall on (its data is independent of the
            # victim's, so which one is "cursor 0" does not matter)
            xc = xy[np.arange((peak + off) % osr, xy.size, osr)] * swing
            if xc.size:
                xc = xc[np.abs(xc) >= 1e-4 * np.abs(xc).max()]
            xcs.append(xc)
            pdf = _convolve_centred(pdf, isi_pdf(xc, levels_norm, v_centers, level_probs))
        for _, x_pdf in x_drift:
            pdf = _convolve_centred(pdf, x_pdf)
        pdf_isi = pdf
        pdf_isi_lv = None
        pdf_isi_pos = None
        if pair_pdf is not None:
            pdf_isi_lv = [_convolve_centred(pdf, pk) for pk in pair_pdf[0]]
            # the partial-response events project the independent-symbol PDF
            pdf_isi = _convolve_centred(pdf, pair_pdf[1])
            # everything but the partner, per position: the partner's level
            # enters the slicer tails as a shift, so the bits its error costs
            # can be the pair's own (pair_bits) rather than the mean over
            # partners -- a symbol errs with the partners that push it over
            # the threshold, and those are not the ones that cost the most
            # (B6 read 1.17-1.34 bits per PAM6 error, the checker 1.01-1.06)
            pdf_isi_pos = [_convolve_centred(pdf, fp) for fp in pair_pdf[2]]
        pair_noisy: dict = {}

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
            pdf = isi_pdf(np.correlate(d_ev, c_full, "full") / dd, levels_norm, v_centers,
                          level_probs)
            for xc in xcs:
                pdf = _convolve_centred(pdf, isi_pdf(np.correlate(d_ev, xc, "full") / dd,
                                                     levels_norm, v_centers, level_probs))
            for k, (sets, _) in enumerate(x_drift):
                key = (k, d_ev.tobytes())
                if key not in drift_projected:   # independent of the victim's phase
                    drift_projected[key] = np.mean(
                        [isi_pdf(np.correlate(d_ev, xc, "full") / dd, levels_norm, v_centers,
                                 level_probs)
                         for xc in sets], axis=0)
                pdf = _convolve_centred(pdf, drift_projected[key])
            return pdf

        def noisy(g_ev: float, pdf_base: np.ndarray | None = None) -> list:
            """ISI + noise PDF per transmitted level, the noise divided by g_ev."""
            pdf = pdf_isi if pdf_base is None else pdf_base
            # noise kernel: one for an electrical link; one per *transmitted*
            # level when the noise follows the level (an inverting channel flips
            # the slicer order, not which symbol carried the most light)
            if pattern_sigma is not None:
                pdf_lv = []
                for k in range(n_levels):
                    acc = np.zeros(v_bins)
                    for combo in np.ndindex(*pattern_sigma.shape[1:]):
                        w_pat = float(np.prod(level_probs[list(combo)]))
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
                        acc += blurred * w_pat
                    pdf_lv.append(acc / max(acc.sum(), 1e-300))
            elif level_sigma is None:
                nk = noise_k if g_ev == 1.0 else gaussian_kernel(noise_sigma / g_ev, dv)
                if pdf_isi_lv is not None and pdf_base is None:
                    pdf_lv = []
                    for pk in pdf_isi_lv:
                        q = np.convolve(pk, nk, mode="same") if nk.size > 1 else pk
                        pdf_lv.append(q / max(q.sum(), 1e-300))
                    pair_noisy["pos"] = []
                    for pk in pdf_isi_pos:
                        q = np.convolve(pk, nk, mode="same") if nk.size > 1 else pk
                        pair_noisy["pos"].append(q / max(q.sum(), 1e-300))
                else:
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
            pair_noisy.pop("pos", None)
            pdf_lv = noisy(g_ev, None if ev_diffs is None else projected_isi(ev_diffs[i_ev]))
            pos_lv = pair_noisy.get("pos")

            # tail CDFs per level
            cdf_up = [np.cumsum(p[::-1])[::-1] for p in pdf_lv]   # P(x >= v)
            cdf_dn = [np.cumsum(p) for p in pdf_lv]               # P(x <= v)

            def tail_ge(v: float, j: int, cdf=cdf_up) -> float:
                i = int(np.searchsorted(v_centers, v))
                return float(cdf[j][i]) if i < v_bins else 0.0

            def tail_le(v: float, j: int, cdf=cdf_dn) -> float:
                i = int(np.searchsorted(v_centers, v)) - 1
                return float(cdf[j][i]) if i >= 0 else 0.0

            # SER/BER over levels: distance to adjacent thresholds = |main|*gap/2
            lv = levels_norm * main
            order = np.argsort(lv)
            lv_sorted = lv[order]
            lt_lv = levels_norm * thr_main
            jit_lv = tx_jit_phi[i_level] if tx_jit_phi is not None else None
            if jit_lv is not None and jit_lv.level_mean is not None:
                # DCD: adapting levels sit at their mean offset, the
                # thresholds between them (stat_noise.dcd_absorbed)
                lt_lv = lt_lv + jit_lv.level_mean
            lt = lt_lv[order]
            if pos_lv is not None:
                # PAM6 pairs: the partner's level is a shift of the slicer
                # tails, the bits its error costs the pair's own (P2 #33)
                pos_up = [np.cumsum(p[::-1])[::-1] for p in pos_lv]
                pos_dn = [np.cumsum(p) for p in pos_lv]
                for j in range(n_levels):
                    s_j = int(order[j])
                    thr_up = (lt[j] + lt[j + 1]) / 2.0 - lv_sorted[j] if j < n_levels - 1 else None
                    thr_dn = (lt[j - 1] + lt[j]) / 2.0 - lv_sorted[j] if j > 0 else None
                    for pi_pos in (0, 1):
                        c_part = float(c_pair[0] if pi_pos == 0 else c_pair[1])
                        for m in range(n_levels):
                            w_km = 0.5 * float(level_probs[s_j] * pair_cond[s_j, m])
                            if w_km == 0.0:
                                continue
                            shift = c_part * float(levels_norm[m])
                            p_up = tail_ge(thr_up - shift, pi_pos, pos_up) if thr_up is not None else 0.0
                            p_dn = tail_le(thr_dn - shift, pi_pos, pos_dn) if thr_dn is not None else 0.0
                            b_up = float(pair_bits[pi_pos, s_j, m, int(order[j + 1])]) if p_up else 0.0
                            b_dn = float(pair_bits[pi_pos, s_j, m, int(order[j - 1])]) if p_dn else 0.0
                            ser += w_ev * w_km * (p_up + p_dn)
                            nbe += w_ev * w_km * (p_up * b_up + p_dn * b_dn)
                if pdf_eye is None:
                    pdf_eye = pdf_lv
                continue
            for j in range(n_levels):
                s_j = int(order[j])
                p_err_up = p_err_dn = 0.0
                b_up = b_dn = 0.0                # bits the neighbour decided costs
                if j < n_levels - 1:
                    thr = (lt[j] + lt[j + 1]) / 2.0
                    p_err_up = tail_ge(thr - lv_sorted[j], s_j)
                    b_up = float(bit_dist[s_j, int(order[j + 1])])
                if j > 0:
                    thr = (lt[j - 1] + lt[j]) / 2.0
                    p_err_dn = tail_le(thr - lv_sorted[j], s_j)
                    b_dn = float(bit_dist[s_j, int(order[j - 1])])
                if pair_bits is not None and ev_pats is not None and not cfg.precode:
                    # PAM6 pairs under a sequence detector: the event's bits are
                    # those of the pairs it touches (an alternating pair moves
                    # its word diagonally, less than two single steps), per
                    # symbol error since w_ev carries the event's error count
                    pat = ev_pats[i_ev]
                    if p_err_up:
                        step = int(order[j + 1]) - s_j
                        b_up = pair_event_bits(tuple(step * int(e) for e in pat), s_j) / pat.size
                    if p_err_dn:
                        step = int(order[j - 1]) - s_j
                        b_dn = pair_event_bits(tuple(step * int(e) for e in pat), s_j) / pat.size
                ser += w_ev * (p_err_up + p_err_dn) * level_probs[s_j]
                nbe += w_ev * (p_err_up * b_up + p_err_dn * b_dn) * level_probs[s_j]
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
            # eye. Each the bits the first one cost. First DFE tap only. With the ADC's
            # DFE engaged (ROADMAP P1 #21; tap 0.57) the time engine read
            # 1.8-2.9x this engine's ideal-DFE BER.
            #
            # Behind an FFE the receiver's noise is coloured: white per UI at
            # the FFE input (the receiver's and the ADC's), it reaches the
            # slicer with covariance sigma_rx^2 * sum w_i w_(i+l) / |w|^2 at
            # lag l. The noise that made the first error is large, and its
            # neighbour's mean follows it (lag 1 is negative behind a
            # peaking FFE: along the DFE's push), so a first error is followed
            # with p1 > p; an error the push made is not noise-driven, the
            # next one sees the two before it (p2, lower), and the one after a
            # chain error two chain errors' small noises (p3, between: the
            # mean pushes back less). Errors per first one
            # K = 1 + p1 (1 + p2 / (1 - p3)); the chain's rate s K / (1 + s K).
            # Example 31's link frozen at its lock: 1.50-1.71 errors per first
            # one against the time engine's 1.77-1.84 before, 1.74-1.81 with
            # p2 for all of the chain (cairn/DSP发端与PR.md §30); at 4 mV the
            # time engine's p1 / p2 / p3 0.57 / 0.25 / 0.43, the model's 0.58 /
            # 0.23 / 0.38, 1.79 per first against 1.82; the 224G preset 0.72 /
            # 0.39 / 0.48 against 0.69 / 0.35 / 0.52, 2.20 against 2.32 (2.06
            # with p2 for all; ROADMAP P2 #37, 2026-10-31). Without colour (no
            # FFE, the mixed-signal receiver) p1 = p2 = p3 = p and this is the
            # two-state chain above.
            step = float(np.min(np.diff(np.sort(levels_norm)))) * h_dfe1

            def p_follow(shift: float) -> float:
                p = 0.0
                for j in range(n_levels):
                    for sgn in (1.0, -1.0):
                        p_j = 0.0
                        if j < n_levels - 1:
                            thr = (lt[j] + lt[j + 1]) / 2.0
                            p_j += tail_ge(thr - lv_sorted[j] - sgn * shift, int(order[j]))
                        if j > 0:
                            thr = (lt[j - 1] + lt[j]) / 2.0
                            p_j += tail_le(thr - lv_sorted[j] - sgn * shift, int(order[j]))
                        p += p_j * level_probs[int(order[j])] / 2.0
                return min(p, 0.99)

            p_ep = p_follow(step)
            ep = 1.0 / (1.0 - p_ep + min(ser, 1.0))
            w_ffe = (np.asarray(ffe_taps, dtype=np.float64)
                     if ffe_taps is not None and len(ffe_taps) > 2 else None)
            if w_ffe is not None and rx_sigma > 0.0:
                ww = float(w_ffe @ w_ffe)
                c1 = rx_sigma ** 2 * float(w_ffe[:-1] @ w_ffe[1:]) / ww
                c2 = rx_sigma ** 2 * float(w_ffe[:-2] @ w_ffe[2:]) / ww
                p_avg = sum(level_probs[int(order[j])] * pdf_lv[int(order[j])]
                            for j in range(n_levels))
                p_avg = p_avg / max(float(p_avg.sum()), 1e-300)
                var_x = float(p_avg @ v_centers ** 2)
                var_n = noise_sigma ** 2
                a = float(np.min(np.diff(lt))) / 2.0       # the level's distance to a threshold

                def tail_mean(t: float) -> float:
                    m = v_centers >= t
                    mass = float(p_avg[m].sum())
                    return float(p_avg[m] @ v_centers[m]) / mass if mass > 0.0 else t

                if var_x > 0.0 and var_n > 0.0 and (c1 != 0.0 or c2 != 0.0):
                    # the first error's deviation and its noise part (E[n | x])
                    n_k = tail_mean(a) * var_n / var_x
                    step1 = step - c1 / var_n * n_k        # the neighbour's mean joins the push
                    p1 = p_follow(step1)
                    # the second error goes the way the push does (against
                    # the first when step1 > 0); its noise in its own frame,
                    # given it erred, and the first's
                    flip = -1.0 if step1 >= 0.0 else 1.0
                    n_first = flip * n_k
                    n_second = (flip * c1 / var_n * n_k
                                + var_n / var_x * tail_mean(a - abs(step1)))
                    cf = np.linalg.solve(np.array([[var_n, c1], [c1, var_n]]), np.array([c2, c1]))
                    m2 = float(cf[0] * n_first + cf[1] * n_second)   # the third's mean there
                    p2 = p_follow(step - m2)
                    # deeper in the chain the two noises before are both a
                    # chain error's, small: the next one's mean pushes back
                    # less (p3 > p2). Iterated to its steady state, a few steps
                    p3 = p2
                    n_prev, m_cur = n_second, m2
                    for _ in range(12):
                        shift = step - m_cur
                        flip = -1.0 if shift >= 0.0 else 1.0
                        n_cur = flip * m_cur + var_n / var_x * tail_mean(a - abs(shift))
                        m_cur = float(cf[0] * flip * n_prev + cf[1] * n_cur)
                        p3 = p_follow(step - m_cur)
                        n_prev = n_cur
                    k_chain = 1.0 + p1 * (1.0 + p2 / (1.0 - p3))
                    ep = k_chain / (1.0 + min(ser, 1.0) * k_chain)
            if not fixed:
                ep_factor_phi[pi] = ep
            ser *= ep
            nbe *= ep
        if fixed:
            ser_v, ber_v = ser, nbe / bits_per_sym
            if pr_active and mlsd_mem == 0:
                s_sym, b_sym = pr_symbol_decisions(
                    pdf_lv, v_centers, levels_norm * main, head,
                    composite=pr_composite, precoded=cfg.precode,
                    bit_dist=bit_dist, level_probs=level_probs,
                    pair_cond=pair_cond, pair_words=pair_words)
                ser_v, ber_v = s_sym, b_sym / bits_per_sym
            fine_vals[float(off + frac)] = (ser_v, ber_v)
            return
        ser_phi[pi] = ser
        ber_phi[pi] = nbe / bits_per_sym
        if pr_active:
            # the per-symbol decisions the LMS and the CDR read; without a
            # sequence detector they are the detector, error propagation and all
            s_sym, b_sym = pr_symbol_decisions(
                pdf_lv if mlsd_mem == 0 else noisy(1.0), v_centers, levels_norm * main, head,
                composite=pr_composite, precoded=cfg.precode,
                bit_dist=bit_dist, level_probs=level_probs,
                pair_cond=pair_cond, pair_words=pair_words)
            ser_sym_phi[pi], ber_sym_phi[pi] = s_sym, b_sym / bits_per_sym
            head_energy_phi[pi] = float(np.sum(np.square(head[1:])))
            if mlsd_mem == 0:
                ser_phi[pi], ber_phi[pi] = ser_sym_phi[pi], ber_sym_phi[pi]

        # marginal slicer PDF (mixture over transmitted levels) for the eye
        eye_col = np.zeros(v_bins)
        for j in range(n_levels):
            _shift_add(eye_col, pdf_lv[j], lv[j] / dv, float(level_probs[j]))
        eye_pdf[:, pi] = eye_col
    finally:
        ctx.edge_jit = edge_jit
        ctx.pattern_sigma = pattern_sigma
        ctx.union_ratio = union_ratio


def _stat_read(ctx: SimpleNamespace) -> StatResult:
    """``run_statistical`` after its phase loop: the smear, the read at the
    receiver's lock point (its loop's density, the pattern-phase factor),
    the warnings, and the result."""
    (as_computed, bb_curve, bb_density, bb_u, ber_phi, cfg, ep_factor_phi, eye_pdf, fine_span,
        fine_vals, head_energy_phi, held, hi_fix, level_probs, level_sigma, levels_norm, lo_fix,
        lock_pos, loop_sol, mlsd_mem, n_dfe, n_levels, noise_sigma, osr, own_clock, pattern_phase,
        peak, phi_offsets, pr_active, pulse, pulse_pre, ser_phi, ser_sym_phi, sigma_ui, smears,
        swing, tx_jit_phi, v_centers, voters, x_drift, x_slicer) = _take(ctx, ("as_computed",
        "bb_curve", "bb_density", "bb_u", "ber_phi", "cfg", "ep_factor_phi", "eye_pdf", "fine_span",
        "fine_vals", "head_energy_phi", "held", "hi_fix", "level_probs", "level_sigma",
        "levels_norm", "lo_fix", "lock_pos", "loop_sol", "mlsd_mem", "n_dfe", "n_levels",
        "noise_sigma", "osr", "own_clock", "pattern_phase", "peak", "phi_offsets", "pr_active",
        "pulse", "pulse_pre", "ser_phi", "ser_sym_phi", "sigma_ui", "smears", "swing", "tx_jit_phi",
        "v_centers", "voters", "x_drift", "x_slicer"))
    sj_d, sj_w, sj_rms, sj_resid = ctx.sj_d, ctx.sj_w, ctx.sj_rms, ctx.sj_resid
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
    if sj_d.size > 1:
        # the tones' swing on the bathtub: its arcsine, ends held as the smear pads them
        def swung(curve):
            return np.sum([w * np.interp(phi_offsets + d, phi_offsets, curve)
                           for d, w in zip(sj_d, sj_w)], axis=0)
        ber_phi, ser_phi = swung(ber_phi), swung(ser_phi)
        if pr_active:
            ser_sym_phi = swung(ser_sym_phi)

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
            if bb_curve is not None:
                # under a tone the loop's noise meets the detector averaged over
                # the swing (it flattens sooner): the density's well is that curve's
                curve_sj = bb_curve
                if sj_d.size > 1:
                    curve_sj = np.sum([w * np.interp(bb_u + dj, bb_u, bb_curve)
                                       for dj, w in zip(sj_d, sj_w)], axis=0)
                densities = [bb_stationary_density(bb_u, curve_sj, sg * osr) for sg in smears]
                ber = float(np.mean([sum(w * _smeared_with(cb, x, lock + dj, bb_u, d)
                                         for dj, w in zip(sj_d, sj_w)) for d in densities]))
                ser = float(np.mean([sum(w * _smeared_with(cs, x, lock + dj, bb_u, d)
                                         for dj, w in zip(sj_d, sj_w)) for d in densities]))
                bb_density = np.mean(densities, axis=0)
                # the phase and the data are not independent on NRZ with a
                # DFE: the runs that vote the loop late are the runs whose
                # samples it then errs on (_pattern_phase_factor, P3 #13)
                kp_s, ki_s = cdr_gains(cfg.rx.cdr, osr)[:2]
                # the chain is the loop's phase as its votes make it; where the
                # clock's wander the loop leaves is most of the smear (a profile
                # clock), the phase is the clock's, not the data's, and the chain
                # does not hold that path: a 16 GBd link under a -50 dB profile
                # (tests/test_rx_clock.py, 0.14 UI) ran it 2000 steps without
                # converging, its marginal 25x off the density, factor 1, 75 s.
                # Nor where the loop is underdamped enough to slip (the warning
                # below): no stationary joint to find (kp_shift 8 on the NRZ 16G
                # preset, 600 steps unconverged, 49 s, factor 1)
                # Nor where a tone the loop leaves swings it more than its own
                # noise: the factor is the chain over the read without the tone,
                # and on a read with it collapsed (NRZ 16G preset, 0.5 / 1 UI at
                # 1 GHz: 6e-4 / 2e-6, the BER falling as the tone grew)
                votes_hold = (loop_sol.sigma_untracked_ui <= loop_sol.sigma_self_ui
                              and sj_rms <= loop_sol.sigma_self_ui
                              and (loop_sol.damping is None
                                   or loop_sol.damping >= _BB_DAMPING_WARN))
                if (n_levels == 2 and n_dfe > 0 and not pr_active and mlsd_mem == 0 and kp_s > 0
                        and votes_hold):
                    def _jit_at(off_s: int):
                        if tx_jit_phi is None:
                            return None
                        i_j = int(np.clip(off_s - int(phi_offsets[0]), 0, len(tx_jit_phi) - 1))
                        return tx_jit_phi[i_j]
                    x_var = None
                    if x_slicer or x_drift:
                        # the aggressors' variance where the victim samples
                        # (sync: its cursors at that phase; quasi-sync: the
                        # mean over the phases it walks through), as the ISI
                        # PDF above takes them, for the factor's Gaussian rest
                        sym_var = float(np.sum(level_probs * levels_norm ** 2))
                        x_grids = [(np.arange(xy.size, dtype=np.float64), xy) for xy in x_slicer]
                        drift_var = sum(sym_var * float(np.mean([np.sum(xc ** 2) for xc in sets]))
                                        for sets, _ in x_drift)

                        def x_var(pos, _g=x_grids, _d=drift_var, _sv=sym_var):
                            pos = np.atleast_1d(np.asarray(pos, dtype=np.float64))
                            v = np.full(pos.shape, float(_d))
                            for grid_x, xy in _g:
                                for i_p, p in enumerate(pos):
                                    idx = np.arange(p % osr, xy.size, osr)
                                    xc = np.interp(idx, grid_x, xy) * swing
                                    v[i_p] += _sv * float(np.sum(xc ** 2))
                            return v
                    pattern_phase = _pattern_phase_factor(
                        pulse, pulse_pre, osr, lock, noise_sigma, swing, n_dfe, bb_u, bb_density,
                        kp_s, edge_jitter=_jit_at, xtalk_var=x_var,
                        edge_jitter_ui=bb_edge_jitter_ui(cfg), ki=ki_s)
                    f_pp = (pattern_phase.factor if own_clock and voters <= 1
                            else pattern_phase.factor_marginal if voters <= 1 else 1.0)
                    ber *= f_pp
                    ser *= f_pp
            else:
                ber = float(np.mean([sum(w * _smeared_at(cb, x, lock + dj, sg * osr)
                                         for dj, w in zip(sj_d, sj_w)) for sg in smears]))
                ser = float(np.mean([sum(w * _smeared_at(cs, x, lock + dj, sg * osr)
                                         for dj, w in zip(sj_d, sj_w)) for sg in smears]))
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
                "engine", stacklevel=3)
    return StatResult(
        ber_phi=ber_phi, ser_phi=ser_phi, best_phi=best,
        ber=ber, ser=ser,
        eye_pdf=eye_pdf, v_centers=v_centers,
        phi_ui=phi_offsets / osr,
        extras={"peak": peak, "noise_sigma": noise_sigma,
                "level_sigma": level_sigma,
                "jitter_sigma_ui": sigma_ui,
                "sj_residual_ui": tuple(sj_resid),
                "clock_loop": loop_sol,
                # the bang-bang sampler's phase about the lock [UI] and its
                # density [1/UI] the read was taken with, and the detector's
                # mean output over the same offsets; None for a Gaussian smear
                "sampling_density": (None if bb_density is None
                                     else (bb_u / osr, bb_density * osr)),
                "detector_curve": None if bb_curve is None else (bb_u / osr, bb_curve),
                # the data-phase correlation the read was multiplied by on
                # NRZ with a DFE under a bang-bang loop (PatternPhase); None
                # where it does not apply
                "pattern_phase": pattern_phase,
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
