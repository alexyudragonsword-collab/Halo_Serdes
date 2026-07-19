"""Phase 2 deliverable: full dynamic mixed-signal RX at 32G NRZ.

Chain: PRBS31 -> Tx FIR + RJ/SJ jitter -> Whisper backplane -> CTLE ->
joint adaptive DFE + bang-bang CDR (staged startup: settle -> data-aided ->
decision-directed). Plots CDR phase acquisition, PD activity, and the
recovered slicer histogram.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.config import load_config, apply_overrides  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

# Mixed-signal (CTLE + DFE, no FFE) suits moderate-loss channels; the -32 dB
# Whisper backplane is ADC/DSP territory (that comparison is the Phase 5
# flagship experiment). Here: ~-13 dB analytic trace at 16 GHz Nyquist.
from halo_serdes.config.schema import ChannelConfig  # noqa: E402
import dataclasses  # noqa: E402

cfg = load_config(REPO / "configs" / "nrz_32g.yaml")
cfg = dataclasses.replace(cfg, channel=ChannelConfig(
    kind="analytic", length_m=0.2, rdc=3.0, r_skin=2.5e-3, loss_tangent=0.015))
cfg = apply_overrides(cfg, {
    "osr": 16,
    "sim.n_symbols": 150_000,
    "tx.rj_ui": 0.01,
    "tx.sj_ui": 0.02, "tx.sj_freq": 5.0e6,
    "rx.noise_rms": 0.002,
    "rx.ctle.peak_db": 9.0,
    "rx.dfe.n_taps": 3,
    "rx.dfe.adapt": "sign_sign",
    "rx.dfe.mu": 1.0e-3,
    "rx.cdr.kp_shift": 5,
    "rx.cdr.ki_shift": 12,
})

res = run_time_link(cfg, collect_eye=True)
print("== 32G NRZ, mixed-signal RX (CTLE + adaptive DFE + BB-CDR) ==")
print(" ", res.summary())
print(f"  DFE taps: init {np.round(res.extras['w_dfe0'], 4).tolist()}"
      f" -> final {np.round(res.dfe_taps, 4).tolist()}")
print(f"  startup: settle={res.extras['settle']}, data-aided until {res.extras['train_end']}")

ph = res.extras["phase_track"]
pd = res.extras["pd_hist"]
osr = cfg.osr
n = ph.size
ph_dev = (ph - ph[0] - np.arange(n) * osr) / osr  # accumulated deviation [UI]

fig, axes = plt.subplots(1, 3, figsize=(15, 4))
axes[0].plot(np.arange(n) / 1e3, ph_dev, lw=0.7)
axes[0].axvline(res.extras["settle"] / 1e3, ls="--", c="gray", label="settle end")
axes[0].axvline(res.extras["train_end"] / 1e3, ls=":", c="gray", label="train end")
axes[0].set(xlabel="symbol [k]", ylabel="phase deviation [UI]",
            title="CDR phase acquisition & SJ tracking")
axes[0].legend()

win = 2000
pd_ma = np.convolve(pd.astype(float), np.ones(win) / win, mode="valid")
axes[1].plot(np.arange(pd_ma.size) / 1e3, pd_ma, lw=0.7)
axes[1].set(xlabel="symbol [k]", ylabel=f"PD mean (win={win})",
            title="Bang-bang PD activity (0 = locked)")

axes[2].hist(res.y_slicer, bins=200, color="C0")
for lv in res.extras["levels"]:
    axes[2].axvline(lv, ls="--", c="r", alpha=0.6)
axes[2].set(title="Slicer input after DFE (locked)", xlabel="V")

for ax in axes:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "02_ms_rx_nrz32.png", dpi=130)
print(f"  wrote {OUT / '02_ms_rx_nrz32.png'}")
