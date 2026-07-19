"""Backchannel Tx FIR training (802.3 KR link-training style).

Product mixed-signal receivers carry no FFE: the linear-equalization split is
Tx FIR (2-4 taps) + RX CTLE + RX DFE, and the Tx taps are trained by the
*receiver* sending coefficient increment/decrement requests over a
backchannel (802.3 clause 72 / PCIe EQ phases). This module models that
protocol behaviorally:

- the RX measures the pre/post cursor ratios of the received pulse response
  (channel + CTLE) at its sampling point;
- each round it issues sign-only inc/dec requests in quantized steps for the
  Tx taps it wants changed (c(-1), c(+1), ...);
- the TX applies the requests under a peak-power constraint
  (sum|taps| = 1, main tap absorbs the change) and the loop repeats until
  the trained cursors fall below threshold or the round budget runs out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..afe import Ctle
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.waveform import Waveform
from ..dsp import channel_cursors


@dataclass
class BackchannelResult:
    taps: np.ndarray               # trained Tx FIR [pre..., main, post...]
    n_pre: int
    converged: bool
    rounds: int
    cursor_history: np.ndarray     # (rounds, n_pre+1+n_post) cursor/main ratios
    tap_history: np.ndarray        # (rounds, n_taps)

    def summary(self) -> str:
        tag = "converged" if self.converged else "round budget exhausted"
        return (f"Tx FIR trained in {self.rounds} rounds ({tag}): "
                f"{np.round(self.taps, 4).tolist()}")


def _rx_pulse_base(cfg: LinkConfig, channel: ChannelModel) -> np.ndarray:
    """Channel (+CTLE) impulse response — what the RX 'sees' before Tx FIR."""
    h = channel.response_set(cfg.dt).h.y
    if cfg.rx.ctle.enable:
        ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
        nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
        f = np.fft.rfftfreq(nfft, d=cfg.dt)
        h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    return h * cfg.rx.vga_gain


def train_tx_fir(cfg: LinkConfig, channel: ChannelModel | None = None,
                 n_pre: int = 1, n_post: int = 1, step: float = 1.0 / 64.0,
                 threshold: float = 0.01, max_rounds: int = 64,
                 dfe_covered: int = 0) -> BackchannelResult:
    """Run the KR-style training loop; returns the trained Tx FIR.

    Args:
        n_pre/n_post: trainable Tx taps around the main cursor (1/1 = the
            classic 3-tap product FIR).
        step: quantized coefficient step per request (KR uses fixed hardware
            steps; sign-only information crosses the backchannel).
        threshold: |cursor|/main below which the RX sends "hold".
        dfe_covered: postcursors 1..dfe_covered are handled by the RX DFE, so
            the RX sends "hold" for them regardless of their size — spending
            Tx swing (peak-power constrained!) on ISI the DFE cancels for
            free only shrinks the main cursor. A real RX trains toward its
            own post-DFE metric; pass ``cfg.rx.dfe.n_taps`` here.
    """
    if channel is None:
        channel = ChannelModel.from_config(cfg)
    osr = cfg.osr
    h_base = _rx_pulse_base(cfg, channel)

    n_taps = n_pre + 1 + n_post
    taps = np.zeros(n_taps)
    taps[n_pre] = 1.0

    cur_hist: list[np.ndarray] = []
    tap_hist: list[np.ndarray] = []
    converged = False
    rounds = 0

    for rounds in range(1, max_rounds + 1):
        # RX-side measurement: pulse response with the current Tx FIR
        fir_up = np.zeros((n_taps - 1) * osr + 1)
        fir_up[::osr] = taps
        h = np.convolve(h_base, fir_up)
        pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
        peak = int(np.argmax(np.abs(pulse.y)))
        c = channel_cursors(pulse, osr, n_pre, n_post, peak_idx=peak)
        main = c[n_pre]
        ratios = c / main
        cur_hist.append(ratios.copy())
        tap_hist.append(taps.copy())

        # backchannel requests: sign-only inc/dec per trainable tap
        requests = np.zeros(n_taps)
        for i in range(n_taps):
            if i == n_pre:
                continue  # main tap is not requested directly (KR: c(0) via normalization)
            k_cursor = i - n_pre
            if 0 < k_cursor <= dfe_covered:
                continue  # RX DFE cancels this postcursor for free: hold
            if abs(ratios[i]) > threshold:
                # a positive residual cursor at position k is reduced by a
                # negative Tx tap at the same position (ZF direction)
                requests[i] = -np.sign(ratios[i])
        if not requests.any():
            converged = True
            break

        taps = taps + step * requests
        # peak-power constraint: renormalize, main tap absorbs the change
        taps = taps / np.abs(taps).sum()

    return BackchannelResult(taps=taps, n_pre=n_pre, converged=converged,
                             rounds=rounds,
                             cursor_history=np.asarray(cur_hist),
                             tap_history=np.asarray(tap_hist))
