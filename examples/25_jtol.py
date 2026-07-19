"""Jitter tolerance (JTOL) — tolerated sinusoidal jitter vs SJ frequency.

Inject Tx sinusoidal jitter and binary-search the amplitude where BER crosses
a threshold, at each SJ frequency. Below the CDR loop bandwidth the recovered
clock tracks the jitter (large tolerance); above it the sampling point cannot
follow and tolerance rolls off ~20 dB/dec to the intrinsic eye margin. Overlay
a generic compliance mask — the tolerance must stay above it.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.analysis import jitter_tolerance, jtol_mask  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig, RxConfig,
    SimConfig, TxConfig,
)

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)


def cfg_for(kp_shift):
    return LinkConfig(
        modulation="nrz", symbol_rate=28e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.28, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.85, -0.05), fir_n_pre=1,
                    rj_ui=0.004),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                    ffe=FfeConfig(n_pre=4, n_post=12), dfe=DfeConfig(n_taps=2),
                    cdr=CdrConfig(kind="bang_bang", kp_shift=kp_shift, ki_shift=12),
                    noise_rms=0.003),
        sim=SimConfig(n_symbols=100000, seed=4, pattern="prbs13"))


freqs = np.array([1e6, 3e6, 1e7, 3e7, 6e7, 1e8, 2e8, 4e8])
fig, ax = plt.subplots(figsize=(7.5, 5))

# two CDR loop bandwidths (faster loop = corner pushes to higher frequency)
print("Sweeping JTOL for two CDR loop gains...")
for kp, color, tag in [(7, "C0", "slower loop (Kp=2^-7)"),
                       (3, "C1", "faster loop (Kp=2^-3)")]:
    jt = jitter_tolerance(cfg_for(kp), freqs, ber_threshold=1e-3,
                          amp_lo=0.05, amp_hi=5.0, iters=6, n_symbols=30000)
    print(f"  {tag}: " + jt.summary())
    ax.loglog(jt.freqs / 1e6, jt.tol_ui, "o-", color=color, label=tag)

mask = jtol_mask(freqs, lf_max_ui=5.0, f_corner=3e6, hf_floor_ui=0.15)
ax.loglog(freqs / 1e6, mask, "k--", lw=1.4, label="compliance mask (generic)")
ax.fill_between(freqs / 1e6, mask, 1e-2, color="red", alpha=0.06)

ax.set(xlabel="SJ frequency [MHz]", ylabel="tolerated SJ amplitude [UI, 0-pk]",
       title="Jitter tolerance (28 GBd NRZ, BER < 1e-3)\n"
             "flat where the CDR tracks; rolls off above the loop bandwidth")
ax.grid(True, which="both", alpha=0.3)
ax.legend(fontsize=8)
ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
fig.tight_layout()
fig.savefig(OUT / "25_jtol.png", dpi=130)
print(f"wrote {OUT / '25_jtol.png'}")
