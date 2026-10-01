"""The cut both engines make through an optical topology.

With a topology the channel is two LTI stages around one noise node: the
photodiode current. The time engine convolves stage 1, injects the
level-dependent noise, convolves stage 2; the statistical engine needs the
same stage-2 impulse to turn that per-sample noise into a per-level slicer
sigma. Keeping the split in one place is what makes invariant #3 testable:
if the two engines disagreed on where the noise enters, the 2x check would
measure the disagreement, not the model.

The impulses are built here from the two stages and the full response is
their convolution, rather than the cascade's own impulse: trimming each
impulse drops its leading delay, so only a response assembled from the same
trimmed pieces lines up sample for sample with the two-stage waveform.
"""

from __future__ import annotations

import numpy as np

from ..afe import Ctle
from ..channel import ChannelModel
from ..config.schema import LinkConfig
from ..core.sampler import baud_samples, upsampled_taps
from .static_link import _levels


def apply_front_end(cfg: LinkConfig, h: np.ndarray) -> np.ndarray:
    """CTLE (if enabled) and VGA on an impulse -- the engines' front-end step."""
    if cfg.rx.ctle.enable:
        ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
        nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
        f = np.fft.rfftfreq(nfft, d=cfg.dt)
        h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    return h * cfg.rx.vga_gain


def split_impulses(cfg: LinkConfig, channel: ChannelModel) -> tuple[np.ndarray, np.ndarray]:
    """(stage 1, stage 2) impulses on the ``cfg.dt`` grid: up to the photodiode
    node, and from it through the O/E, segment B and the receiver front end."""
    opt = channel.optical
    h1 = opt.pre_pd.response_set(cfg.dt).h.y
    h2 = apply_front_end(cfg, opt.post_pd.response_set(cfg.dt).h.y)
    return h1, h2


def slicer_sigma_per_level(cfg: LinkConfig, channel: ChannelModel, h1: np.ndarray,
                           h2: np.ndarray, ffe_taps: np.ndarray | None = None,
                           nominal: bool = False) -> np.ndarray:
    """Per-level optical noise sigma at the slicer [V].

    The photodiode noise is white per sample with a variance set by the
    optical power *at that sample*. The slicer sample is sum_n g[n] nu[t_s - n]
    over the stage-2 impulse ``g`` (O/E, segment B, CTLE, VGA and the
    symbol-spaced FFE upsampled onto the grid), so its variance given that
    symbol k was sent is

        sigma_k^2 = sum_n g[n]^2 * E[ s^2(P(t_s - n)) | a_0 = a_k ]

    and the power along the filter's memory is not P_k: it is the pre-PD
    pulse of symbol k, p1, riding on the random neighbours. With symmetric
    levels, E[P | k] = P_mid + a_k p1 / (R v), var(P | k) = E[a^2] *
    sum_{m != 0} p1(. - mT)^2 / (R v)^2; shot noise needs the mean, RIN also
    the variance (``OpticalNoise.sample_var_v2``). ``nominal=True`` is the
    memoryless picture, every sample at P_k -- pessimistic by up to 2x at
    ER 6 dB on a 40 GHz TIA + CTLE + FFE chain, because Q() is convex and
    the chain averages the variances before the slicer sees them.
    """
    noise = channel.optical.noise
    osr = cfg.osr
    g = h2
    if ffe_taps is not None and len(ffe_taps) > 1:
        g = np.convolve(g, upsampled_taps(ffe_taps, osr))
    g2 = g * g
    if nominal or g2.sum() == 0.0:
        return np.sqrt(noise.sample_var_v2(noise.level_powers_w) * g2.sum())

    # pre-PD pulse of one symbol (volts per unit level) and the slicer instant
    p1 = np.convolve(h1, np.ones(osr))
    p_full = np.convolve(p1, g)
    t_s = int(np.argmax(np.abs(p_full)))
    # PD-node sample feeding output delay n is t_s - n; take p1 there (0 outside)
    idx = t_s - np.arange(g.size)
    ok = (idx >= 0) & (idx < p1.size)
    p1_n = np.zeros(g.size)
    p1_n[ok] = p1[idx[ok]]
    # ISI power at each PD-node sample: all symbol shifts minus the symbol's own
    fold = np.array([float(np.sum(baud_samples(p1, osr, r) ** 2)) for r in range(osr)])
    isi_n = np.zeros(g.size)
    isi_n[ok] = fold[idx[ok] % osr] - p1_n[ok] ** 2
    isi_n = np.maximum(isi_n, 0.0)

    levels_v = _levels(cfg)                         # symmetric, ascending
    a2 = float(np.mean(levels_v ** 2))
    scale = noise.volts_per_amp * noise.responsivity_a_w   # V per W at the PD node
    p_var = a2 * isi_n / scale ** 2
    out = np.empty(levels_v.size)
    for k, a_k in enumerate(levels_v):
        p_mean = noise.p_mid_w + a_k * p1_n / scale
        out[k] = np.sqrt(np.sum(g2 * noise.sample_var_v2(np.maximum(p_mean, 0.0), p_var)))
    return out
