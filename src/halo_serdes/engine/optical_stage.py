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
from ..core.sampler import baud_samples, hold, upsampled_taps
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


def split_impulses_nonlinear(cfg: LinkConfig, channel: ChannelModel
                             ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(drive, optics, receiver) impulses around a large-signal E/O curve:
    up to the curve, from it to the photodiode, and on to the slicer. Their
    convolution is the small-signal chain the receiver's cursors come from;
    the waveform passes the curve between the first two."""
    opt = channel.optical
    hd = opt.drive.response_set(cfg.dt).h.y
    ho = opt.optics.response_set(cfg.dt).h.y
    h2 = apply_front_end(cfg, opt.post_pd.response_set(cfg.dt).h.y)
    return hd, ho, h2


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


def transmitter_power(cfg: LinkConfig, *, through_fibre: bool = False, include_seg_a: bool = True,
                      include_rin: bool = True, symbols: np.ndarray | None = None,
                      rng: np.random.Generator | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Optical power [W] leaving the E/O (TP2), or after the fibre, and the
    line symbols it carries -- what a TDECQ measurement sees.

    The host Tx waveform is built as the time engine builds it (pattern,
    precoding, FIR, edge jitter), passes segment A (``include_seg_a``; an LPO
    module's light depends on the host trace), the E/O response, the
    large-signal curve if one is configured (the same Wiener order as the
    link, ``channel/model.py``) and optionally the fibre. Only the
    laser's RIN is added: shot and TIA noise belong to a receiver, not to the
    transmitter being measured. RIN is white per sample at the instantaneous
    power, sigma^2 = RIN P^2 f_s / 2, the convention of ``optical/noise.py``;
    the reference receiver then sets its bandwidth.
    """
    from ..optical import static_curve
    from ..tx.pipeline import TxPipeline
    from .lti import fft_filter
    from .static_link import check_symbols, make_pattern
    from .timedomain import _tx_symbols

    top = cfg.topology
    if top is None:
        raise ValueError("transmitter_power needs an optical topology (cfg.topology)")
    opt = top.optical
    rng = np.random.default_rng(cfg.sim.seed) if rng is None else rng
    user = make_pattern(cfg) if symbols is None else check_symbols(cfg, symbols)
    line = _tx_symbols(cfg, user)
    tx_pipe = TxPipeline.from_config(cfg)
    tx_wave = tx_pipe.waveform(tx_pipe.symbol_stage(line), rng)

    seg_a = ChannelModel.from_channel_config(top.seg_a, cfg.symbol_rate, "topology.seg_a")
    eo = ChannelModel.from_optical(opt, seg_a.f, "eo")
    fib = ChannelModel.from_optical(opt, seg_a.f, "fiber")
    # without segment A the drive is the Tx waveform through the matched
    # divider alone: a scale, not a filter (an ideal flat segment on this
    # grid would be a brick wall with a wrapping sinc impulse)
    gain_a = abs(seg_a.H[0]) if include_seg_a else 0.5
    amp = cfg.tx.swing / 2.0 * gain_a * float(np.sum(cfg.tx.fir_taps))

    def impulse(*models):
        return ChannelModel.cascade(*models).band_limited().response_set(cfg.dt).h.y

    y = (fft_filter(tx_wave.y, impulse(seg_a, eo)) if include_seg_a
         else fft_filter(0.5 * tx_wave.y, impulse(eo)))
    curve = static_curve(opt)
    if curve is not None:
        y = curve.apply(y, amp)
    if through_fibre:
        y = fft_filter(y, impulse(fib))
    p_mid = 0.5 * (opt.p_low_w + opt.p_high_w)
    power = np.maximum(p_mid + y / amp * opt.oma_w / 2.0, 0.0)
    if include_rin:
        rin = 10.0 ** (opt.rin_db_hz / 10.0)
        power = power + rng.normal(size=power.size) * power * np.sqrt(rin * 0.5 / cfg.dt)
    return power, line


def slicer_sigma_binned(cfg: LinkConfig, channel: ChannelModel, h1: np.ndarray,
                        h2: np.ndarray, ffe_taps: np.ndarray | None = None,
                        neighbours: tuple[int, ...] = (-1, 1)) -> np.ndarray:
    """Optical noise sigma at the slicer [V] per level *and* per pattern of
    the symbols at ``neighbours`` (cursor offsets: -1 the next symbol sent,
    +1 the previous one).

    ``slicer_sigma_per_level`` averages the noise variance over every
    neighbour, so each level gets one Gaussian; but which neighbours were
    sent moves the light along the filter's memory, so the true per-level
    distribution is a scale mixture of Gaussians, wider in the tails than its
    matched-variance stand-in (0.55-0.63x optimistic at ER 6 dB). Here the
    two nearest neighbours are fixed per bin -- their pre-PD pulses enter the
    mean power, only the others stay random -- which leaves the per-bin
    distribution close to Gaussian.

    Returns shape ``(M,) + (M,) * len(neighbours)``: level, then each
    neighbour's level, all ascending.
    """
    noise = channel.optical.noise
    osr = cfg.osr
    g = h2
    if ffe_taps is not None and len(ffe_taps) > 1:
        g = np.convolve(g, upsampled_taps(ffe_taps, osr))
    g2 = g * g

    p1 = np.convolve(h1, np.ones(osr))
    p_full = np.convolve(p1, g)
    t_s = int(np.argmax(np.abs(p_full)))
    base = t_s - np.arange(g.size)

    def p1_at(shift: int) -> np.ndarray:
        # symbol at cursor offset j was sent j UI earlier: its pre-PD pulse,
        # seen at the PD-node sample feeding output delay n, is p1[t_s - n + j T]
        idx = base + shift * osr
        out = np.zeros(g.size)
        ok = (idx >= 0) & (idx < p1.size)
        out[ok] = p1[idx[ok]]
        return out

    own = p1_at(0)
    fixed = [p1_at(j) for j in neighbours]
    fold = np.array([float(np.sum(baud_samples(p1, osr, r) ** 2)) for r in range(osr)])
    rest = np.zeros(g.size)
    ok = (base >= 0) & (base < p1.size)
    rest[ok] = fold[base[ok] % osr]
    rest = np.maximum(rest - own ** 2 - sum(f ** 2 for f in fixed), 0.0)

    levels_v = _levels(cfg)
    m = levels_v.size
    a2 = float(np.mean(levels_v ** 2))
    scale = noise.volts_per_amp * noise.responsivity_a_w
    p_var = a2 * rest / scale ** 2
    out = np.empty((m,) * (1 + len(neighbours)))
    for combo in np.ndindex(*out.shape):
        lin = levels_v[combo[0]] * own
        for f, i in zip(fixed, combo[1:]):
            lin = lin + levels_v[i] * f
        p_mean = noise.p_mid_w + lin / scale
        out[combo] = np.sqrt(np.sum(g2 * noise.sample_var_v2(np.maximum(p_mean, 0.0), p_var)))
    return out


#: symbols the curve's pattern average runs over: three periods of PRBS13(Q)
#: (2^13 - 1), so every three-symbol pattern appears 128+ times for PAM4
_CURVE_PATTERN_SYMBOLS = 3 * 8192


def curve_pattern_offsets(cfg: LinkConfig, channel: ChannelModel,
                          ffe_taps: np.ndarray | None = None,
                          neighbours: tuple[int, ...] = (-1, 1)
                          ) -> tuple[np.ndarray, np.ndarray]:
    """What the large-signal E/O curve does at the slicer beyond moving the
    levels: per sampling phase, transmitted level and pattern of the binned
    neighbours, the mean and the variance of (sample through the curve) -
    (the statistical engine's linear sample).

    The linear model sends the curve's image of each level through the
    small-signal chain, so it has the curve's steady-state levels but not how
    the curve bends the waveform between them. The curve sits after the E/O
    dynamics (Wiener order), so its input already carries the neighbours'
    ISI and the bend depends on them. Measured at c = 0.4 on VCSEL / EML ADC
    and VCSEL mixed-signal links, the symbol and its two nearest neighbours
    -- the bins the optical noise already uses (``slicer_sigma_binned``) --
    explain 92-96 % of the difference's variance. A per-bin shift plus the
    remainder as variance is therefore enough; without it the statistical
    engine read 0.30-0.40x of the time engine at c = 0.5 on the EML and NRZ
    links.

    The average runs noiselessly over the configured pattern, the symbols the
    time engine sends. It uses the same drive, optics and receiver impulses and
    the same curve call, so it is an exact average over that pattern, not a
    Monte-Carlo BER. The Tx's LTI part
    (``TxPipeline.equivalent_symbol_response``) shapes the drive as it shapes
    the statistical engine's pulse.

    Returns ``(mu, var)`` [V, V^2], each of shape ``(osr, M) + (M,) *
    len(neighbours)``: phase index as the statistical engine's (``i - osr //
    2`` samples from the pulse peak), then the level and each neighbour's
    level, ascending.
    """
    import dataclasses

    from ..core.mapping import nrz_levels, pam4_levels
    from ..tx.pipeline import TxPipeline
    from .lti import fft_filter
    from .static_link import make_pattern
    from .timedomain import _tx_symbols

    osr = cfg.osr
    opt = channel.optical
    hd, ho, h2 = split_impulses_nonlinear(cfg, channel)
    g = h2
    if ffe_taps is not None and len(ffe_taps) > 1:
        g = np.convolve(g, upsampled_taps(ffe_taps, osr))
    tx_resp = TxPipeline.from_config(cfg).equivalent_symbol_response(osr)

    n = _CURVE_PATTERN_SYMBOLS
    short = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, n_symbols=n))
    sym = np.asarray(_tx_symbols(cfg, make_pattern(short)), dtype=np.int64)
    n = sym.size
    lv_drive = (pam4_levels(cfg.tx.swing, cfg.tx.rlm) if cfg.modulation == "pam4"
                else nrz_levels(cfg.tx.swing))
    lv_model = _levels(cfg)                  # the curve's image: the engine's levels

    def drive(levels):
        x = hold(levels[sym], osr)
        if tx_resp is not None:
            x = fft_filter(x, tx_resp)
        return fft_filter(x, hd)

    y_curve = fft_filter(fft_filter(opt.curve.apply(drive(lv_drive), opt.drive_amplitude), ho), g)
    err = y_curve - fft_filter(fft_filter(drive(lv_model), ho), g)
    one = np.ones(osr) if tx_resp is None else np.convolve(np.ones(osr), tx_resp)
    pulse = np.convolve(np.convolve(np.convolve(one, hd), ho), g)
    peak = int(np.argmax(np.abs(pulse)))

    m_lv = lv_drive.size
    # skip the start-up (zero history) and the end; neighbour -j is the symbol
    # sent j later, +j the one sent j earlier (statistical._BINNED_NEIGHBOURS)
    edge = pulse.size // osr + 2 + max(abs(j) for j in neighbours)
    idx = np.arange(edge, n - edge)
    key = sym[idx]
    for j in neighbours:
        key = key * m_lv + sym[idx - j]
    n_bins = m_lv ** (1 + len(neighbours))
    mu = np.zeros((osr, n_bins))
    var = np.zeros((osr, n_bins))
    for i in range(osr):
        # symbol m's sample at this phase is UI m + base, offset ph within it
        base, ph = divmod(peak + i - osr // 2, osr)
        per_ui = baud_samples(err, osr, ph)
        ok = idx + base < per_ui.size
        e = per_ui[idx[ok] + base]
        k = key[ok]
        count = np.maximum(np.bincount(k, minlength=n_bins), 1)
        s1 = np.bincount(k, weights=e, minlength=n_bins)
        s2 = np.bincount(k, weights=e * e, minlength=n_bins)
        mu[i] = s1 / count
        var[i] = np.maximum(s2 / count - mu[i] ** 2, 0.0)
    shape = (osr,) + (m_lv,) * (1 + len(neighbours))
    return mu.reshape(shape), var.reshape(shape)
