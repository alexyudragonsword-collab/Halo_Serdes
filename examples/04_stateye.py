"""Phase 3 deliverable: statistical eye + bathtub, cross-checked against MC.

Same lossy channel evaluated two ways:
- Monte-Carlo (static link, 4e5 symbols) — measurable down to ~1e-5;
- statistical engine — same configuration, extrapolated to arbitrarily low BER.

The bathtub overlay is the acceptance visual: MC points must sit on the
statistical curve in the overlap region.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, CtleConfig, DfeConfig, FfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_static_link  # noqa: E402
from halo_serdes.engine.statistical import run_statistical  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

cfg = LinkConfig(
    modulation="nrz", symbol_rate=32e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.15, rdc=2.0,
                          r_skin=1.5e-3, loss_tangent=0.01),
    tx=TxConfig(swing=1.0),
    rx=RxConfig(ctle=CtleConfig(enable=False),
                ffe=FfeConfig(n_pre=2, n_post=6),
                dfe=DfeConfig(n_taps=2),
                noise_rms=0.05),
    sim=SimConfig(n_symbols=400_000, seed=6, pattern="prbs31"),
)

mc = run_static_link(cfg, collect_eye=False)
stat = run_statistical(cfg, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)

print("== dual-engine comparison (32G NRZ, lossy analytic channel) ==")
print(f"  Monte-Carlo : BER = {mc.ber.ber:.3e} ({mc.ber.n_errors} errors / {mc.ber.n_checked})")
print(f"  statistical : BER = {stat.ber:.3e} at best phase (ratio {stat.ber / mc.ber.ber:.2f}x)")

# low-noise variant: where only the statistical engine can go
import dataclasses  # noqa: E402

cfg_lo = dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=0.02))
stat_lo = run_statistical(cfg_lo, ffe_taps=mc.ffe_taps, ffe_pre=2)
print(f"  statistical @ sigma=20mV: BER = {stat_lo.ber:.3e} (below any MC reach)")

fig, axes = plt.subplots(1, 3, figsize=(16, 4.2))

# statistical eye
img = np.log10(stat.eye_pdf + 1e-18)
axes[0].imshow(img, aspect="auto", origin="lower", cmap="inferno",
               extent=(stat.phi_ui[0], stat.phi_ui[-1],
                       stat.v_centers[0], stat.v_centers[-1]),
               vmin=-12, vmax=0)
axes[0].set(xlabel="phase [UI]", ylabel="V", title="Statistical eye (log10 PDF)")

# phase bathtub
axes[1].semilogy(stat.phi_ui, np.maximum(stat.ber_phi, 1e-18), label="statistical")
axes[1].axhline(mc.ber.ber, color="C1", ls="--",
                label=f"MC @ best phase ({mc.ber.ber:.1e})")
axes[1].semilogy(stat.phi_ui, np.maximum(stat_lo.ber_phi, 1e-18), color="C2",
                 label="statistical, sigma=20mV")
axes[1].set(xlabel="sampling phase [UI]", ylabel="BER",
            title="Phase bathtub: MC overlaps statistical", ylim=(1e-18, 1))
axes[1].legend(fontsize=8)

# slicer histogram (MC) vs statistical PDF at best phase
pcol = stat.eye_pdf[:, stat.best_phi]
dv = stat.v_centers[1] - stat.v_centers[0]
axes[2].semilogy(stat.v_centers, np.maximum(pcol / dv, 1e-12), label="statistical PDF")
hist, edges = np.histogram(mc.y_slicer, bins=200, density=True)
axes[2].semilogy((edges[:-1] + edges[1:]) / 2, np.maximum(hist, 1e-12), ".",
                 ms=3, label="MC histogram")
axes[2].set(xlabel="V", ylabel="pdf", title="Slicer-input PDF: engines overlaid",
            ylim=(1e-7, None))
axes[2].legend(fontsize=8)

for ax in axes:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "04_stateye.png", dpi=130)
print(f"  wrote {OUT / '04_stateye.png'}")
