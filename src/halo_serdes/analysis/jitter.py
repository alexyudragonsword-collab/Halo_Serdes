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


def edge_crossings(y: np.ndarray, dt: float, thresh: float = 0.0
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Crossing times [s], and whether each edge rises (from the sample after it)."""
    xings = find_crossings(y, dt, thresh)
    ridx = np.clip((xings / dt).astype(np.int64) + 1, 0, y.size - 1)
    return xings, y[ridx] > thresh


class CrossingCollector:
    """:func:`edge_crossings` of a waveform that arrives in pieces.

    Gives the whole-array result exactly, whatever the pieces: a crossing
    between samples i and i + 1 is classified from sample i + 1 or i + 2 (the
    float division can land either side of an integer), so the last two
    samples wait for the next piece, and :meth:`result` settles them with the
    whole-array clip at the end. Holds two numbers per edge, not the waveform.
    """

    def __init__(self, dt: float, thresh: float = 0.0):
        self.dt, self.thresh = dt, thresh
        self.tail = np.zeros(0)
        self.off = 0                 # absolute index of tail[0]
        self.first = None            # sample 0
        self.xings: list[np.ndarray] = []
        self.rising: list[np.ndarray] = []

    def _take(self, buf: np.ndarray, last: int, n_total: int) -> None:
        """The crossings that start at local indices below ``last``."""
        s = np.signbit(buf - self.thresh)
        idx = np.nonzero(s[:-1] != s[1:])[0]
        idx = idx[idx < last]
        frac = (self.thresh - buf[idx]) / (buf[idx + 1] - buf[idx])
        xg = ((idx + self.off) + frac) * self.dt
        ridx = np.clip((xg / self.dt).astype(np.int64) + 1, 0, n_total - 1)
        # a -0.0 next to +0.0 is a crossing with a NaN time, whose index the
        # whole-array clip sends to sample 0 -- long gone from the buffer
        here = ridx >= self.off
        val = np.full(idx.size, self.first)
        val[here] = buf[ridx[here] - self.off]
        self.xings.append(xg)
        self.rising.append(val > self.thresh)

    def feed(self, y: np.ndarray) -> None:
        buf = np.concatenate([self.tail, np.asarray(y, dtype=np.float64)])
        if self.first is None and buf.size:
            self.first = float(buf[0])
        if buf.size < 3:
            self.tail = buf
            return
        # a crossing starting before buf.size - 2 has its i + 2 in hand
        self._take(buf, buf.size - 2, np.iinfo(np.int64).max)
        self.tail = buf[buf.size - 2:]
        self.off += buf.size - 2

    def result(self) -> tuple[np.ndarray, np.ndarray]:
        if self.tail.size >= 2:
            self._take(self.tail, self.tail.size, self.off + self.tail.size)
            self.off += self.tail.size
        self.tail = np.zeros(0)
        return (np.concatenate(self.xings) if self.xings else np.zeros(0),
                np.concatenate(self.rising) if self.rising else np.zeros(0, dtype=bool))


def calc_jitter(y: np.ndarray, dt: float, ui: float, pattern_len: int,
                thresh: float = 0.0, rel_thresh: float = 3.0) -> JitterResult:
    """Full decomposition of a repeating-pattern waveform's zero crossings."""
    xings, rising = edge_crossings(y, dt, thresh)
    return jitter_from_crossings(xings, rising, ui, pattern_len, rel_thresh)


def jitter_from_crossings(xings: np.ndarray, rising: np.ndarray, ui: float,
                          pattern_len: int, rel_thresh: float = 3.0) -> JitterResult:
    """:func:`calc_jitter` from the crossings (:func:`edge_crossings`)."""
    tie, slots = tie_from_crossings(xings, ui)

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


# two-sided Q scaling for total-jitter extrapolation (2 * Qinv(BER))
_TJ_Q = {1e-6: 9.507, 1e-9: 11.996, 1e-12: 14.069, 1e-15: 15.612}


def total_jitter(jr: JitterResult, ber: float = 1e-12) -> float:
    """Peak-to-peak total jitter at a target BER [s].

    Classic dual-Dirac decomposition: TJ(BER) = Dj_pp + 2*Qinv(BER)*Rj,
    with the bounded deterministic part Dj_pp = ISI + DCD + Pj and the
    random tail extrapolated Gaussian. Uses the dual-Dirac sigma for Rj.
    """
    q = _TJ_Q.get(ber)
    if q is None:
        # 2 * inverse-Q(BER), from the complementary error function
        from scipy.special import erfcinv

        q = 2.0 * np.sqrt(2.0) * float(erfcinv(2.0 * ber))
    dj_pp = jr.isi + jr.dcd + jr.pj
    return dj_pp + q * jr.rj_dd


def pattern_period(pattern: str, modulation: str) -> int:
    """Symbol-domain repetition period of a PRBS/PRQS pattern name.

    For PRBS-N (NRZ) the bit sequence repeats every 2^N-1 bits; PAM4 maps
    2 bits/symbol so the symbol period is (2^N-1) when 2^N-1 is odd (always,
    since 2^N-1 is odd) — the LFSR period in symbols is 2^N-1. Q-coded
    PRBS-NQ and PRQS10 repeat every 2^N-1 symbols directly.
    """
    p = pattern.lower()
    if p == "prqs10":
        return 2 ** 10 - 1
    if p.startswith("prbs") and p.endswith("q"):
        return 2 ** int(p[4:-1]) - 1
    if p.startswith("prbs"):
        order = int(p[4:])
        period_bits = 2 ** order - 1
        if modulation == "pam4":
            # 2 bits/symbol; period_bits is odd so full period is 2*period_bits
            # bits == (2^N-1) symbols only after two LFSR laps -> lcm handling
            return period_bits  # symbol pattern realigns every 2^N-1 symbols
        return period_bits
    raise ValueError(f"no known period for pattern {pattern!r}")


def stage_jitter_budget(stages: dict[str, np.ndarray], dt: float, ui: float,
                        pattern_len: int, thresh: float = 0.0,
                        rel_thresh: float = 3.0) -> dict[str, JitterResult]:
    """Run calc_jitter at each named observation point in the signal chain.

    `stages` maps a stage label (e.g. 'tx', 'chnl', 'ctle') to its
    oversampled waveform samples. Returns label -> JitterResult. This is the
    per-stage jitter budget PyBERT produces on every run; here it is an
    opt-in analysis pass over waveforms captured by the time engine.

    A stage may also be a :class:`CrossingCollector` that saw the waveform
    in pieces (the streamed engine); the result is the same.
    """
    out: dict[str, JitterResult] = {}
    for name, y in stages.items():
        if isinstance(y, CrossingCollector):
            out[name] = jitter_from_crossings(*y.result(), ui, pattern_len, rel_thresh)
        else:
            out[name] = calc_jitter(np.asarray(y, dtype=np.float64), dt, ui,
                                     pattern_len, thresh, rel_thresh)
    return out


def format_jitter_budget(budget: dict[str, JitterResult], ui: float,
                         ber: float = 1e-12) -> str:
    """ASCII per-stage jitter budget table (all figures in %UI)."""
    u = 100.0 / ui
    hdr = (f"{'stage':<8}{'ISI':>9}{'DCD':>9}{'Pj':>9}"
           f"{'Rj(rms)':>10}{'TJ@%g' % ber:>11}")
    lines = [hdr, "-" * len(hdr)]
    for name, jr in budget.items():
        tj = total_jitter(jr, ber) * u
        lines.append(
            f"{name:<8}{jr.isi * u:>8.2f}%{jr.dcd * u:>8.2f}%"
            f"{jr.pj * u:>8.2f}%{jr.rj * u:>9.3f}%{tj:>10.2f}%")
    return "\n".join(lines)


def make_bathtub(jr: JitterResult, ui: float, n_pts: int = 201) -> tuple[np.ndarray, np.ndarray]:
    """Timing bathtub BER(t) across the UI from the dual-Dirac model:
    BER(t) = 0.5[Q((t - mu_l)/sigma) + Q((ui - t + mu_r ... )] (PyBERT
    make_bathtub folded-CDF construction, Gaussian-extrapolated)."""
    t = np.linspace(0, ui, n_pts)
    left = qfunc((t - (jr.mu_l + 0.0)) / max(jr.rj_dd, 1e-18))
    right = qfunc(((ui - t) + jr.mu_r) / max(jr.rj_dd, 1e-18))
    return t, 0.5 * np.clip(left + right, 1e-30, 1.0)
