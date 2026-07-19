"""Post-EQ eye diagrams for the 32G NRZ Whisper link.

Reconstructs the *oversampled* equalized waveform (baud-spaced FFE taps
upsampled onto the waveform grid) and the DFE summing-node view (per-UI
staircase feedback subtracted), then folds all three eyes:

    pre-EQ (CTLE out)  ->  post-FFE  ->  post-FFE+DFE (summing node)
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.analysis.eye import plot_eye  # noqa: E402
from halo_serdes.config import apply_overrides, load_config  # noqa: E402
from halo_serdes.engine import run_static_link  # noqa: E402
from halo_serdes.engine.static_link import fold_eye, make_pattern  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

cfg = load_config(REPO / "configs" / "nrz_32g.yaml", overrides={
    "channel.file": str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p"),
    "sim.n_symbols": 200_000,
})
cfg = apply_overrides(cfg, {"rx.noise_rms": 0.003,
                            "rx.ffe.n_pre": 4, "rx.ffe.n_post": 12,
                            "rx.dfe.n_taps": 3})

res = run_static_link(cfg, collect_eye=True)
print("== 32G NRZ over Whisper backplane ==")
print(" ", res.summary())

# ---- rebuild the received oversampled waveform exactly as the engine did ----
import numpy.fft as fft  # noqa: E402

from halo_serdes.afe import Ctle  # noqa: E402
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.tx import build_tx_waveform  # noqa: E402

rng = np.random.default_rng(cfg.sim.seed)
symbols = make_pattern(cfg)
tx_wave = build_tx_waveform(symbols, cfg)
channel = ChannelModel.from_config(cfg)
ch_rs = channel.response_set(cfg.dt)
ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist) if cfg.rx.ctle.enable else None
n = tx_wave.y.size
f = fft.rfftfreq(n, d=cfg.dt)
H = fft.rfft(ch_rs.h.y, n=n)
if ctle is not None:
    H = H * ctle.transfer(f)
rx_y = fft.irfft(fft.rfft(tx_wave.y) * H, n=n)
rx_y += rng.normal(scale=cfg.rx.noise_rms, size=rx_y.size)

osr = cfg.osr
phase = res.sample_phase

# ---- post-FFE waveform: upsample the baud-spaced taps onto the grid ----
w = res.ffe_taps
tap_pre = cfg.rx.ffe.n_pre
w_up = np.zeros((w.size - 1) * osr + 1)
w_up[::osr] = w
y_ffe = np.convolve(rx_y, w_up)[tap_pre * osr: tap_pre * osr + rx_y.size]

# ---- DFE summing-node waveform: subtract per-UI staircase feedback ----
from halo_serdes.engine.static_link import _levels  # noqa: E402

levels = _levels(cfg) * res.extras["main_cursor"]  # engine slicer levels
y_baud = y_ffe[phase::osr]
from halo_serdes.dsp import dfe_static  # noqa: E402

dec, _ = dfe_static(y_baud.astype(np.float64), res.dfe_taps.astype(np.float64),
                    levels)
fb_baud = np.zeros(y_baud.size)
for i, wd in enumerate(res.dfe_taps, start=1):
    fb_baud[i:] += wd * levels[dec[:-i]]
# staircase: feedback for symbol k applies over its UI centered on the sampler
fb_wave = np.zeros_like(y_ffe)
start = phase - osr // 2
fb_up = np.repeat(fb_baud, osr)
lo = max(0, start)
seg = fb_up[lo - start: lo - start + (y_ffe.size - lo)]
fb_wave[lo: lo + seg.size] = seg
y_dfe = y_ffe - fb_wave

# ---- fold and plot the three eyes ----
fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
for ax, y_plot, title in (
        (axes[0], rx_y, "pre-EQ eye (CTLE out) — closed"),
        (axes[1], y_ffe, "post-FFE eye"),
        (axes[2], y_dfe, "post-FFE+DFE eye (summing node)")):
    eye = fold_eye(y_plot, osr, phase, n_traces=2000)
    plot_eye(ax, eye, cfg.ui * 1e12, mode="density", title=title)
    ax.axvline(0.0, color="w", ls=":", lw=0.8, alpha=0.7)

# annotate measured eye height at the sampling instant on the DFE eye
eye_d = fold_eye(y_dfe, osr, phase, n_traces=4000)
center_col = eye_d.shape[1] // 2
samples = eye_d[:, center_col]
hi, lo_ = samples[samples > 0], samples[samples < 0]
eye_h = (hi.min() - lo_.max()) if hi.size and lo_.size else 0.0
axes[2].set_xlabel(f"Time [UI]   (eye height @ center: {eye_h * 1e3:.1f} mV)")
print(f"  post-EQ inner eye height at sampling instant: {eye_h * 1e3:.1f} mV")
print(f"  slicer levels: ±{abs(res.extras['main_cursor']):.4f} V")

fig.tight_layout()
fig.savefig(OUT / "03_eq_eye_nrz32.png", dpi=130)
print(f"  wrote {OUT / '03_eq_eye_nrz32.png'}")
