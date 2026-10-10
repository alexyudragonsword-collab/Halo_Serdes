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
- DFE: ideal cancellation of the covered postcursors (weights assumed exact);
- FFE: noise enhancement sigma_eq = sigma * ||w||_2;
- ADC receiver: the ADC's noise, fullscale^2/12 * 2^(-2 ENOB) (bare
  quantisation without an ENOB), joins the receiver noise at the FFE input
  (``adc_noise_sigma``); clipping and TI lane mismatch are not modelled;
- sampling jitter: BER(phi) smeared with the RJ Gaussian on the phase axis;
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
  a = 0.5), each event less its overlap with the one before 0.78-2.09x;
  this 0.67-1.55x across receive and transmit PR, two to four cursors, at
  BER 7e-5 to 2e-2. The per-symbol decisions the LMS and
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
  minimum);
- coloured clock (``tx.clock.kind = "profile"``): the CDR is linearised
  (``cdr/linear.py``) and the smear sigma is the profile power the loop does
  not track plus the jitter it acquires from its own detector noise, both
  integrated from 1/(N UI). The bang-bang detector's gain is set by the
  jitter at its input, which here is the untracked clock, the loop's own
  hunting, and AWGN + ISI at the edge sample converted through the edge
  slope of the pre-DFE pulse (first order; a lossy channel's data-dependent
  edge shape is only in it as variance). A receiver clock (``rx.clock``)
  rides through the same error response, since the loop tracks the
  difference of the two clocks; on the bang-bang loop that also means the
  bandwidth shrinks with the *total* input, so two clocks' residues do not
  simply add there (they do on the linear Mueller-Muller loop). A loop
  driven past its slew limit slips cycles, as does a bang-bang loop whose
  detector sees more than ~0.1 UI of phase error; neither is in a linear
  model and both raise a warning. The white kind keeps its RJ-only smear
  (no CDR self-noise), which is also what keeps it byte-identical to the
  pre-profile engine;
- crosstalk: aggressor cursor sets convolved in as independent stationary
  interference;
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

from dataclasses import dataclass, field

import numpy as np

from ..afe import Ctle
from ..cdr.linear import (
    LoopParams, LoopSolution, bb_loop_fixed_point, lock_offset_samples, mm_loop_solution,
    mm_pd_statistics,
)
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.sampler import upsampled_taps
from ..core.waveform import Waveform
from ..dsp.mlsd import mlse_min_distance_sq
from ..tx.pipeline import TxPipeline
from .static_link import _levels


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


def pr_error_events(cursors: np.ndarray, noise_acf: np.ndarray, n_levels: int,
                    precoded: bool, max_len: int = 12,
                    snr: float | None = None,
                    families: tuple[str, ...] = ("alternating",),
                    n_samples: int = 1000, seed: int = 0) -> list[tuple[float, float]]:
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
    of the weights, as the per-level sum adds it to each P(A_j).

    The chained pairwise overlap this replaced (each L less its intersection
    with L - 1) read 0.78-2.09x the time engine on 16 PR links, up to 2.09x
    with a negative third cursor; this 0.67-1.55x
    (``cairn/DSP发端与PR.md`` §14).

    ``families`` adds ``"constant"`` (+1, +1, ...): with a negative
    controlled cursor after the first (b or c < 0) a run of same-sign errors is
    pulled close by it, where for a non-negative target it lies far.
    ``n_samples`` per event and ``seed`` fix the sampling (deterministic).
    """
    from scipy.special import ndtr, ndtri

    h = np.asarray(cursors, dtype=float)
    rho = np.asarray(noise_acf, dtype=float) / float(noise_acf[0])
    pats = [np.where(np.arange(n) % 2 == 0, 1.0, -1.0) if fam == "alternating" else np.ones(n)
            for fam in families for n in range(1, max_len + 1)
            if not (fam == "constant" and n == 1)]       # L = 1 is the same event
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
    as evenly spaced. Bits per wrong symbol follow the checker:
    min(|index difference|, 2).
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
        return min(abs(int(u) - int(v)), 2)

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


def dac_noise_at_slicer(sigma_q: float, h: np.ndarray, dt: float, osr: int) -> float:
    """sigma at the slicer of a white per-UI error of ``sigma_q`` added at the
    DAC output: sqrt(sum_k p_k^2) sigma_q with p_k the symbol response from the
    DAC to the slicer at the main cursor's phase."""
    from ..core.sampler import baud_samples

    p = pulse_from_impulse(Waveform(h, dt), osr).y
    peak = int(np.argmax(np.abs(p)))
    cursors = baud_samples(p, osr, peak % osr)
    return float(sigma_q * np.sqrt(np.sum(cursors ** 2)))


#: PR loop load (per-symbol decision SER x sum c_k^2) from which run_statistical
#: warns that the time engine's MM CDR may slip (measured 0.09-0.14, never below)
_PR_LOOP_LOAD_WARN = 0.08


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
                    level_sigma: np.ndarray | None = None) -> StatResult:
    """``level_sigma``: extra noise sigma at the slicer per transmitted level
    (ascending), added in quadrature to the receiver noise; derived from
    ``channel.optical`` when None. Explicit for studies and for pinning that
    equal kernels reproduce the single-kernel engine."""
    osr = cfg.osr
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
    if tx_pipe.dac_sigma_q > 0.0:
        noise_sigma = float(np.hypot(noise_sigma, dac_noise_at_slicer(
            tx_pipe.dac_sigma_q, h_after_dac, cfg.dt, osr)))
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
    if pr_active and has_ffe and lock_pos is not None:
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

    # phase axis: one UI centered on the pulse peak
    phi_offsets = np.arange(osr) - osr // 2

    # voltage grid sized to worst-case ISI + noise
    span = float(np.abs(pulse.y).max()) * swing * 2.5 + 8 * noise_sigma + 1e-6
    if level_sigma is not None:
        span += 8 * float(level_sigma.max())
    if curve_mu is not None:
        span += float(np.abs(curve_mu).max()) + 8 * float(np.sqrt(curve_var.max()))
    v_centers = np.linspace(-span, span, v_bins)
    dv = v_centers[1] - v_centers[0]
    noise_k = gaussian_kernel(noise_sigma, dv)

    eye_pdf = np.zeros((v_bins, osr))
    ser_phi = np.zeros(osr)
    ber_phi = np.zeros(osr)
    ser_sym_phi = np.zeros(osr)
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
    if pr_active and mlsd_mem > 0 and ffe_taps is not None and len(ffe_taps) > 1:
        w = np.asarray(ffe_taps, dtype=float)
        noise_acf = np.correlate(w, w, "full")[w.size - 1:]

    # the phase the receiver samples goes first: the PR union bound's overlap
    # weights are sampled there once (pr_error_events) and carried to the
    # other phases as ratios to the plain sum's -- per phase they would cost
    # the bathtub's flanks 16x as much for little
    i_lock = osr // 2
    if lock_pos is not None:
        i_lock = int(np.argmin(np.abs(phi_offsets - ((lock_pos - peak + osr / 2) % osr - osr / 2))))
    union_ratio = None
    for pi in [i_lock] + [i for i in range(osr) if i != i_lock]:
        off = phi_offsets[pi]
        # cursors at this phase (volts, per unit symbol level)
        idx = peak + off + np.arange(-n_pre, n_post + 1) * osr
        valid = (idx >= 0) & (idx < pulse.y.size)
        c = np.zeros(idx.size)
        c[valid] = pulse.y[idx[valid]]
        c = c * swing
        main = c[n_pre]
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
        if n_dfe > 0:
            isi_list = list(isi)
            for d in range(n_dfe):
                pos = n_pre + n_t - 1 + d  # index into isi (post side starts at n_pre)
                if pos < len(isi_list):
                    isi_list[pos] = 0.0
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
                    np.concatenate([head, np.asarray(res)])))
                g_mlsd = max(g, 1e-12)
        # error events the per-level sum runs over: one, unless a sequence
        # detector works a partial-response target (pr_error_events)
        events = [(g_mlsd, 1.0)]
        if pr_active and mlsd_mem > 0:
            half_gap = 0.5 * abs(main) * float(np.min(np.diff(np.sort(levels_norm))))
            fams = (("alternating", "constant") if min(cfg.pr.target[2:], default=0.0) < 0.0
                    else ("alternating",))
            cur = np.concatenate([head, np.asarray(res)])
            events = pr_error_events(cur, noise_acf, n_levels, cfg.precode, families=fams)
            if noise_sigma > 0:
                if union_ratio is None:
                    union = pr_error_events(cur, noise_acf, n_levels, cfg.precode,
                                            snr=half_gap / noise_sigma, families=fams)
                    union_ratio = [u[1] / e[1] if e[1] > 0 else 0.0
                                   for u, e in zip(union, events)]
                events = [(g_e, w_e * r) for (g_e, w_e), r in zip(events, union_ratio)]

        if pattern_sigma is not None:
            # the binned neighbours leave the random ISI and become a shift
            pos = [n_pre + j if j < 0 else n_pre + j - 1 for j in _BINNED_NEIGHBOURS]
            c_bin = np.array([isi[p] for p in pos])
            isi = isi.copy()
            isi[pos] = 0.0
        pdf = isi_pdf(isi, levels_norm, v_centers)
        if xtalk_pulses:
            for xp in xtalk_pulses:
                xpk = int(np.argmax(np.abs(xp.y)))
                xidx = xpk + np.arange(-n_pre, n_post + 1) * osr
                xval = (xidx >= 0) & (xidx < xp.y.size)
                xc = np.zeros(xidx.size)
                xc[xval] = xp.y[xidx[xval]]
                pdf = np.convolve(pdf, isi_pdf(xc * swing, levels_norm, v_centers),
                                  mode="same")
        pdf_isi = pdf

        def noisy(g_ev: float) -> list:
            """ISI + noise PDF per transmitted level, the noise divided by g_ev."""
            pdf = pdf_isi
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
                        var = noise_sigma ** 2 + pattern_sigma[(k,) + combo] ** 2
                        if curve_mu is not None:
                            shift += float(curve_mu[(pi, k) + combo])
                            var += float(curve_var[(pi, k) + combo])
                        shifted = np.zeros(v_bins)
                        _shift_add(shifted, pdf, shift / dv, 1.0)
                        nk = gaussian_kernel(np.sqrt(var) / g_ev, dv)
                        acc += (np.convolve(shifted, nk, mode="same") if nk.size > 1 else shifted) / n_pat
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
        for g_ev, w_ev in events:
            pdf_lv = noisy(g_ev)

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
            for j in range(n_levels):
                p_err_up = p_err_dn = 0.0
                if j < n_levels - 1:
                    thr = (lv_sorted[j] + lv_sorted[j + 1]) / 2.0
                    p_err_up = tail_ge(thr - lv_sorted[j], int(order[j]))
                if j > 0:
                    thr = (lv_sorted[j - 1] + lv_sorted[j]) / 2.0
                    p_err_dn = tail_le(thr - lv_sorted[j], int(order[j]))
                ser += w_ev * (p_err_up + p_err_dn) / n_levels
                nbe += w_ev * (p_err_up + p_err_dn) / n_levels  # Gray: adjacent = 1 bit
            if pdf_eye is None:
                pdf_eye = pdf_lv
        pdf_lv = pdf_eye
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

    # sampling-jitter smearing on the phase axis. White clock: the RJ sigma
    # as stated. Profile clock: what the CDR leaves of it, plus the loop's own
    # noise -- see _sampling_jitter_ui and the assumption list above.
    sigma_ui, loop_sol = _sampling_jitter_ui(
        cfg, pulse if cfg.mm_pd_input == "ffe" else
        pulse_from_impulse(Waveform(h_pre_ffe, cfg.dt), osr), levels_norm, swing,
        cfg.rx.noise_rms)
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
                # SER of the per-symbol PR decisions (the time engine's
                # extras['ser_slicer']); None without a PR target
                "ser_slicer": float(ser_sym_phi[best]) if pr_active else None,
                # that SER times the controlled cursors' energy: the load the
                # PR decisions put on the MM CDR (warned from 0.08; None
                # without a PR target or with the detector on raw samples)
                "pr_loop_load": loop_load})


def _sampling_jitter_ui(cfg: LinkConfig, pulse_pd: Waveform, levels_norm: np.ndarray,
                        swing: float, noise_sigma: float) -> tuple[float, LoopSolution | None]:
    """RMS sampling-instant jitter [UI] to smear the bathtub with, and the loop model behind it.

    White clocks on both sides return their RJ in power sum and no model,
    exactly as before profiles existed. A profile on either side (transmit
    or receiver -- the loop tracks their difference, so they are
    interchangeable here) is integrated through the linearised CDR of the
    configured architecture (bang-bang for mixed-signal, Mueller-Muller for
    ADC, matching which kernel the time engine would run) from ``1/(N UI)``,
    the lowest offset a run of ``N`` symbols resolves, so both engines look at
    the same band. The white-kind RJ terms either clock carries on top ride
    through the same error response.
    """
    from ..tx.clock import ClockProfile

    clocks = (cfg.tx.clock, cfg.rx.clock)
    if all(c.kind != "profile" for c in clocks):
        # white on both sides: the historical RJ-only smear, the two clocks'
        # independent Gaussians adding in power
        return float(np.hypot(cfg.tx.clock.rj_ui, cfg.rx.clock.rj_ui)), None
    # The loop tracks the *difference* between the two clocks, so both ride
    # through the same error response and their untracked powers add.
    profs = [ClockProfile.load(c.file, f0_hz=c.f0_hz) if c.kind == "profile" else None
             for c in clocks]
    loop = LoopParams.from_config(cfg)
    ui = cfg.ui
    f_lo = cfg.symbol_rate / max(int(cfg.sim.n_symbols), 2)
    f_nyq = cfg.symbol_rate / 2.0
    f_white = np.linspace(f_lo, f_nyq, 2048)
    rj_white = float(np.hypot(cfg.tx.clock.rj_ui, cfg.rx.clock.rj_ui))

    def untracked(err_fn) -> float:
        var = sum((p.untracked_sigma_s(err_fn, f_lo) / ui) ** 2 for p in profs if p is not None)
        white = rj_white * float(np.sqrt(np.mean(np.asarray(err_fn(f_white)) ** 2)))
        return float(np.sqrt(var + white ** 2))

    if cfg.rx.arch == "adc_dsp":
        peak = int(np.argmax(np.abs(pulse_pd.y)))
        k_pd, var = mm_pd_statistics(pulse_pd.y, cfg.osr, peak, levels_norm * swing, noise_sigma)
        var /= max(int(cfg.rx.adc.n_lanes), 1)        # block average per update
        if k_pd <= 0:
            # no timing gradient: the loop does not move, nothing is tracked
            return untracked(lambda f: np.ones_like(np.asarray(f, dtype=np.float64))), None
        sol = mm_loop_solution(loop, k_pd, var, untracked)
    else:
        sol = bb_loop_fixed_point(
            loop, untracked,
            sigma_edge_ui=_bb_edge_noise_ui(pulse_pd, cfg.osr, noise_sigma, swing, levels_norm))
    for p in profs:
        if p is not None:
            _warn_if_slew_limited(p, loop, f_lo, sol, cfg)
    if cfg.rx.arch != "adc_dsp" and sol.sigma_ui > 0.1:
        import warnings

        warnings.warn(
            f"the bang-bang detector sees {sol.sigma_ui:.3f} UI RMS of phase error "
            "(clock jitter it cannot track plus its own hunting); its linearisation "
            "assumes a small error, and the kernel loses lock from about 0.1 UI "
            "(measured: locked at 0.08, slipping at 0.14) -- the statistical engine's "
            "sampling-jitter model is noise-limited here", stacklevel=3)
    return sol.sigma_ui, sol


def _bb_edge_noise_ui(pulse_pd: Waveform, osr: int, noise_sigma: float, swing: float,
                      levels_norm: np.ndarray, n_pre: int = 24, n_post: int = 64) -> float:
    """What randomises the Alexander edge comparison besides clock jitter, in UI.

    The edge sample sits half a UI before the data sample on the raw (pre-DFE)
    waveform, and the loop locks where, for a transition, its mean is zero:
    where this symbol's half-cursor equals the previous symbol's
    (``lock_offset_samples``). The slope of that difference with timing is
    the edge slope; AWGN and the ISI of every other symbol add voltage noise
    on top. Voltage noise over edge slope is timing noise, which the
    bang-bang detector cannot tell from clock jitter.
    """
    y = np.asarray(pulse_pd.y, dtype=np.float64)
    peak = int(np.argmax(np.abs(y)))
    half = osr // 2
    lock = lock_offset_samples(y, peak, osr, half)
    data = peak + int(round(lock))
    e0, e1 = data - half, data + half           # this symbol's and the previous symbol's cursor

    def at(i: int) -> float:
        return float(y[i]) if 0 <= i < y.size else 0.0

    amp = swing * float(np.mean(np.abs(levels_norm)))
    # d/dtau [c0(tau) - c1(tau)] * amplitude, volts per sample
    slope = amp * ((at(e0 + 1) - at(e0 - 1)) - (at(e1 + 1) - at(e1 - 1))) / 2.0
    if abs(slope) < 1e-18:
        return 0.0
    idx = e0 + np.arange(-n_pre, n_post + 1) * osr
    others = [at(int(i)) for i in idx if 0 <= i < y.size and i not in (e0, e1)]
    isi_var = swing ** 2 * float(np.mean(levels_norm ** 2)) * float(np.sum(np.square(others)))
    sigma_v = float(np.sqrt(isi_var + noise_sigma ** 2))
    return sigma_v / (abs(slope) * osr)


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
