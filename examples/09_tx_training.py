"""Product-partition 32G NRZ link with KR-style backchannel Tx training.

Product mixed-signal receivers carry no FFE. The linear-EQ split is:
Tx 3-tap FIR (trained by the RX over a backchannel, 802.3 clause-72 style)
+ RX CTLE + RX adaptive DFE. This example runs that exact partition:

1. no de-emphasis baseline (full dynamic link: CTLE + DFE + BB-CDR);
2. backchannel training rounds (sign-only inc/dec requests, quantized steps);
3. re-run with the trained 3-tap Tx FIR -> SNR/BER improvement.
"""

import dataclasses
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
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link, train_tx_fir  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

cfg = LinkConfig(
    modulation="nrz", symbol_rate=32e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.2, rdc=3.0,
                          r_skin=2.5e-3, loss_tangent=0.015),  # ~-13 dB @ Nyq
    tx=TxConfig(swing=1.0, rj_ui=0.01),
    rx=RxConfig(arch="mixed_signal",
                ctle=CtleConfig(enable=True, peak_db=9.0),
                dfe=DfeConfig(n_taps=3, adapt="sign_sign", mu=1e-3),
                noise_rms=0.004),
    sim=SimConfig(n_symbols=150_000, seed=5, pattern="prbs31"),
)

print("== product partition: Tx FIR (backchannel) + RX CTLE + DFE, no RX FFE ==")
r0 = run_time_link(cfg)
print(f"  baseline (no de-emphasis): {r0.summary()}")

bt = train_tx_fir(cfg, n_pre=1, n_post=1, step=1 / 64, threshold=0.02)
print(f"  {bt.summary()}")

cfg1 = dataclasses.replace(cfg, tx=dataclasses.replace(
    cfg.tx, fir_taps=tuple(bt.taps), fir_n_pre=1))
r1 = run_time_link(cfg1)
print(f"  with trained Tx FIR      : {r1.summary()}")
print(f"  slicer SNR: {r0.slicer_snr_db:.1f} -> {r1.slicer_snr_db:.1f} dB")

fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))

rounds = np.arange(bt.cursor_history.shape[0])
axes[0].plot(rounds, bt.cursor_history[:, 0], "o-", label="c(-1)/main")
axes[0].plot(rounds, bt.cursor_history[:, 2], "s-", label="c(+1)/main")
axes[0].axhspan(-0.02, 0.02, color="green", alpha=0.12, label="hold threshold")
axes[0].set(xlabel="backchannel round", ylabel="cursor ratio",
            title="RX-measured cursors during training")
axes[0].legend(fontsize=8)

axes[1].plot(rounds, bt.tap_history[:, 0], "o-", label="pre tap c(-1)")
axes[1].plot(rounds, bt.tap_history[:, 1], "^-", label="main tap c(0)")
axes[1].plot(rounds, bt.tap_history[:, 2], "s-", label="post tap c(+1)")
axes[1].set(xlabel="backchannel round", ylabel="Tx tap value",
            title="Tx FIR taps (sign-only inc/dec, sum|w|=1)")
axes[1].legend(fontsize=8)

for res, color, label in ((r0, "C0", "no de-emphasis"),
                          (r1, "C2", "trained Tx FIR")):
    axes[2].hist(res.y_slicer, bins=200, alpha=0.6, color=color, label=label)
axes[2].set(xlabel="V", ylabel="count",
            title=f"Slicer input: SNR {r0.slicer_snr_db:.1f} -> {r1.slicer_snr_db:.1f} dB")
axes[2].legend(fontsize=8)

for ax in axes:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "09_tx_training.png", dpi=130)
print(f"  wrote {OUT / '09_tx_training.png'}")
