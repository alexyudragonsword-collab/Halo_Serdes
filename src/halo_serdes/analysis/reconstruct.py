"""Reconstruct intermediate-node waveforms/eyes for visualization.

The engines return a single folded eye (mixed-signal post-EQ node) and scalar
metrics; scopes at other observation points require rebuilding the LTI front
end. This centralizes that reconstruction so the example scripts and the GUI
share one implementation instead of each inlining it.

The front end mirrors the time engine's first half exactly (Tx FIR + jittered
edges -> channel [+ CTLE] -> VGA), so the reconstructed analog eye matches
what the engine sees before the slicer loop.
"""

from __future__ import annotations

import numpy as np

from ..afe import Ctle
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.waveform import Waveform
from ..engine.lti import fft_filter
from ..engine.static_link import fold_eye, make_pattern
from ..tx.builder import symbols_to_voltages, tx_fir
from ..tx.jitter import build_jittered_tx


def front_end_impulse(cfg: LinkConfig, channel: ChannelModel,
                      include_ctle: bool = True) -> np.ndarray:
    """Folded channel [+ CTLE] + VGA impulse response (V/sample at cfg.dt)."""
    h = channel.response_set(cfg.dt).h.y
    if include_ctle and cfg.rx.ctle.enable:
        ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
        nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
        f = np.fft.rfftfreq(nfft, d=cfg.dt)
        h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    return h * cfg.rx.vga_gain


def front_end_waveform(cfg: LinkConfig, channel: ChannelModel | None = None,
                       include_ctle: bool = True, with_noise: bool = False,
                       seed: int | None = None) -> tuple[Waveform, int]:
    """Rebuild the analog front-end waveform at the slicer input.

    Returns ``(waveform, sample_phase)`` where ``sample_phase`` is the
    oversample-grid phase of the pulse peak (the natural eye alignment).
    """
    if channel is None:
        channel = ChannelModel.from_config(cfg)
    rng = np.random.default_rng(cfg.sim.seed if seed is None else seed)
    symbols = make_pattern(cfg)
    v = symbols_to_voltages(symbols, cfg)
    if len(cfg.tx.fir_taps) > 1:
        v = tx_fir(v, cfg.tx.fir_taps, cfg.tx.fir_n_pre)
    tx_wave, _ = build_jittered_tx(v, cfg, rng)

    h = front_end_impulse(cfg, channel, include_ctle=include_ctle)
    rx_y = fft_filter(tx_wave.y, h)
    if with_noise and cfg.rx.noise_rms > 0:
        rx_y = rx_y + rng.normal(scale=cfg.rx.noise_rms, size=rx_y.size)

    pulse = pulse_from_impulse(Waveform(h, cfg.dt), cfg.osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    return Waveform(rx_y, cfg.dt), peak % cfg.osr


def front_end_eye(cfg: LinkConfig, channel: ChannelModel | None = None,
                  include_ctle: bool = True, n_traces: int = 2000,
                  with_noise: bool = True) -> np.ndarray:
    """Folded eye traces (n_traces, 2*osr) at the analog front-end node."""
    wave, phase = front_end_waveform(cfg, channel, include_ctle=include_ctle,
                                     with_noise=with_noise)
    return fold_eye(wave.y, cfg.osr, phase, n_traces=n_traces)


def post_ffe_eye(cfg: LinkConfig, ffe_taps: np.ndarray,
                 channel: ChannelModel | None = None, n_traces: int = 2000,
                 with_noise: bool = True) -> np.ndarray:
    """Reconstructed post-FFE eye for the ADC/DSP architecture.

    The digital FFE runs at baud rate (1 sample/UI), so its output carries no
    continuous waveform to fold into an eye. This upsamples the converged
    baud-rate taps onto the oversampled grid and convolves them with the analog
    front-end (ADC-input) waveform, giving the continuous-time *equivalent* of
    the FFE output — what the equalized signal looks like between samples. It is
    a visualization reconstruction (the real datapath only evaluates it at baud
    instants); the DFE, being per-symbol nonlinear feedback, has no analogous
    continuous view. Mirrors ``examples/16_adc_eyes.py``; shared with the GUI.
    """
    wave, phase = front_end_waveform(cfg, channel, include_ctle=True,
                                     with_noise=with_noise)
    w = np.asarray(ffe_taps, dtype=float)
    if w.size == 0:
        return fold_eye(wave.y, cfg.osr, phase, n_traces=n_traces)
    osr = cfg.osr
    tap_pre = cfg.rx.ffe.n_pre
    w_up = np.zeros((w.size - 1) * osr + 1)
    w_up[::osr] = w
    post = np.convolve(wave.y, w_up)[tap_pre * osr: tap_pre * osr + wave.y.size]
    return fold_eye(post, osr, phase, n_traces=n_traces)
