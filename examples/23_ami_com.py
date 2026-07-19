"""IBIS-AMI adapter seam + behavioral COM.

Two things this shows:

1. **COM sweep** — the native behavioral Channel Operating Margin (a
   transparent stand-in for the IEEE 802.3 tool, plugged in through the same
   ComAdapter.compute seam) vs channel loss, at two Tx-FIR strengths. COM
   crosses the ~3 dB pass line where the margin runs out.

2. **AMI Rx model in the loop** — a native FIR AMI model (the dependency-free
   reference for the AmiModel interface a real pyibisami `.so` binds to) is
   dropped into the time engine's Rx GetWave slot and the post-EQ SNR is read
   back, proving the seam carries a model end-to-end.

A real vendor model would be `load_ami_model(ami_file=..., dll_file=...)`
instead of the native FIR — identical call sites downstream.
"""

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

from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, CtleConfig, DfeConfig, FfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.io import NativeCom, NativeFirAmi, load_ami_model  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)


def cfg_for(length_m, fir, noise=0.004):
    return LinkConfig(
        modulation="nrz", symbol_rate=28e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=fir, fir_n_pre=1, rj_ui=0.005),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                    ffe=FfeConfig(n_pre=4, n_post=12), dfe=DfeConfig(n_taps=2),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=40_000, seed=4, pattern="prbs13"))


# --- 1. COM sweep vs loss, two Tx-FIR strengths ---
com = NativeCom(target_der=1e-4, n_dfe=2, rx_ffe_taps=15, rx_ffe_pre=4)
FIRS = {"Weak Tx FIR (-0.06,0.9,-0.04)": (-0.06, 0.90, -0.04),
        "Strong Tx FIR (-0.12,0.8,-0.08)": (-0.12, 0.80, -0.08)}
lengths = np.array([0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50])

print("== Behavioral COM sweep (28 GBd NRZ) ==")
fig, (axc, axb) = plt.subplots(1, 2, figsize=(11, 4.6))
for (label, fir), c in zip(FIRS.items(), ("C0", "C1")):
    loss, comdb = [], []
    for L in lengths:
        cfg = cfg_for(L, fir)
        cm = ChannelModel.from_config(cfg)
        r = com.compute(cm, cfg)
        loss.append(-cm.loss_at(cfg.f_nyquist)); comdb.append(r.com_db)
    axc.plot(loss, comdb, "o-", color=c, label=label)
    print(f"  {label}: " + "  ".join(f"{l:.0f}dB->{cb:.1f}"
          for l, cb in zip(loss, comdb)))
axc.axhline(3.0, color="green", ls=":", lw=1.2, label="COM pass line ~3 dB")
axc.set(xlabel="Channel loss @ 14 GHz Nyquist [dB]", ylabel="Behavioral COM [dB]",
        title="Behavioral COM vs loss\n(via ComAdapter.compute seam)")
axc.legend(fontsize=8); axc.grid(True, alpha=0.3)

# --- 2. AMI Rx model in the engine loop: post-cursor FIR strength sweep ---
print("\n== AMI Rx GetWave model in engine (SNR readback) ==")
post_taps = [0.0, -0.03, -0.06, -0.09, -0.12]
snrs = []
base_cfg = cfg_for(0.25, (-0.08, 0.85, -0.05))
cm = ChannelModel.from_config(base_cfg)
for pt in post_taps:
    # a real model would be load_ami_model(ami_file=..., dll_file=...)
    rx_ami = load_ami_model(taps=[1.0, pt], n_pre=0, sample_spaced=False)
    res = run_time_link(base_cfg, channel=cm, rx_ami=rx_ami)
    snrs.append(res.slicer_snr_db)
    print(f"  Rx AMI post tap {pt:+.2f} -> slicer SNR {res.slicer_snr_db:.2f} dB "
          f"(BER {res.ber.ber:.1e})")
axb.plot([-p for p in post_taps], snrs, "s-", color="C3")
axb.set(xlabel="Rx AMI post-cursor cancel strength |tap|", ylabel="slicer SNR [dB]",
        title="Native FIR AMI model in Rx GetWave slot\n(vendor .so uses same load_ami_model seam)")
axb.grid(True, alpha=0.3)

assert isinstance(rx_ami, NativeFirAmi)  # native reference in use (no pyibisami)
fig.tight_layout()
fig.savefig(OUT / "23_ami_com.png", dpi=130)
print(f"\nwrote {OUT / '23_ami_com.png'}")
