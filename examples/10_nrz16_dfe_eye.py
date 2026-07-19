"""16 Gb/s NRZ, product partition (NO RX FFE): does the post-DFE eye open?

Channel: Whisper 42.8" backplane, -18.7 dB @ 8 GHz Nyquist.
RX: CTLE + 4-tap DFE (tap-1 unrolled) + BB-CDR — no RX FFE anywhere.
Tx: 3-tap FIR trained over the backchannel (KR-style), the only linear EQ
besides the CTLE.

Outputs: full dynamic-link BER/SNR (with and without the trained Tx FIR) and
the reconstructed eyes: CTLE output vs DFE summing-node, eye height measured
at the sampling instant.
"""

import dataclasses
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.afe import Ctle  # noqa: E402
from halo_serdes.analysis.eye import plot_eye  # noqa: E402
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.channel.response import pulse_from_impulse  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.waveform import Waveform  # noqa: E402
from halo_serdes.dsp import channel_cursors, dfe_static  # noqa: E402
from halo_serdes.engine import run_time_link, train_tx_fir  # noqa: E402
from halo_serdes.engine.static_link import _levels, fold_eye, make_pattern  # noqa: E402
from halo_serdes.tx.builder import symbols_to_voltages, tx_fir  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

S4P = str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p")

cfg = LinkConfig(
    modulation="nrz", symbol_rate=16e9, osr=32,
    channel=ChannelConfig(kind="touchstone", file=S4P, n_freq=4096),
    tx=TxConfig(swing=1.0, rj_ui=0.01),
    rx=RxConfig(arch="mixed_signal",
                ctle=CtleConfig(enable=True, peak_db=7.0),
                dfe=DfeConfig(n_taps=4, adapt="sign_sign", mu=5e-4,
                              tap1_mode="unrolled"),
                noise_rms=0.003),
    sim=SimConfig(n_symbols=120_000, seed=13, pattern="prbs31"),
)

channel = ChannelModel.from_config(cfg)
print(f"== 16G NRZ over Whisper ({channel.loss_at(8e9):.1f} dB @ Nyquist), "
      f"NO RX FFE ==")

# --- dynamic link, before/after backchannel Tx training ---
r0 = run_time_link(cfg, channel=channel)
# RX DFE covers postcursors 1..4: the backchannel only requests Tx swing for
# ISI the DFE cannot reach (precursor)
bt = train_tx_fir(cfg, channel=channel, n_pre=1, n_post=1, threshold=0.02,
                  dfe_covered=cfg.rx.dfe.n_taps)
cfg_t = dataclasses.replace(cfg, tx=dataclasses.replace(
    cfg.tx, fir_taps=tuple(bt.taps), fir_n_pre=1))
r1 = run_time_link(cfg_t, channel=channel)
print(f"  no Tx FIR      : {r0.summary()}")
print(f"  {bt.summary()}")
print(f"  trained Tx FIR : {r1.summary()}")

# --- eye reconstruction (no jitter for clean folding; same EQ state) ---
def build_rx_wave(c: LinkConfig) -> tuple[np.ndarray, np.ndarray, int]:
    rng = np.random.default_rng(c.sim.seed)
    symbols = make_pattern(c)
    v = symbols_to_voltages(symbols, c)
    if len(c.tx.fir_taps) > 1:
        v = tx_fir(v, c.tx.fir_taps, c.tx.fir_n_pre)
    y = np.repeat(v, c.osr)
    h = channel.response_set(c.dt).h.y
    ctle = Ctle.from_config(c.rx.ctle, c.f_nyquist)
    nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
    f = np.fft.rfftfreq(nfft, d=c.dt)
    h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    n = y.size
    rx = np.fft.irfft(np.fft.rfft(y) * np.fft.rfft(h, n), n)
    rx += rng.normal(scale=c.rx.noise_rms, size=n)
    return rx, h, symbols.size


def post_dfe_wave(c: LinkConfig, rx: np.ndarray, h: np.ndarray):
    osr = c.osr
    pulse = pulse_from_impulse(Waveform(h, c.dt), osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    n_dfe = c.rx.dfe.n_taps
    cur = channel_cursors(pulse, osr, 0, n_dfe, peak_idx=peak)
    main = cur[0]
    w_dfe = cur[1:] / main
    levels = _levels(c) * abs(main)
    phase = peak % osr
    y_baud = rx[phase::osr]
    dec, _ = dfe_static(y_baud.astype(np.float64), w_dfe, levels)
    fb_baud = np.zeros(y_baud.size)
    for i, wd in enumerate(w_dfe, start=1):
        fb_baud[i:] += wd * levels[dec[:-i]]
    fb_wave = np.zeros_like(rx)
    start = phase - osr // 2
    fb_up = np.repeat(fb_baud, osr)
    lo = max(0, start)
    seg = fb_up[lo - start: lo - start + (rx.size - lo)]
    fb_wave[lo: lo + seg.size] = seg
    return rx - fb_wave, phase, levels


def eye_height(eye: np.ndarray) -> float:
    col = eye[:, eye.shape[1] // 2]
    hi, lo = col[col > 0], col[col < 0]
    return float(hi.min() - lo.max()) if hi.size and lo.size else float("-inf")


fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
titles_data = []
# panel 1: CTLE out (trained FIR case)
rx_t, h_t, _ = build_rx_wave(dataclasses.replace(
    cfg_t, tx=dataclasses.replace(cfg_t.tx, rj_ui=0.0)))
eye_ctle = fold_eye(rx_t, cfg.osr, int(np.argmax(np.abs(pulse_from_impulse(
    Waveform(h_t, cfg.dt), cfg.osr).y))) % cfg.osr, n_traces=2500)
plot_eye(axes[0], eye_ctle, cfg.ui * 1e12, title="CTLE out (trained Tx FIR)")

# panel 2: post-DFE, no Tx FIR
rx_0, h_0, _ = build_rx_wave(dataclasses.replace(
    cfg, tx=dataclasses.replace(cfg.tx, rj_ui=0.0)))
y_d0, ph0, lv0 = post_dfe_wave(cfg, rx_0, h_0)
eye_d0 = fold_eye(y_d0, cfg.osr, ph0, n_traces=2500)
eh0 = eye_height(fold_eye(y_d0, cfg.osr, ph0, n_traces=4000))
plot_eye(axes[1], eye_d0, cfg.ui * 1e12,
         title=f"Post-DFE (no Tx FIR) eye={eh0 * 1e3:.1f} mV")

# panel 3: post-DFE, trained Tx FIR
y_d1, ph1, lv1 = post_dfe_wave(cfg_t, rx_t, h_t)
eye_d1 = fold_eye(y_d1, cfg.osr, ph1, n_traces=2500)
eh1 = eye_height(fold_eye(y_d1, cfg.osr, ph1, n_traces=4000))
plot_eye(axes[2], eye_d1, cfg.ui * 1e12,
         title=f"Post-DFE (trained Tx FIR) eye={eh1 * 1e3:.1f} mV")

for ax in axes:
    ax.axvline(0.0, color="w", ls=":", lw=0.8, alpha=0.7)
fig.tight_layout()
fig.savefig(OUT / "10_nrz16_dfe_eye.png", dpi=130)
print(f"  post-DFE inner eye: no FIR {eh0 * 1e3:.1f} mV -> trained FIR {eh1 * 1e3:.1f} mV"
      f"  (slicer levels ±{lv1[1] * 1e3:.1f} mV)")
print(f"  wrote {OUT / '10_nrz16_dfe_eye.png'}")
