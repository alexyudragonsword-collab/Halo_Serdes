"""Jitter decomposition (PyBERT calc_jitter three-layer method).

1. **Pattern averaging** separates data-dependent jitter: averaging TIE over
   repetitions of the pattern period keeps only data-correlated components
   -> ISI (pk-pk of the averaged trace) and DCD (rise/fall mean offset).
2. **Spectral thresholding** splits the data-independent residue: spectral
   lines above an adaptive threshold are periodic jitter (Pj), the rest is
   random jitter (Rj = std of the residual).
3. **Dual-Dirac fit**: Gaussian fits to the two tails of the data-independent
   histogram give (mu_L, mu_R, sigma) for bathtub extrapolation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit

from .metrics import qfunc


@dataclass
class JitterResult:
    tie: np.ndarray          # raw TIE per edge [s]
    isi: float               # data-dependent pk-pk [s]
    dcd: float               # |mean(rise) - mean(fall)| [s]
    pj: float                # periodic pk-pk [s]
    rj: float                # random sigma [s]
    mu_l: float              # dual-Dirac left/right means [s]
    mu_r: float
    rj_dd: float             # dual-Dirac sigma [s]

    def summary(self, ui: float) -> str:
        u = 100.0 / ui
        return (f"ISI={self.isi * u:.2f}%UI DCD={self.dcd * u:.2f}%UI "
                f"Pj={self.pj * u:.2f}%UI Rj={self.rj * u:.3f}%UI "
                f"(dual-Dirac: sigma={self.rj_dd * u:.3f}%UI)")


def find_crossings(y: np.ndarray, dt: float, thresh: float = 0.0) -> np.ndarray:
    """Linear-interpolated threshold crossing times [s]."""
    s = np.signbit(y - thresh)
    idx = np.nonzero(s[:-1] != s[1:])[0]
    frac = (thresh - y[idx]) / (y[idx + 1] - y[idx])
    return (idx + frac) * dt


def tie_from_crossings(xings: np.ndarray, ui: float) -> tuple[np.ndarray, np.ndarray]:
    """Crossing times -> (TIE [s], edge symbol-slot indices)."""
    slots = np.round(xings / ui)
    return xings - slots * ui, slots.astype(np.int64)


def calc_jitter(y: np.ndarray, dt: float, ui: float, pattern_len: int,
                thresh: float = 0.0, rel_thresh: float = 3.0) -> JitterResult:
    """Full decomposition of a repeating-pattern waveform's zero crossings."""
    xings = find_crossings(y, dt, thresh)
    tie, slots = tie_from_crossings(xings, ui)

    # rising/falling classification from post-crossing slope
    ridx = np.clip((xings / dt).astype(np.int64) + 1, 0, y.size - 1)
    rising = y[ridx] > thresh

    # --- 1. pattern averaging, rising/falling separated (PyBERT method:
    # polarity split keeps DCD out of the ISI number) ---
    slot_in_pat = slots % pattern_len
    dd_avg = np.zeros(tie.size)
    isi_ptp = []
    for sel in (rising, ~rising):
        if not sel.any():
            continue
        s_sum = np.zeros(pattern_len)
        s_cnt = np.zeros(pattern_len)
        np.add.at(s_sum, slot_in_pat[sel], tie[sel])
        np.add.at(s_cnt, slot_in_pat[sel], 1)
        have = s_cnt > 0
        avg = np.zeros(pattern_len)
        avg[have] = s_sum[have] / s_cnt[have]
        isi_ptp.append(float(avg[have].max() - avg[have].min()) if have.any() else 0.0)
        dd_avg[sel] = avg[slot_in_pat[sel]]
    isi = max(isi_ptp) if isi_ptp else 0.0

    r_mean = float(np.mean(tie[rising])) if rising.any() else 0.0
    f_mean = float(np.mean(tie[~rising])) if (~rising).any() else 0.0
    dcd = abs(r_mean - f_mean)

    # data-independent residue (polarity-specific average removed)
    tie_ind = tie - dd_avg

    # --- 2. spectral Pj/Rj split ---
    # resample TIE(edge) onto a uniform edge index grid (edges are the clock)
    n = tie_ind.size
    if n >= 64:
        spec = np.fft.rfft(tie_ind - tie_ind.mean())
        mag = np.abs(spec)
        med = np.median(mag)
        mad = np.median(np.abs(mag - med)) + 1e-30
        peaks = mag > med + rel_thresh * 1.4826 * mad
        peaks[0] = False
        spec_pj = np.where(peaks, spec, 0.0)
        tie_pj = np.fft.irfft(spec_pj, n)
        pj = float(tie_pj.max() - tie_pj.min()) if peaks.any() else 0.0
        rj = float(np.std(tie_ind - tie_pj))
    else:
        pj, rj = 0.0, float(np.std(tie_ind))

    # --- 3. dual-Dirac tail fit ---
    mu_l, mu_r, rj_dd = _dual_dirac(tie_ind)
    return JitterResult(tie=tie, isi=isi, dcd=dcd, pj=pj, rj=rj,
                        mu_l=mu_l, mu_r=mu_r, rj_dd=rj_dd)


def _dual_dirac(tie_ind: np.ndarray) -> tuple[float, float, float]:
    """Fit Gaussians to the two half-height tails of the TIE histogram."""
    if tie_ind.size < 100:
        s = float(np.std(tie_ind))
        return -s, s, s

    scale = 1e12  # fit in picoseconds to keep curve_fit well-conditioned
    x = tie_ind * scale
    hist, edges = np.histogram(x, bins=100, density=True)
    centers = (edges[:-1] + edges[1:]) / 2

    def gaus(v, a, mu, sig):
        return a * np.exp(-0.5 * ((v - mu) / sig) ** 2)

    half = hist.max() / 2
    try:
        # left tail: bins left of the first half-height crossing
        li = int(np.argmax(hist >= half))
        ri = len(hist) - int(np.argmax(hist[::-1] >= half))
        lsel = slice(0, max(li, 3))
        rsel = slice(min(ri, len(hist) - 3), len(hist))
        pl, _ = curve_fit(gaus, centers[lsel], hist[lsel],
                          p0=[hist.max(), centers[max(li - 1, 0)], np.std(x) / 2],
                          maxfev=5000)
        pr, _ = curve_fit(gaus, centers[rsel], hist[rsel],
                          p0=[hist.max(), centers[min(ri, len(hist) - 1)], np.std(x) / 2],
                          maxfev=5000)
        mu_l, sig_l = pl[1] / scale, abs(pl[2]) / scale
        mu_r, sig_r = pr[1] / scale, abs(pr[2]) / scale
        return float(mu_l), float(mu_r), float((sig_l + sig_r) / 2)
    except (RuntimeError, ValueError):
        s = float(np.std(x) / scale)
        return -s, s, s


def make_bathtub(jr: JitterResult, ui: float, n_pts: int = 201) -> tuple[np.ndarray, np.ndarray]:
    """Timing bathtub BER(t) across the UI from the dual-Dirac model:
    BER(t) = 0.5[Q((t - mu_l)/sigma) + Q((ui - t + mu_r ... )] (PyBERT
    make_bathtub folded-CDF construction, Gaussian-extrapolated)."""
    t = np.linspace(0, ui, n_pts)
    left = qfunc((t - (jr.mu_l + 0.0)) / max(jr.rj_dd, 1e-18))
    right = qfunc(((ui - t) + jr.mu_r) / max(jr.rj_dd, 1e-18))
    return t, 0.5 * np.clip(left + right, 1e-30, 1.0)
