"""TDECQ: transmitter and dispersion eye closure for PAM4 optical signals.

The 802.3 figure of merit for a PAM4 optical transmitter: how much Gaussian
noise its eye could still absorb, after a reference receiver and a reference
equaliser, before the symbol error ratio at the eye's two sampling instants
reaches the target -- expressed as dB of closure relative to an ideal eye.
An ideal transmitter scores 0 dB.

The algorithm follows IEEE 802.3 121.8.5 (400GBASE-DR4, the definition every
later PAM4 optical clause refers to; 802.3cd/cu 138.8.5 / 140.7.5 for
100G/lambda, 802.3db clause 167 for the VCSEL PMDs, 802.3dj for 200G/lambda).
The standard text was not reachable from this environment; each element
below is cited to the clause it comes from as reported by task-force and
instrument-vendor material, and the places where this implementation is a
simplification are marked:

- Reference receiver: fourth-order Bessel-Thomson response with its 3 dB
  point at half the signalling rate (121.8.5.1; 26.5625 GHz at 53.125 GBd;
  802.3dj states 53.125 GHz for 200G/lambda).
- Reference equaliser: T-spaced FFE with tap coefficients summing to 1;
  5 taps with the largest at tap 1 or 2 for 100G/lambda (121.8.5.4,
  138.8.5), 15 taps with up to 3 precursors for 200G/lambda (802.3dj).
  802.3 optimises the taps for minimum TDECQ; here the taps start from a
  constrained least-squares fit at the eye centre (main-tap position chosen
  there) and are refined by a coordinate search on TDECQ itself, which moves
  the result by 0.06 dB on a good eye and over 1 dB on a slow one.
- ``dfe=True``: the 1-tap DFE 802.3dj added to the 200G/lambda reference
  equaliser in draft 2.0, as reported by task-force comment-resolution
  material (second-hand; the drafts were not reachable): coefficient b(1)
  bounded 0 <= b <= 0.3 on its unnormalised value, the FFE taps then sum to
  1 + b so that FFE minus DFE keeps unit gain, and OMA and the thresholds are
  referred to the FFE input (where they are measured here anyway). The DFE
  subtracts b times the previous symbol's nominal level at that scale; its
  decisions are the transmitted symbols (no error propagation -- a pattern-
  locked measurement), and the noise sees only the FFE, so C_eq is the FFE's.
  Not modelled: the later FFE tap limits on w(i)/w(0) and |w(1) - w(-1)|.
- Thresholds: P_th1 = P_ave - OMA_outer / 3, P_th2 = P_ave,
  P_th3 = P_ave + OMA_outer / 3, with P_ave the waveform's mean power.
- OMA_outer = P3 - P0, P3 averaged over the central 2 UI of a run of seven
  3s, P0 over the central 2 UI of a run of six 0s (121.8.4; the SSPRQ and
  PRBS13Q patterns contain them). A pattern without such runs falls back to
  its longest runs of at least three, reported in ``oma_runs``.
- Two vertical histograms 0.04 UI wide centred at 0.45 and 0.55 UI
  (121.8.5.3). Here the UI origin is placed where the waveform best
  correlates with the transmitted symbols held over their UI (the eye's
  centroid); 802.3 places it from the crossing times, which coincide for
  a symmetric eye. Each histogram is sampled at five positions across its
  width by linear interpolation.
- SER at an instant: every sample contributes the Gaussian probability of
  crossing the thresholds that bound the region it lies in; the larger of
  the two instants' SER must not exceed 4.8e-4 (Q_t = 3.414, the Q that
  gives that SER on an ideal PAM4 eye: SER = 1.5 Q(OMA / (6 sigma))).
- TDECQ = 10 log10(OMA_outer / (6 Q_t sqrt(sigma_G^2 + sigma_S^2))), with
  sigma_G the largest noise at the equaliser *input* that meets the target
  and sigma_S the instrument's own noise (0 in simulation). The equaliser
  passes that noise with gain C_eq, the root of the equaliser's response
  weighted by the noise spectrum at its input (121.8.5.3): white noise
  through a fourth-order Bessel-Thomson filter, reported as 19.34 GHz for
  the 26.5625 GBd clause, i.e. 0.728 x the signalling rate, scaled here
  (``noise_bw_hz``).
- Histograms are binned (4096 bins across the samples' range) and the SER
  is evaluated on bin centres, as an instrument does; the search for sigma
  is a bisection in log sigma.

Pure numpy apart from the Q function (scipy.special, already on the phone
path). Waveform-to-symbol sampling goes through ``core.sampler`` (invariant
#5): fractional instants are reached by shifting the waveform, not by
indexing between samples.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.sampler import baud_samples, hold
from .metrics import qfunc, qfunc_inv

#: 802.3 PAM4 TDECQ target symbol error ratio (121.8.5.3).
TARGET_SER = 4.8e-4
#: The Q at which an ideal PAM4 eye has that SER: SER = 1.5 Q.
Q_T = qfunc_inv(TARGET_SER / 1.5)
#: 3 dB frequency of the delay-normalised fourth-order Bessel polynomial [rad/s].
_BT4_W3DB = 2.113917674904216
#: Histogram centres and width in UI (121.8.5.3).
INSTANTS_UI = (0.45, 0.55)
WINDOW_UI = 0.04
_WINDOW_POINTS = 5
_BINS = 4096
#: C_eq noise filter bandwidth over signalling rate (19.34 GHz at 26.5625 GBd).
NOISE_BW_RATIO = 19.34 / 26.5625
#: Upper bound on the 802.3dj reference DFE coefficient b(1) (lower bound 0).
DFE_MAX = 0.3


def bt4_response(f: np.ndarray, f3db_hz: float) -> np.ndarray:
    """Fourth-order Bessel-Thomson low-pass, unity at DC, -3 dB at ``f3db_hz``.

    H(p) = 105 / (p^4 + 10 p^3 + 45 p^2 + 105 p + 105), p = j w / w_n, the
    delay-normalised Bessel polynomial with w_n chosen so the 3 dB point lands
    where asked."""
    p = 1j * _BT4_W3DB * np.asarray(f, dtype=np.float64) / f3db_hz
    return 105.0 / ((((p + 10.0) * p + 45.0) * p + 105.0) * p + 105.0)


def reference_receiver(power: np.ndarray, dt: float, f3db_hz: float) -> np.ndarray:
    """The waveform as the reference O/E + scope would record it."""
    y = np.asarray(power, dtype=np.float64)
    n = y.size
    pad = int(2 ** np.ceil(np.log2(n + 4096)))
    mean = float(np.mean(y))
    Y = np.fft.rfft(y - mean, pad) * bt4_response(np.fft.rfftfreq(pad, d=dt), f3db_hz)
    return np.fft.irfft(Y, pad)[:n] + mean


@dataclass
class TdecqResult:
    tdecq_db: float
    oma_outer_w: float
    p_ave_w: float
    sigma_g_w: float               # noise at the equaliser input that just meets the target
    ceq: float                     # equaliser noise gain
    taps: np.ndarray
    n_pre: int
    ser_left: float                # at sigma_G, 0.45 UI
    ser_right: float               # at sigma_G, 0.55 UI
    thresholds_w: np.ndarray
    levels_w: np.ndarray           # mean equalised level per symbol at the eye centre
    rlm: float                     # 802.3 R_LM of those levels
    er_db: float                   # 10 log10(P3 / P0)
    oma_runs: tuple = (7, 6)       # run lengths the OMA came from
    dfe_b: float = 0.0             # reference DFE coefficient (0 without the DFE)
    extras: dict = field(default_factory=dict)


def _shifted(y: np.ndarray, shift: float) -> np.ndarray:
    """y(n + shift) by linear interpolation: a waveform on the same grid."""
    if shift == 0.0:
        return y
    n = np.arange(y.size, dtype=np.float64)
    return np.interp(n + shift, n, y)


def _align(y: np.ndarray, osr: int, symbols: np.ndarray, levels: np.ndarray,
           max_delay_ui: int) -> float:
    """Fractional sample delay at which the symbols, each held for one UI,
    line up best with the waveform (parabolic peak of the cross-correlation)."""
    x = hold(levels[symbols], osr)
    n = min(x.size, y.size)
    x = x[:n] - x[:n].mean()
    yy = y[:n] - y[:n].mean()
    m = int(2 ** np.ceil(np.log2(2 * n)))
    r = np.fft.irfft(np.fft.rfft(yy, m) * np.conj(np.fft.rfft(x, m)), m)[: max_delay_ui * osr]
    k = int(np.argmax(r))
    if 0 < k < r.size - 1:
        a, b, c = r[k - 1], r[k], r[k + 1]
        den = a - 2 * b + c
        return k + (0.5 * (a - c) / den if den != 0 else 0.0)
    return float(k)


def _runs(symbols: np.ndarray, value: int, min_len: int) -> list[tuple[int, int]]:
    """(start, length) of every run of ``value`` at least ``min_len`` long."""
    s = np.asarray(symbols)
    is_v = np.concatenate([[False], s == value, [False]])
    edges = np.flatnonzero(np.diff(is_v.astype(np.int8)))
    starts, stops = edges[0::2], edges[1::2]
    return [(int(a), int(b - a)) for a, b in zip(starts, stops) if b - a >= min_len]


def _run_level(y: np.ndarray, osr: int, delay: float, runs, lo: int, hi: int) -> float:
    vals = []
    for start, length in runs:
        c = delay + (start + length / 2.0) * osr        # centre of the run [samples]
        a, b = int(round(c - osr)), int(round(c + osr))  # central 2 UI
        if a >= lo and b <= hi:
            vals.append(y[a:b])
    return float(np.mean(np.concatenate(vals))) if vals else float("nan")


def _outer_levels(y, osr, delay, symbols, lo, hi):
    """(P0, P3, (run of 3s, run of 0s)) per 121.8.4, with a fallback to the
    longest runs available when the pattern lacks 7 threes / 6 zeros."""
    want3, want0 = 7, 6
    r3, r0 = _runs(symbols, 3, want3), _runs(symbols, 0, want0)
    if not r3:
        longest = max((n for _, n in _runs(symbols, 3, 3)), default=0)
        want3, r3 = longest, _runs(symbols, 3, max(longest, 3))
    if not r0:
        longest = max((n for _, n in _runs(symbols, 0, 3)), default=0)
        want0, r0 = longest, _runs(symbols, 0, max(longest, 3))
    p3 = _run_level(y, osr, delay, r3, lo, hi)
    p0 = _run_level(y, osr, delay, r0, lo, hi)
    return p0, p3, (want3, want0)


class _Histogram:
    """Samples binned once; SER under added noise evaluated on bin centres,
    each centre charged to the thresholds that bound its own region."""

    def __init__(self, samples: np.ndarray, thr: np.ndarray):
        lo, hi = float(samples.min()), float(samples.max())
        pad = 1e-9 + 1e-6 * (hi - lo)
        counts, edges = np.histogram(samples, bins=_BINS, range=(lo - pad, hi + pad))
        keep = counts > 0
        self.w = counts[keep] / samples.size
        c = 0.5 * (edges[:-1] + edges[1:])[keep]
        region = np.searchsorted(thr, c)
        self.d_lo = np.where(region > 0, c - thr[np.maximum(region - 1, 0)], np.inf)
        self.d_hi = np.where(region < thr.size, thr[np.minimum(region, thr.size - 1)] - c, np.inf)

    def ser(self, sigma: float) -> float:
        return float(np.dot(self.w, qfunc(self.d_lo / sigma) + qfunc(self.d_hi / sigma)))


def _ser(samples: np.ndarray, thr: np.ndarray, sigma: float) -> float:
    """Region-based SER of a histogram under added Gaussian noise ``sigma``."""
    return _Histogram(samples, thr).ser(sigma)


def _equalise(seqs: np.ndarray, taps: np.ndarray, n_pre: int) -> np.ndarray:
    """T-spaced FFE on baud sequences (rows), cursor at tap ``n_pre``:
    e[k] = sum_i c_i s[k + n_pre - i]."""
    n_taps = taps.size
    out = np.zeros((seqs.shape[0], seqs.shape[1] - n_taps + 1))
    for i, c in enumerate(taps):
        a = n_taps - 1 - i
        out += c * seqs[:, a: a + out.shape[1]]
    # out[:, m] corresponds to symbol m + (n_taps - 1 - n_pre)
    return out


def _sigma_eq(hists, oma, target, guess=None):
    """Largest noise after the equaliser for which max SER <= target
    (bisection in log sigma; ``guess`` narrows the bracket when it holds)."""
    def worst(sig):
        return max(h.ser(sig) for h in hists)

    ref = oma / (6.0 * Q_T)                 # the ideal eye's answer
    if guess is not None and guess > 0.0:
        lo, hi = guess / 1.25, guess * 1.25
        if worst(lo) <= target < worst(hi):
            for _ in range(16):
                mid = np.sqrt(lo * hi)
                lo, hi = (mid, hi) if worst(mid) <= target else (lo, mid)
            return lo
    lo, hi = 1e-3 * ref, 2.0 * ref
    if worst(lo) > target:
        return 0.0
    while worst(hi) <= target:
        hi *= 2.0
    for _ in range(24):
        mid = np.sqrt(lo * hi)
        lo, hi = (mid, hi) if worst(mid) <= target else (lo, mid)
    return lo


def _dfe_start(X, A, target_lv, prev_lv, dfe_max, evaluate, n_pre):
    """Least-squares FFE + DFE at the eye centre: min |X w - b prev - t| with
    sum w - b = 1; a b outside [0, dfe_max] is clipped and the FFE refitted
    with b held there (``A`` is the FFE-only system, reused for that)."""
    n = X.shape[1]
    Xd = np.column_stack([X, -prev_lv])
    g = np.concatenate([np.ones(n), [-1.0]])
    Ad = np.zeros((n + 2, n + 2))
    Ad[:n + 1, :n + 1] = 2.0 * Xd.T @ Xd
    Ad[:n + 1, n + 1] = g
    Ad[n + 1, :n + 1] = g
    theta = np.linalg.solve(Ad, np.concatenate([2.0 * Xd.T @ target_lv, [1.0]]))[:n + 1]
    b = float(min(max(theta[n], 0.0), dfe_max))
    if b == theta[n]:
        taps = theta[:n]
    else:
        rhs = np.concatenate([2.0 * X.T @ (target_lv + b * prev_lv), [1.0 + b]])
        taps = np.linalg.solve(A, rhs)[:n]
    return evaluate(taps, n_pre, b=b)[0], taps, n_pre, b


def tdecq(power: np.ndarray, dt: float, symbol_rate: float, symbols: np.ndarray, *,
          f_ref_hz: float | None = None, n_taps: int = 5, pre_options=(1, 2),
          target_ser: float = TARGET_SER, sigma_scope_w: float = 0.0,
          reference_filter: bool = True, optimise: bool = True,
          noise_bw_hz: float | None = None, skip_ui: int = 64,
          dfe: bool = False, dfe_max: float = DFE_MAX) -> TdecqResult:
    """TDECQ of a PAM4 optical power waveform.

    ``power`` [W] sampled every ``dt``, an integer number of samples per UI,
    carrying ``symbols`` (level indices 0..3, the *line* symbols actually
    sent) from sample 0 on. ``f_ref_hz`` defaults to half the symbol rate;
    ``reference_filter=False`` skips the reference receiver (for waveforms
    already band-limited, and for the closed-form tests). ``noise_bw_hz``
    is the bandwidth of the noise C_eq weighs the equaliser with (default
    0.728 x the signalling rate). ``skip_ui`` UI at each end are left out of
    every statistic (filter and equaliser edges). ``dfe`` adds the 802.3dj
    1-tap reference DFE, its coefficient optimised in [0, ``dfe_max``].
    """
    symbols = np.asarray(symbols, dtype=np.int64)
    if symbols.max() > 3 or symbols.min() < 0:
        raise ValueError("TDECQ is defined for PAM4: symbols must be level indices 0..3")
    if dfe and skip_ui < 1:
        raise ValueError("the DFE needs the symbol before the first scored one: skip_ui >= 1")
    osr = int(round(1.0 / (symbol_rate * dt)))
    if abs(osr * symbol_rate * dt - 1.0) > 1e-6:
        raise ValueError("dt must be an integer fraction of the UI")
    f_ref = 0.5 * symbol_rate if f_ref_hz is None else f_ref_hz
    y = reference_receiver(power, dt, f_ref) if reference_filter else np.asarray(power, float)

    n_sym = min(symbols.size, y.size // osr)
    nominal = np.array([-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0])
    delay = _align(y, osr, symbols[:n_sym], nominal, max_delay_ui=min(64, n_sym // 4))
    lo, hi = skip_ui * osr, y.size - skip_ui * osr
    p_ave = float(np.mean(y[lo:hi]))
    p0, p3, runs_used = _outer_levels(y, osr, delay, symbols[:n_sym], lo, hi)
    oma = p3 - p0
    thr = np.array([p_ave - oma / 3.0, p_ave, p_ave + oma / 3.0])

    # baud sequences at the eye centre and across both histogram windows
    def seq_at(t_ui: float) -> tuple[np.ndarray, int]:
        pos = delay + t_ui * osr
        ip = int(np.floor(pos))
        s = baud_samples(_shifted(y, pos - ip), osr, ip % osr)
        return s, ip // osr                   # s[k + first] belongs to symbol k

    offs = np.linspace(-WINDOW_UI / 2, WINDOW_UI / 2, _WINDOW_POINTS)
    t_list = [0.5] + [t + o for t in INSTANTS_UI for o in offs]
    raw = [seq_at(t) for t in t_list]
    k0 = skip_ui
    k1 = n_sym - skip_ui
    span = k1 - k0
    pad = n_taps
    seqs = np.zeros((len(raw), span + 2 * pad))
    for r, (s, first) in enumerate(raw):
        idx = np.arange(k0 - pad, k1 + pad) + first
        ok = (idx >= 0) & (idx < s.size)
        seqs[r, ok] = s[idx[ok]]
    sym = symbols[k0:k1]
    target_lv = p_ave + nominal[sym] * oma / 2.0
    # what the DFE feeds back: the previous symbol's nominal level, on the
    # FFE input's scale (802.3dj refers the coefficient to OMA there)
    prev_lv = p_ave + nominal[symbols[k0 - 1: k1 - 1]] * oma / 2.0 if dfe else None

    # noise autocorrelation at the equaliser input, at multiples of T
    if reference_filter:
        f_n = NOISE_BW_RATIO * symbol_rate if noise_bw_hz is None else noise_bw_hz
        m = 64 * osr
        h = np.fft.irfft(bt4_response(np.fft.rfftfreq(m, d=dt), f_n), m)
        rho = np.array([np.dot(h[: m - k * osr], h[k * osr:]) for k in range(n_taps)])
        rho = rho / rho[0]
    else:
        rho = np.eye(1, n_taps).ravel()
    R = rho[np.abs(np.subtract.outer(np.arange(n_taps), np.arange(n_taps)))]

    def evaluate(taps, n_pre, guess=None, b=0.0):
        eq = _equalise(seqs, taps, n_pre)
        off = pad - (n_taps - 1 - n_pre)
        eq = eq[:, off: off + span]
        if b:
            eq = eq - b * prev_lv
        hists = [_Histogram(eq[1: 1 + _WINDOW_POINTS].ravel(), thr),
                 _Histogram(eq[1 + _WINDOW_POINTS:].ravel(), thr)]
        s_eq = _sigma_eq(hists, oma, target_ser, guess)
        ceq = float(np.sqrt(taps @ R @ taps))
        s_g = s_eq / ceq
        noise = np.hypot(s_g, sigma_scope_w)
        val = np.inf if s_g <= 0.0 else 10.0 * np.log10(oma / (6.0 * Q_T * noise))
        return val, s_g, ceq, eq, hists

    # the main-tap position is chosen on the least-squares start, and only
    # that one is refined: the refinement moves TDECQ by tenths of a dB, the
    # choice of cursor rarely changes under it, and refining every option
    # tripled the cost of a 15-tap measurement
    starts = []
    for n_pre in pre_options:
        if not 0 <= n_pre < n_taps:
            continue
        # constrained least squares at the eye centre: min |X c - t|, sum c = 1
        X = np.stack([seqs[0, pad + n_pre - i: pad + n_pre - i + span] for i in range(n_taps)],
                     axis=1)
        A = np.zeros((n_taps + 1, n_taps + 1))
        A[:n_taps, :n_taps] = 2.0 * X.T @ X
        A[:n_taps, n_taps] = 1.0
        A[n_taps, :n_taps] = 1.0
        b = np.concatenate([2.0 * X.T @ target_lv, [1.0]])
        taps = np.linalg.solve(A, b)[:n_taps]
        starts.append((evaluate(taps, n_pre)[0], taps, n_pre, 0.0))
        if dfe:
            starts.append(_dfe_start(X, A, target_lv, prev_lv, dfe_max, evaluate, n_pre))
    val, taps, n_pre, b_dfe = min(starts, key=lambda t: t[0])
    if optimise and np.isfinite(val):
        sig = evaluate(taps, n_pre, b=b_dfe)[1] * float(np.sqrt(taps @ R @ taps))
        step = 0.02
        while step > 2e-3:
            improved = False
            for i in range(n_taps):
                if i == n_pre:
                    continue
                for sgn in (1.0, -1.0):
                    trial = taps.copy()
                    trial[i] += sgn * step
                    trial[n_pre] -= sgn * step      # keep sum = 1 (+ b)
                    v, s_g, c_eq = evaluate(trial, n_pre, sig, b_dfe)[:3]
                    if v < val - 1e-6:
                        taps, val, improved, sig = trial, v, True, s_g * c_eq
            if dfe:
                for sgn in (1.0, -1.0):
                    bt = min(max(b_dfe + sgn * step, 0.0), dfe_max)
                    if bt == b_dfe:
                        continue
                    trial = taps.copy()
                    trial[n_pre] += bt - b_dfe      # keep sum - b = 1
                    v, s_g, c_eq = evaluate(trial, n_pre, sig, bt)[:3]
                    if v < val - 1e-6:
                        taps, b_dfe, val, improved, sig = trial, bt, v, True, s_g * c_eq
            if not improved:
                step /= 2.0
    best = (val, taps, n_pre)

    val, taps, n_pre = best
    val, s_g, ceq, eq, hists = evaluate(taps, n_pre, b=b_dfe)
    s_eq = s_g * ceq
    ser_l = hists[0].ser(s_eq) if s_eq > 0 else 1.0
    ser_r = hists[1].ser(s_eq) if s_eq > 0 else 1.0
    centre = eq[0]
    levels = np.array([centre[sym == k].mean() if np.any(sym == k) else np.nan
                       for k in range(4)])
    from ..optical.eo import rlm as _rlm

    return TdecqResult(
        tdecq_db=float(val), oma_outer_w=float(oma), p_ave_w=p_ave, sigma_g_w=float(s_g),
        ceq=ceq, taps=np.asarray(taps), n_pre=int(n_pre), ser_left=ser_l, ser_right=ser_r,
        thresholds_w=thr, levels_w=levels, rlm=float(_rlm(levels)),
        er_db=float(10.0 * np.log10(p3 / p0)) if p0 > 0 else float("inf"),
        oma_runs=runs_used, dfe_b=float(b_dfe), extras={"delay_samples": delay, "osr": osr, "f_ref_hz": f_ref})
