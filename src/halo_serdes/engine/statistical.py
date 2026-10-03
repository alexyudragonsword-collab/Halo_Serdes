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
- sampling jitter: BER(phi) smeared with the RJ Gaussian on the phase axis;
- receive-side partial response (``cfg.pr``): the controlled cursor (the
  first postcursor of the FFE-shaped pulse, whatever it is at each phase)
  leaves the ISI PDF; with a sequence detector it joins the trellis cursors
  [1, alpha, r...], without one it is cancelled like an ideal DFE tap. The
  DFE then starts after it. With a sequence detector the BER is not the
  plain MLSD's minimum-distance gain but a union bound over the alternating error
  events, in the noise the shaping FFE coloured (``pr_error_events``): the
  minimum distance alone was 2.5-20x optimistic against the time engine;
  the bound is within 1.5x at BER <= 5e-4 and loose (up to 3x pessimistic)
  above 1e-2. The time engine's per-symbol PR decision and its LMS
  reference are not modelled (outside the LTI + AWGN cross-check);
- sampling phase: the ADC receiver is read at the bathtub minimum; the
  mixed-signal receiver at the bang-bang loop's lock point (where its edge
  samples balance, ``cdr.linear.lock_offset_samples``), because that loop
  does not look for minimum BER and with a DFE the two are 0.1-0.2 UI and up
  to 8x in BER apart (``extras['ber_min_phase']`` keeps the minimum);
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
  chain stays linear; the curve's bending of ISI is the time engine's alone
  and a warning says so;
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
    best_phi: int                # index into the phase axis (0 = pulse peak)
    ber: float                   # at best phase
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
                    precoded: bool, max_len: int = 12) -> list[tuple[float, float]]:
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
    """
    h = np.asarray(cursors, dtype=float)
    rho = np.asarray(noise_acf, dtype=float) / float(noise_acf[0])
    out = []
    for n in range(1, max_len + 1):
        e = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        d = np.convolve(e, h)
        acf_d = np.correlate(d, d, "full")[d.size - 1:]
        k = min(acf_d.size, rho.size)
        proj = acf_d[0] + 2.0 * float(np.dot(acf_d[1:k], rho[1:k]))
        g = float(acf_d[0] / np.sqrt(max(proj, 1e-300)))
        if precoded:
            errs = int(np.count_nonzero(np.convolve(e, [1.0, 1.0]).round().astype(int) % n_levels))
        else:
            errs = n
        out.append((g, ((n_levels - 1) / n_levels) ** (n - 1) * errs))
    return out


def gaussian_kernel(sigma: float, dv: float, n_sigma: float = 8.0) -> np.ndarray:
    if sigma <= 0:
        return np.array([1.0])
    half = max(1, int(np.ceil(n_sigma * sigma / dv)))
    x = np.arange(-half, half + 1) * dv
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def dac_noise_at_slicer(sigma_q: float, h: np.ndarray, dt: float, osr: int) -> float:
    """sigma at the slicer of a white per-UI error of ``sigma_q`` added at the
    DAC output: sqrt(sum_k p_k^2) sigma_q with p_k the symbol response from the
    DAC to the slicer at the main cursor's phase."""
    from ..core.sampler import baud_samples

    p = pulse_from_impulse(Waveform(h, dt), osr).y
    peak = int(np.argmax(np.abs(p)))
    cursors = baud_samples(p, osr, peak % osr)
    return float(sigma_q * np.sqrt(np.sum(cursors ** 2)))


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
    if getattr(channel, "optical", None) is not None:
        if channel.optical.curve is not None:
            import warnings

            warnings.warn(
                "optical topology with a large-signal E/O curve (li_compression > 0): "
                "the statistical engine takes the curve's steady-state levels through a "
                "linear chain and does not see how the curve bends ISI; it is not "
                "covered by the 2x cross-check (invariant #3) -- use the time engine",
                stacklevel=2)
        # same two-stage split as the time engine (engine/optical_stage.py)
        from .optical_stage import (
            slicer_sigma_binned, slicer_sigma_per_level, split_impulses,
        )

        h1, h2 = split_impulses(cfg, channel)
        h = np.convolve(h1, h2)
        if level_sigma is None:
            level_sigma = slicer_sigma_per_level(cfg, channel, h1, h2, ffe_taps)
            pattern_sigma = slicer_sigma_binned(cfg, channel, h1, h2, ffe_taps,
                                                neighbours=_BINNED_NEIGHBOURS)
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
    tx_resp = tx_pipe.equivalent_symbol_response(osr)
    if tx_resp is not None:
        h = np.convolve(h, tx_resp)
    noise_sigma = cfg.rx.noise_rms
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
    if pr_active and peak >= osr and abs(pulse.y[peak - osr]) >= 0.5 * abs(pulse.y[peak]):
        # equalised to [1, a] the pulse has two comparable cursors (equal at
        # a = 1) and nothing before them: the main one is the earlier
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
    v_centers = np.linspace(-span, span, v_bins)
    dv = v_centers[1] - v_centers[0]
    noise_k = gaussian_kernel(noise_sigma, dv)

    eye_pdf = np.zeros((v_bins, osr))
    ser_phi = np.zeros(osr)
    ber_phi = np.zeros(osr)

    n_levels = levels_norm.size
    bits_per_sym = cfg.bits_per_symbol
    # noise autocorrelation at the slicer, symbol spacing: white at the FFE
    # input (the receiver noise is band-limited to the baud rate), coloured
    # by the taps; only the partial-response union bound reads it
    noise_acf = np.ones(1)
    if pr_active and mlsd_mem > 0 and ffe_taps is not None and len(ffe_taps) > 1:
        w = np.asarray(ffe_taps, dtype=float)
        noise_acf = np.correlate(w, w, "full")[w.size - 1:]

    for pi, off in enumerate(phi_offsets):
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
        # decision subtracts it like an ideal DFE tap)
        head = [1.0]
        if pr_active:
            head.append(isi[n_pre] / main if main else 0.0)
            isi = isi.copy()
            isi[n_pre] = 0.0
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
            events = pr_error_events(np.concatenate([head, np.asarray(res)]), noise_acf,
                                     n_levels, cfg.precode)

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
        ser = 0.0
        nbe = 0.0
        pdf_isi = pdf
        pdf_eye = None
        for g_ev, w_ev in events:
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
                        shifted = np.zeros(v_bins)
                        _shift_add(shifted, pdf, float(np.dot(c_bin, levels_norm[list(combo)])) / dv, 1.0)
                        nk = gaussian_kernel(
                            np.sqrt(noise_sigma ** 2 + pattern_sigma[(k,) + combo] ** 2) / g_ev, dv)
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

    best = int(np.argmin(ber_phi))
    ber, ser = float(ber_phi[best]), float(ser_phi[best])
    lock_ui = None
    if cfg.rx.arch == "mixed_signal":
        # Report what the receiver samples, not the best it could. The
        # bang-bang loop settles where its edge samples balance, which is not
        # where BER is lowest: a DFE moves the bathtub's floor 0.1-0.2 UI away
        # from it. With an FFE this is the static engine, which has no loop and
        # samples at the pre-FFE pulse peak. Either point is found on the
        # pre-FFE pulse, then moved to the equalised pulse's phase axis (the
        # FFE's main tap delays it by ffe_pre UI).
        pre_y = pulse_from_impulse(Waveform(h_pre_ffe, cfg.dt), osr).y
        pk_pre = int(np.argmax(np.abs(pre_y)))
        has_ffe = ffe_taps is not None and len(ffe_taps) > 1
        at = 0.0 if has_ffe else lock_offset_samples(pre_y, pk_pre, osr, osr // 2)
        lag = pk_pre + at + (ffe_pre * osr if has_ffe else 0) - peak
        lock = (lag + osr / 2) % osr - osr / 2
        ber = float(np.interp(lock, phi_offsets, ber_phi))
        ser = float(np.interp(lock, phi_offsets, ser_phi))
        best = int(np.argmin(np.abs(phi_offsets - lock)))
        lock_ui = lock / osr
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
                "ber_min_phase": float(ber_phi.min())})


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
