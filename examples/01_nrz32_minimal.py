"""Phase 1 deliverable: minimal full link at 32G NRZ (static EQ + MC BER).

Chain: PRBS31 -> Tx FIR -> Whisper backplane (-32 dB @ Nyquist) -> CTLE ->
MMSE FFE -> static DFE -> slicer -> self-syncing checker.

Also runs a PAM4 sanity link (milder analytic channel) to exercise the
Gray-mapping/PRBS13Q path end to end.
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
from halo_serdes.config import load_config, apply_overrides  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, CtleConfig, DfeConfig, FfeConfig, LinkConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_static_link  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- NRZ 32G ---
cfg = load_config(REPO / "configs" / "nrz_32g.yaml", overrides={
    "channel.file": str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p"),
    "sim.n_symbols": 200_000,
})
cfg = apply_overrides(cfg, {"rx.noise_rms": 0.003,
                            "rx.ffe.n_pre": 4, "rx.ffe.n_post": 12,
                            "rx.dfe.n_taps": 3})
res = run_static_link(cfg)
print("== NRZ 32G over Whisper backplane ==")
print(" ", res.summary())
print(f"  sample phase: {res.sample_phase}/{cfg.osr},  "
      f"main cursor: {res.extras['main_cursor']:.4f} V")
print(f"  FFE taps: {np.round(res.ffe_taps, 3).tolist()}")
print(f"  DFE taps: {np.round(res.dfe_taps, 4).tolist()}")

fig, axes = plt.subplots(1, 3, figsize=(15, 4))
plot_eye(axes[0], res.eye_data, cfg.ui * 1e12, mode="density",
         title="RX eye before EQ (CTLE out)")
# slicer scatter: y_eq vs ideal
axes[1].hist(res.y_slicer, bins=200, color="C0")
axes[1].set(title="Slicer input histogram (after FFE+DFE)", xlabel="V", ylabel="count")
eq = res.extras["eq_cursors"]; ep = res.extras["eq_pre"]
k = np.arange(-6, 13)
vals = [eq[ep + i] if 0 <= ep + i < eq.size else 0.0 for i in k]
axes[2].stem(k, vals)
axes[2].set(title="Equalized cursors (FFE out)", xlabel="UI", ylabel="V")
for ax in axes:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "01_nrz32_link.png", dpi=130)
print(f"  wrote {OUT / '01_nrz32_link.png'}")

# ------------------------------------------------------------- PAM4 check ---
pam4 = LinkConfig(
    modulation="pam4", symbol_rate=56e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.08, rdc=2.0,
                          r_skin=8.0e-4, loss_tangent=0.01),
    tx=TxConfig(swing=1.0),
    rx=RxConfig(ctle=CtleConfig(enable=False),
                ffe=FfeConfig(n_pre=3, n_post=8),
                dfe=DfeConfig(n_taps=1),
                noise_rms=0.004),
    sim=SimConfig(n_symbols=100_000, seed=11, pattern="prbs13q"),
)
res4 = run_static_link(pam4, collect_eye=True)
print("== PAM4 56 GBd over mild analytic channel ==")
print(" ", res4.summary())

fig, axes = plt.subplots(1, 2, figsize=(10, 4))
plot_eye(axes[0], res4.eye_data, pam4.ui * 1e12, mode="density", title="PAM4 RX eye (pre-EQ)")
axes[1].hist(res4.y_slicer, bins=300, color="C1")
axes[1].set(title="PAM4 slicer input (4 levels)", xlabel="V", ylabel="count")
fig.tight_layout()
fig.savefig(OUT / "01_pam4_sanity.png", dpi=130)
print(f"  wrote {OUT / '01_pam4_sanity.png'}")
