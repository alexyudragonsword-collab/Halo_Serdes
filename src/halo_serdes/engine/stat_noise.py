"""The statistical engine's noise sources other than the receiver's AWGN.

The Gaussian kernel, the ADC's quantisation noise (``adc_noise_sigma``), the
transmitter's own noise through the chain (``tx_noise_covariance``, also the
static and time engines' FFE solve), its white edge jitter as noise that
follows the transitions (``tx_edge_jitter``) and a DAC's error at the slicer.
Part of ``engine.statistical`` (split out 2026-10-30; the names are
importable from there as before).
"""

from __future__ import annotations

import dataclasses

from dataclasses import dataclass

import numpy as np

from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.mapping import level_probabilities
from ..core.waveform import Waveform
from .stat_pdf import _BINNED_NEIGHBOURS


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
    #: duty-cycle distortion (``tx.clock.dcd_ui``): rising edges come late and
    #: falling ones early by half of it. ``phi`` is then a rising edge's,
    #: ``phi_fall`` a falling one's; ``shift`` the mean the other edges' DCD
    #: adds at the sample per bin (linearised) -- None / zeros without it
    phi_fall: np.ndarray | None = None
    shift: np.ndarray | None = None

    def kernel(self, combo: tuple, dv: float, scale: float = 1.0) -> tuple[np.ndarray, int]:
        """PDF on a ``dv`` grid of the two own edges' contribution for one bin,
        voltages divided by ``scale``: ``(pdf, lo)`` with ``pdf[0]`` at bin ``lo``."""
        out, lo = np.ones(1), 0
        for e in range(self.phi.shape[0]):
            st = self.step[combo + (e,)]
            ph = self.phi[e] if self.phi_fall is None or st >= 0.0 else self.phi_fall[e]
            x = st * ph / (scale * dv)
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
        if self.shift is not None and self.shift[combo] != 0.0:
            pos = float(self.shift[combo]) / (scale * dv)
            k_lo = int(np.floor(pos))
            frac = pos - k_lo
            out, lo = np.convolve(out, np.array([1.0 - frac, frac])), lo + k_lo
        return out, lo


def tx_edge_jitter(cfg: LinkConfig, tx_pipe, h: np.ndarray, osr: int, offset: int = 0,
                   neighbours: tuple[int, ...] = _BINNED_NEIGHBOURS,
                   n_pre: int = 24, n_post: int = 64, n_sigma: float = 8.0,
                   n_eps: int = 321) -> TxEdgeJitter | None:
    """Slicer noise of the transmitter's white edge jitter (``tx.clock.rj_ui``)
    and duty-cycle distortion (``dcd_ui``: rising edges late, falling ones
    early by half of it, as ``tx/jitter.py`` places them)
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
    dcd = cfg.tx.clock.dcd_ui
    if rj <= 0.0 and dcd <= 0.0:
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
    lv = np.asarray(tx_pipe.levels(np.arange(cfg.n_levels)), dtype=np.float64)
    pw = level_probabilities(cfg.modulation)
    mu = float(np.sum(pw * lv))
    var_a = float(np.sum(pw * (lv - mu) ** 2))
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
    shift = None
    if dcd > 0.0:
        # every other edge's DCD, linearised: edge m moves by +-dcd/2 UI with
        # its step's sign, so it adds -|step_m| h_m dcd osr / 2 at the sample
        # -- a mean (the eye's DC moves) and the spread of |step| over the
        # data the bin leaves free (exact over the three largest free
        # symbols, the rest Gaussian); edges taken independent
        half = dcd * osr / 2.0
        hbar = 0.5 * (h_at(idx) + h_at(idx + 1))
        far = np.flatnonzero(~exact & (np.abs(hbar) > 0.0))
        from scipy.special import erf

        free_d = []                                      # per far edge: (free coefficients)
        for i_e in far:
            dj = d_at(ms[i_e] + np.arange(-span, span + 1))
            js = np.arange(-span, span + 1)
            free = np.array([dj[t] for t in range(js.size) if js[t] not in offs])
            free = free[np.abs(free) > 1e-12]
            free_d.append(free[np.argsort(-np.abs(free))])
        shift = np.zeros(shape)
        for combo in np.ndindex(*shape):
            mean_step = coef @ (lv[list(combo)] - mu)
            m_abs = 0.0
            v_abs = 0.0
            for i_e, free in zip(far, free_d):
                big, small = free[:3], free[3:]
                sg = float(np.sqrt(var_a * np.sum(small ** 2)))
                xs = np.zeros(1) + mean_step[i_e]
                wx = np.ones(1)
                for c in big:                              # enumerate the largest free symbols
                    xs = (xs[:, None] + c * (lv - mu)[None, :]).ravel()
                    wx = (wx[:, None] * pw[None, :]).ravel()
                if sg > 0.0:
                    e1 = (sg * np.sqrt(2.0 / np.pi) * np.exp(-0.5 * (xs / sg) ** 2)
                          + xs * erf(xs / (np.sqrt(2.0) * sg)))
                    e2 = xs ** 2 + sg ** 2
                else:
                    e1, e2 = np.abs(xs), xs ** 2
                ea, ea2 = float(np.sum(wx * e1)), float(np.sum(wx * e2))
                m_abs += hbar[i_e] * ea
                v_abs += hbar[i_e] ** 2 * max(ea2 - ea * ea, 0.0)
            shift[combo] = -half * m_abs
            var_all[combo] += half ** 2 * v_abs
            var_rest[combo] += half ** 2 * v_abs
    # the two own edges, exactly: the step response at the sample, displaced
    # (by RJ on a Gaussian grid, and by DCD with the edge's sign)
    s = np.cumsum(h)
    if rj > 0.0:
        sig = rj * osr                                    # displacement sigma [samples]
        eps = np.linspace(-n_sigma, n_sigma, n_eps) * sig
        w = np.exp(-0.5 * (eps / sig) ** 2)
        w /= w.sum()
    else:
        eps, w = np.zeros(1), np.ones(1)
    grid = np.arange(s.size)

    def own(delay: float) -> np.ndarray:
        return np.stack([np.interp(pk - m * osr - eps - delay, grid, s, left=0.0, right=s[-1])
                         - np.interp(pk - m * osr, grid, s, left=0.0, right=s[-1])
                         for m in _EXACT_EDGES])

    if dcd > 0.0:
        phi, phi_fall = own(dcd * osr / 2.0), own(-dcd * osr / 2.0)
    else:
        phi, phi_fall = own(0.0), None
    return TxEdgeJitter(sigma=np.sqrt(var_all), rest=np.sqrt(var_rest), step=step, phi=phi,
                        weight=w, phi_fall=phi_fall, shift=shift)


def dcd_absorbed(jit: TxEdgeJitter | None, levels: np.ndarray, probs: np.ndarray,
                 per_level: bool, prev_axis: int | None) -> TxEdgeJitter | None:
    """``jit`` with the part of DCD's deterministic offset an adapting
    receiver takes up: each level's mean (``per_level``: its levels, and so
    its thresholds, follow the sampled amplitude) and the part linear in the
    previous symbol (``prev_axis``: the bin axis that holds it, a DFE's first
    tap adapting). The offset is the other edges' mean (``shift``) and the
    two own edges' over the RJ grid. Unchanged without DCD."""
    if jit is None or jit.shift is None or (not per_level and prev_axis is None):
        return jit
    shape = jit.shift.shape
    own = np.zeros(shape)
    for combo in np.ndindex(*shape):
        for e in range(jit.phi.shape[0]):
            st = float(jit.step[combo + (e,)])
            ph = jit.phi[e] if jit.phi_fall is None or st >= 0.0 else jit.phi_fall[e]
            own[combo] += st * float(np.sum(jit.weight * ph))
    d = jit.shift + own
    lv = np.asarray(levels, dtype=np.float64)
    pw = np.asarray(probs, dtype=np.float64)
    w = np.ones(shape)
    for ax in range(len(shape)):
        w = w * pw.reshape([-1 if i == ax else 1 for i in range(len(shape))])
    adj = np.zeros(shape)
    if per_level:
        m = np.sum(w * d, axis=tuple(range(1, len(shape))), keepdims=True) / np.sum(
            w, axis=tuple(range(1, len(shape))), keepdims=True)
        adj -= np.broadcast_to(m, shape)
    if prev_axis is not None:
        x = lv - float(np.sum(pw * lv))
        xb = x.reshape([-1 if i == prev_axis else 1 for i in range(len(shape))])
        r = d + adj
        alpha = float(np.sum(w * r * xb) / np.sum(w * xb * xb))
        adj -= alpha * np.broadcast_to(xb, shape)
    return dataclasses.replace(jit, shift=jit.shift + adj)


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
