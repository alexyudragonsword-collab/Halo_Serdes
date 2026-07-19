"""32 Gb/s PAM4 (16 GBd) product mixed-signal: post-DFE eye diagrams.

Canonical config (configs/pam4_32g_ms.yaml): CTLE + 4-tap DFE (tap-1
unrolled) + BB-CDR, no RX FFE, -7.9 dB @ 8 GHz analytic channel.
Reconstructs the CTLE-output eye and the DFE summing-node eye, and measures
the three PAM4 sub-eye heights at the sampling instant.
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
from halo_serdes.config import load_config  # noqa: E402
from halo_serdes.core.waveform import Waveform  # noqa: E402
from halo_serdes.dsp import channel_cursors, dfe_static  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.static_link import _levels, fold_eye, make_pattern  # noqa: E402
from halo_serdes.tx.builder import symbols_to_voltages, tx_fir  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

cfg = load_config(REPO / "configs" / "pam4_32g_ms.yaml")
channel = ChannelModel.from_config(cfg)
print(f"== 32 Gb/s PAM4 (16 GBd) mixed-signal, {channel.loss_at(8e9):.1f} dB @ Nyquist ==")

res = run_time_link(cfg, channel=channel)
print(" ", res.summary())

# --- waveform reconstruction (jitter off for clean folding) ---
c = dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, rj_ui=0.0))
rng = np.random.default_rng(c.sim.seed)
symbols = make_pattern(c)
v = symbols_to_voltages(symbols, c)
if len(c.tx.fir_taps) > 1:
    v = tx_fir(v, c.tx.fir_taps, c.tx.fir_n_pre)
y_tx = np.repeat(v, c.osr)
h = channel.response_set(c.dt).h.y
ctle = Ctle.from_config(c.rx.ctle, c.f_nyquist)
nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
f = np.fft.rfftfreq(nfft, d=c.dt)
h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
n = y_tx.size
rx = np.fft.irfft(np.fft.rfft(y_tx) * np.fft.rfft(h, n), n)
rx += rng.normal(scale=c.rx.noise_rms, size=n)

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
y_dfe = rx - fb_wave

# --- PAM4 sub-eye heights at the sampling instant ---
eye_big = fold_eye(y_dfe, osr, phase, n_traces=5000)
col = eye_big[:, eye_big.shape[1] // 2]
thr = (levels[:-1] + levels[1:]) / 2
groups = [col[col < thr[0]], col[(col >= thr[0]) & (col < thr[1])],
          col[(col >= thr[1]) & (col < thr[2])], col[col >= thr[2]]]
sub_eyes = [float(groups[i + 1].min() - groups[i].max())
            if groups[i].size and groups[i + 1].size else float("nan")
            for i in range(3)]
print(f"  PAM4 sub-eye heights @ sampler: "
      f"{', '.join(f'{e * 1e3:.1f} mV' for e in sub_eyes)}  "
      f"(levels ±{levels[3] * 1e3:.0f}/{levels[2] * 1e3:.0f} mV)")

fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
eye_ctle = fold_eye(rx, osr, phase, n_traces=3000)
plot_eye(axes[0], eye_ctle, c.ui * 1e12, title="CTLE 输出(4 电平 + ISI)")
plot_eye(axes[1], fold_eye(y_dfe, osr, phase, n_traces=3000), c.ui * 1e12,
         title=f"DFE 后:三只子眼 {sub_eyes[0]*1e3:.0f}/{sub_eyes[1]*1e3:.0f}/"
               f"{sub_eyes[2]*1e3:.0f} mV")
for ax in axes:
    ax.axvline(0.0, color="w", ls=":", lw=0.8, alpha=0.7)
fig.tight_layout()
fig.savefig(OUT / "11_pam4_32g_ms_eye.png", dpi=130)
print(f"  wrote {OUT / '11_pam4_32g_ms_eye.png'}")
