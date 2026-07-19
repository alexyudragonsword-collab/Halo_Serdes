"""IEEE 802.3 Channel Operating Margin (COM), Clause 93A / 178A method.

This is the faithful COM procedure, distinct from the transparent RSS figure of
merit in :class:`halo_serdes.io.ami.NativeCom`. It reuses the statistical
engine's exact PDF-convolution machinery (:func:`isi_pdf`, :func:`gaussian_kernel`)
so the noise amplitude at the target detector error ratio is read off the
*convolved* interference-plus-noise distribution, not a Gaussian RSS.

The recipe (93A/178A, behavioral-level):

1. **Equalizer optimization.** Search a grid of Rx CTLE peaking (and, optionally,
   Tx FFE presets). For each candidate the through pulse response is rebuilt
   (Tx FIR ⊗ channel ⊗ CTLE), sampled at the baud interval at each of ``osr``
   sampling phases. A reference DFE with ``n_dfe`` taps is set directly from the
   cursors, ``b_n = clamp(h_post_n / h_main, ±b_max)`` (the 802.3 rule — taps
   are *derived*, not adapted), cancelling the covered postcursors.
2. **Figure of merit.** ``FOM = A_s / sqrt(σ_ISI² + σ_XT² + σ_N² + σ_J²)``
   (Gaussian approximation) selects the best (CTLE, Tx, phase).
3. **A_ni via PDF.** For the winner the full noise PDF is built by convolving the
   residual-ISI PMF, each aggressor's cursor PMF, the Gaussian device+RJ kernel
   and the dual-Dirac (DCD) jitter kernel; ``A_ni`` is the amplitude where the
   one-sided tail equals the target DER.
4. ``COM = 20 log10(A_s / A_ni)`` dB.

``A_s`` is the signal amplitude = main cursor × half the minimum normalized
level spacing (so PAM4's inner eye and R_LM are handled automatically).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..afe import Ctle
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.waveform import Waveform
from ..engine.statistical import _shift_add, gaussian_kernel, isi_pdf
from ..engine.static_link import _levels


@dataclass
class ComParams:
    """COM search + noise parameters (802.3 electrical baseline defaults)."""

    target_der: float = 1e-4               # detector error ratio (Q≈3.72)
    n_dfe: int | None = None               # DFE taps; None -> cfg.rx.dfe.n_taps
    b_max: float = 1.0                     # per-tap DFE bound |b_n| ≤ b_max
    ctle_peak_grid_db: tuple[float, ...] | None = None  # None -> auto sweep
    tx_fir_grid: tuple[tuple[float, ...], ...] | None = None  # None -> cfg Tx only
    v_bins: int = 8192
    n_pre_cursor: int = 20
    n_post_cursor: int = 80


@dataclass
class ComResult93a:
    """Faithful-COM result (superset of io.ami.ComResult fields)."""

    com_db: float
    a_signal: float                        # A_s [V]
    a_noise: float                         # A_ni at target DER [V]
    fom_db: float                          # optimized figure of merit [dB]
    fom_isi: float                         # σ contributions [V rms] (Gaussian)
    fom_xtalk: float
    fom_noise: float
    fom_jitter: float
    detail: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (f"COM={self.com_db:.2f} dB  (A_s={self.a_signal:.4f} V, "
                f"A_ni={self.a_noise:.4f} V, FOM={self.fom_db:.2f} dB; "
                f"σ_ISI={self.fom_isi:.4f} σ_XT={self.fom_xtalk:.4f} "
                f"σ_N={self.fom_noise:.4f} σ_J={self.fom_jitter:.4f})")


def _channel_impulse(channel: ChannelModel, cfg: LinkConfig) -> np.ndarray:
    return channel.response_set(cfg.dt).h.y


def _apply_ctle(h: np.ndarray, cfg: LinkConfig, peak_db: float) -> np.ndarray:
    fp1 = cfg.rx.ctle.fp1 if cfg.rx.ctle.fp1 is not None else cfg.f_nyquist
    ctle = Ctle(gdc_db=cfg.rx.ctle.gdc_db, peak_db=peak_db, fp1=fp1,
                fp2=cfg.rx.ctle.fp2, fz=cfg.rx.ctle.fz)
    nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
    f = np.fft.rfftfreq(nfft, d=cfg.dt)
    return np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: 2 * h.size]


def _apply_tx_fir(h: np.ndarray, taps, osr: int) -> np.ndarray:
    if taps is None or len(taps) <= 1:
        return h
    fir_up = np.zeros((len(taps) - 1) * osr + 1)
    fir_up[::osr] = taps
    return np.convolve(h, fir_up)


def _baud_cursors(pulse_y: np.ndarray, peak: int, phase: int, osr: int,
                  n_pre: int, n_post: int) -> tuple[np.ndarray, int]:
    """Baud-spaced cursors around the peak at sampling ``phase``; returns
    ``(cursors, main_index)``."""
    idx = peak + phase + np.arange(-n_pre, n_post + 1) * osr
    valid = (idx >= 0) & (idx < pulse_y.size)
    c = np.zeros(idx.size)
    c[valid] = pulse_y[idx[valid]]
    return c, n_pre


def _dfe_residual(cursors: np.ndarray, main_i: int, n_dfe: int,
                  b_max: float) -> np.ndarray:
    """Residual ISI cursors after a reference DFE cancels ``n_dfe`` postcursors
    with per-tap bound ``b_max`` (802.3: b_n = clamp(h_n/h_0, ±b_max))."""
    main = cursors[main_i]
    resid = cursors.copy()
    resid[main_i] = 0.0
    if main != 0.0:
        for n in range(1, n_dfe + 1):
            j = main_i + n
            if j < resid.size:
                b = np.clip(resid[j] / main, -b_max, b_max)
                resid[j] -= b * main          # cancelled portion removed
    return resid


def compute_com(channel: ChannelModel, cfg: LinkConfig,
                xtalk_pulses: list[Waveform] | None = None,
                params: ComParams | None = None) -> ComResult93a:
    """Compute COM (dB) for ``channel`` under ``cfg`` per the 93A/178A method."""
    p = params or ComParams()
    osr = cfg.osr
    n_dfe = p.n_dfe if p.n_dfe is not None else cfg.rx.dfe.n_taps

    # normalized symbol levels and per-symbol variance
    levels_norm = _levels(cfg) / (cfg.tx.swing / 2.0)      # {-1,1} or 4-PAM
    sym_var = float(np.mean(levels_norm ** 2))
    lv_sorted = np.sort(levels_norm)
    half_gap = float(np.min(np.diff(lv_sorted))) / 2.0     # inner-eye half spacing

    # search grids
    peak_grid = p.ctle_peak_grid_db
    if peak_grid is None:
        if cfg.rx.ctle.enable:
            base = cfg.rx.ctle.peak_db
            peak_grid = tuple(sorted({0.0, 2.0, 4.0, 6.0, 8.0, 10.0, base}))
        else:
            peak_grid = (0.0,)
    tx_grid = p.tx_fir_grid or (tuple(cfg.tx.fir_taps),)

    h_ch = _channel_impulse(channel, cfg)
    swing = cfg.tx.swing / 2.0

    # precompute aggressor baud-cursor magnitudes per phase (phase-independent
    # peak alignment: sample each aggressor pulse around its own peak)
    agg_cursors = []
    if xtalk_pulses:
        for xp in xtalk_pulses:
            xpk = int(np.argmax(np.abs(xp.y)))
            xc, _ = _baud_cursors(xp.y, xpk, 0, osr, p.n_pre_cursor, p.n_post_cursor)
            agg_cursors.append(xc * swing)

    best = None  # (fom, dict)
    for peak_db in peak_grid:
        h_ct = _apply_ctle(h_ch, cfg, peak_db) if cfg.rx.ctle.enable else h_ch
        for taps in tx_grid:
            h_eq = _apply_tx_fir(h_ct, taps, osr)
            pulse = pulse_from_impulse(Waveform(h_eq, cfg.dt), osr)
            pk = int(np.argmax(np.abs(pulse.y)))
            for phase in range(-(osr // 2), osr - osr // 2):
                c, mi = _baud_cursors(pulse.y, pk, phase, osr,
                                      p.n_pre_cursor, p.n_post_cursor)
                c = c * swing
                main = c[mi]
                if main <= 0:
                    continue
                resid = _dfe_residual(c, mi, n_dfe, p.b_max)
                a_s = main * half_gap

                sig_isi = float(np.sqrt(np.sum(resid ** 2) * sym_var))
                sig_xt = float(np.sqrt(sum(np.sum(ac ** 2) for ac in agg_cursors)
                                       * sym_var)) if agg_cursors else 0.0
                sig_n = float(cfg.rx.noise_rms)
                # jitter: slope at the sampling instant -> voltage noise
                si = pk + phase
                slope = 0.0
                if 0 < si < pulse.y.size - 1:
                    slope = abs(pulse.y[si + 1] - pulse.y[si - 1]) / (2 * cfg.dt)
                slope *= swing
                a_dd_v = slope * cfg.tx.dcd_ui * cfg.ui         # dual-Dirac half
                sig_rj_v = slope * cfg.tx.rj_ui * cfg.ui        # RJ sigma
                sig_j = float(np.sqrt(sig_rj_v ** 2 + a_dd_v ** 2))

                sig_tot = np.sqrt(sig_isi ** 2 + sig_xt ** 2 + sig_n ** 2
                                  + sig_j ** 2)
                fom = a_s / max(sig_tot, 1e-18)
                if best is None or fom > best[0]:
                    best = (fom, dict(
                        peak_db=peak_db, taps=taps, phase=phase, main=main,
                        a_s=a_s, resid=resid, sig_isi=sig_isi, sig_xt=sig_xt,
                        sig_n=sig_n, sig_j=sig_j, a_dd_v=a_dd_v,
                        sig_rj_v=sig_rj_v))

    b = best[1]

    # --- final A_ni from the convolved interference-plus-noise PDF ----------
    span = (abs(b["main"]) * 2.5 + 8 * (b["sig_n"] + b["sig_rj_v"] + b["a_dd_v"])
            + 1e-6)
    v = np.linspace(-span, span, p.v_bins)
    dv = v[1] - v[0]
    pdf = isi_pdf(b["resid"], levels_norm, v)
    for ac in agg_cursors:
        pdf = np.convolve(pdf, isi_pdf(ac, levels_norm, v), mode="same")
    gk = gaussian_kernel(float(np.hypot(b["sig_n"], b["sig_rj_v"])), dv)
    if gk.size > 1:
        pdf = np.convolve(pdf, gk, mode="same")
    if b["a_dd_v"] > dv:                       # dual-Dirac DCD kernel
        dd = np.zeros_like(pdf)
        _shift_add(dd, pdf, -b["a_dd_v"] / dv, 0.5)
        _shift_add(dd, pdf, b["a_dd_v"] / dv, 0.5)
        pdf = dd
    pdf = pdf / max(pdf.sum(), 1e-300)

    # one-sided tail P(noise >= x); A_ni where tail == target DER
    tail = np.cumsum(pdf[::-1])[::-1]
    pos = v >= 0
    vp, tp = v[pos], tail[pos]
    der = p.target_der
    if tp[-1] >= der:
        a_ni = float(vp[-1])                  # PDF grid too narrow; clamp
    elif tp[0] <= der:
        a_ni = 0.0
    else:
        k = int(np.searchsorted(-tp, -der))   # tp is decreasing
        k = min(max(k, 1), tp.size - 1)
        # log-linear interpolation in probability
        t0, t1 = tp[k - 1], tp[k]
        w = (np.log(max(t0, 1e-300)) - np.log(der)) / \
            (np.log(max(t0, 1e-300)) - np.log(max(t1, 1e-300)))
        a_ni = float(vp[k - 1] + w * (vp[k] - vp[k - 1]))

    com = 20.0 * np.log10(max(b["a_s"], 1e-18) / max(a_ni, 1e-18))
    return ComResult93a(
        com_db=float(com), a_signal=float(b["a_s"]), a_noise=float(a_ni),
        fom_db=float(20.0 * np.log10(max(best[0], 1e-18))),
        fom_isi=b["sig_isi"], fom_xtalk=b["sig_xt"], fom_noise=b["sig_n"],
        fom_jitter=b["sig_j"],
        detail={"ctle_peak_db": b["peak_db"], "tx_fir_taps": b["taps"],
                "sample_phase": b["phase"], "main_cursor": b["main"],
                "n_dfe": n_dfe, "b_max": p.b_max, "target_der": der,
                "n_aggressors": len(agg_cursors)})
