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
- crosstalk: aggressor cursor sets convolved in as independent stationary
  interference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..afe import Ctle
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.waveform import Waveform
from ..dsp.mlsd import mlse_min_distance_sq
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


def gaussian_kernel(sigma: float, dv: float, n_sigma: float = 8.0) -> np.ndarray:
    if sigma <= 0:
        return np.array([1.0])
    half = max(1, int(np.ceil(n_sigma * sigma / dv)))
    x = np.arange(-half, half + 1) * dv
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def run_statistical(cfg: LinkConfig, channel: ChannelModel | None = None,
                    ffe_taps: np.ndarray | None = None, ffe_pre: int = 0,
                    v_bins: int = 4096, n_pre: int = 24, n_post: int = 64,
                    xtalk_pulses: list[Waveform] | None = None) -> StatResult:
    osr = cfg.osr
    if channel is None:
        channel = ChannelModel.from_config(cfg)

    # --- equalized pulse response: Tx FIR (x) channel (x) CTLE [(x) FFE] ---
    ch_rs = channel.response_set(cfg.dt)
    h = ch_rs.h.y
    if cfg.rx.ctle.enable:
        ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
        nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
        f = np.fft.rfftfreq(nfft, d=cfg.dt)
        h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: 2 * h.size]
    h = h * cfg.rx.vga_gain
    if len(cfg.tx.fir_taps) > 1:
        fir_up = np.zeros((len(cfg.tx.fir_taps) - 1) * osr + 1)
        fir_up[::osr] = cfg.tx.fir_taps
        h = np.convolve(h, fir_up)
    noise_sigma = cfg.rx.noise_rms
    if ffe_taps is not None and len(ffe_taps) > 1:
        w_up = np.zeros((len(ffe_taps) - 1) * osr + 1)
        w_up[::osr] = ffe_taps
        h = np.convolve(h, w_up)
        noise_sigma = noise_sigma * float(np.linalg.norm(ffe_taps))
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)

    swing = cfg.tx.swing / 2.0  # symbol amplitude scale (levels_norm in [-1,1])
    levels_norm = _levels(cfg) / swing  # {-1,1} or {-1,-1/3,1/3,1}*rlm

    peak = int(np.argmax(np.abs(pulse.y)))
    n_dfe = cfg.rx.dfe.n_taps
    mlsd_mem = cfg.rx.mlsd.memory if cfg.rx.mlsd.kind != "none" else 0

    # phase axis: one UI centered on the pulse peak
    phi_offsets = np.arange(osr) - osr // 2

    # voltage grid sized to worst-case ISI + noise
    span = float(np.abs(pulse.y).max()) * swing * 2.5 + 8 * noise_sigma + 1e-6
    v_centers = np.linspace(-span, span, v_bins)
    dv = v_centers[1] - v_centers[0]
    noise_k = gaussian_kernel(noise_sigma, dv)

    eye_pdf = np.zeros((v_bins, osr))
    ser_phi = np.zeros(osr)
    ber_phi = np.zeros(osr)

    n_levels = levels_norm.size
    bits_per_sym = cfg.bits_per_symbol

    for pi, off in enumerate(phi_offsets):
        # cursors at this phase (volts, per unit symbol level)
        idx = peak + off + np.arange(-n_pre, n_post + 1) * osr
        valid = (idx >= 0) & (idx < pulse.y.size)
        c = np.zeros(idx.size)
        c[valid] = pulse.y[idx[valid]]
        c = c * swing
        main = c[n_pre]
        isi = np.delete(c, n_pre)
        # ideal DFE removes the first n_dfe postcursors
        if n_dfe > 0:
            isi_list = list(isi)
            for d in range(n_dfe):
                pos = n_pre + d  # index into isi (post side starts at n_pre)
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
        sigma_eff = noise_sigma
        if mlsd_mem > 0:
            res = []
            isi_list = list(isi)
            for d in range(mlsd_mem):
                pos = n_pre + n_dfe + d       # postcursors after the DFE's
                if pos < len(isi_list):
                    res.append(isi_list[pos] / main if main else 0.0)
                    isi_list[pos] = 0.0       # resolved, not interference
            isi = np.asarray(isi_list)
            if res:
                g = np.sqrt(mlse_min_distance_sq(
                    np.concatenate([[1.0], np.asarray(res)])))
                sigma_eff = noise_sigma / max(g, 1e-12)

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
        nk = noise_k if sigma_eff == noise_sigma else gaussian_kernel(sigma_eff, dv)
        if nk.size > 1:
            pdf = np.convolve(pdf, nk, mode="same")
        pdf = pdf / max(pdf.sum(), 1e-300)

        # tail CDFs
        cdf_up = np.cumsum(pdf[::-1])[::-1]   # P(x >= v)
        cdf_dn = np.cumsum(pdf)               # P(x <= v)

        def tail_ge(v: float) -> float:
            i = int(np.searchsorted(v_centers, v))
            return float(cdf_up[i]) if i < v_bins else 0.0

        def tail_le(v: float) -> float:
            i = int(np.searchsorted(v_centers, v)) - 1
            return float(cdf_dn[i]) if i >= 0 else 0.0

        # SER/BER over levels: distance to adjacent thresholds = |main|*gap/2
        ser = 0.0
        nbe = 0.0
        lv = levels_norm * main
        order = np.argsort(lv)
        lv_sorted = lv[order]
        for j in range(n_levels):
            p_err_up = p_err_dn = 0.0
            if j < n_levels - 1:
                thr = (lv_sorted[j] + lv_sorted[j + 1]) / 2.0
                p_err_up = tail_ge(thr - lv_sorted[j])
            if j > 0:
                thr = (lv_sorted[j - 1] + lv_sorted[j]) / 2.0
                p_err_dn = tail_le(thr - lv_sorted[j])
            ser += (p_err_up + p_err_dn) / n_levels
            nbe += (p_err_up + p_err_dn) / n_levels  # Gray: adjacent = 1 bit
        ser_phi[pi] = ser
        ber_phi[pi] = nbe / bits_per_sym

        # marginal slicer PDF (mixture over transmitted levels) for the eye
        eye_col = np.zeros(v_bins)
        for j in range(n_levels):
            _shift_add(eye_col, pdf, lv[j] / dv, 1.0 / n_levels)
        eye_pdf[:, pi] = eye_col

    # sampling-jitter smearing on the phase axis (RJ only for now)
    if cfg.tx.rj_ui > 0:
        sig_phi = cfg.tx.rj_ui * osr
        k = gaussian_kernel(sig_phi, 1.0)
        pad = k.size // 2
        bp = np.pad(ber_phi, pad, mode="edge")
        ber_phi = np.convolve(bp, k, mode="valid")
        sp = np.pad(ser_phi, pad, mode="edge")
        ser_phi = np.convolve(sp, k, mode="valid")

    best = int(np.argmin(ber_phi))
    return StatResult(
        ber_phi=ber_phi, ser_phi=ser_phi, best_phi=best,
        ber=float(ber_phi[best]), ser=float(ser_phi[best]),
        eye_pdf=eye_pdf, v_centers=v_centers,
        phi_ui=phi_offsets / osr,
        extras={"peak": peak, "noise_sigma": noise_sigma})
