"""The canonical product mixed-signal link: 16G NRZ (this mode's default).

Loads configs/nrz_16g_ms.yaml — 16 GBd is the validated upper limit of the
CTLE + DFE (tap-1 unrolled) + BB-CDR partition with no RX FFE. Runs the full
dynamic chain with RJ/SJ jitter and plots CDR acquisition, PD activity, and
the locked slicer histogram.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.config import apply_overrides, load_config  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

cfg = load_config(REPO / "configs" / "nrz_16g_ms.yaml", overrides={
    "channel.file": str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p"),
})
# add sinusoidal jitter on top of the canonical config to exercise the CDR
cfg = apply_overrides(cfg, {"tx.sj_ui": 0.02, "tx.sj_freq": 5.0e6})

res = run_time_link(cfg, collect_eye=True)
print("== 16G NRZ, product mixed-signal RX (canonical default config) ==")
print(" ", res.summary())
print(f"  DFE taps: init {np.round(res.extras['w_dfe0'], 4).tolist()}"
      f" -> final {np.round(res.dfe_taps, 4).tolist()}")
print(f"  startup: settle={res.extras['settle']}, data-aided until {res.extras['train_end']}")

ph = res.extras["phase_track"]
pd = res.extras["pd_hist"]
osr = cfg.osr
n = ph.size
ph_dev = (ph - ph[0] - np.arange(n) * osr) / osr

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
fig.savefig(OUT / "02_ms_rx_nrz16.png", dpi=130)
print(f"  wrote {OUT / '02_ms_rx_nrz16.png'}")
