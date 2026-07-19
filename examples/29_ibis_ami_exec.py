"""Real IBIS-AMI execution: compile a model and run it over the C ABI.

Most "AMI support" in behavioral tools stops at a seam. This actually *runs* a
compiled IBIS-AMI shared object: the shipped reference model (io/ami_c/
halo_fir_ami.c, a UI-spaced FFE with the three spec C entry points) is compiled
with the system C compiler and driven through its real AMI_Init / AMI_GetWave /
AMI_Close ABI by AmiCModel — no pyibisami, no vendor binary.

Panel 1: the model's AMI_Init transform of the channel impulse response,
run inside the .so, overlaid on the dependency-free native reference (they
match bit-for-bit — the seam is faithful).
Panel 2: the Tx-EQ effect on the link — slicer-input SNR with the AMI model in
the Tx slot vs no equalization, for both the Init (LTI) and GetWave (time-domain)
flows.

A vendor model is a drop-in: load_ami_model(so_file="vendor.so", ...) — or
load_ami_model(ami_file=..., dll_file=...) for the pyibisami backend — with
identical call sites downstream.
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
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.io import (  # noqa: E402
    AmiCModel, NativeFirAmi, build_reference_ami,
)

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

TAPS = [-0.08, 0.85, -0.05]
N_PRE = 1

# compile the real IBIS-AMI shared object
so, ami = build_reference_ami(out_dir=str(OUT))
print(f"compiled {Path(so).name} from {Path(so).name.replace('.so', '.c')} "
      f"+ {Path(ami).name}")

cfg = LinkConfig(
    modulation="nrz", symbol_rate=28e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.28, rdc=5.0, r_skin=2e-3,
                          loss_tangent=0.012, n_freq=8192),
    tx=TxConfig(swing=1.0, fir_taps=(1.0,), fir_n_pre=0),   # EQ delegated to AMI
    rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                dfe=DfeConfig(n_taps=2), noise_rms=0.004),
    sim=SimConfig(n_symbols=40000, seed=5, pattern="prbs13"))
ch = ChannelModel.from_config(cfg)

# --- Init flow: run the .so's AMI_Init on the channel impulse ---
h = ch.response_set(cfg.dt).h.y
c_model = AmiCModel(so, taps=TAPS, n_pre=N_PRE)
h_c = c_model.init(h.copy(), cfg.dt, cfg.ui)
h_n = NativeFirAmi(TAPS, n_pre=N_PRE, sample_spaced=False).init(h.copy(), cfg.dt, cfg.ui)
print(f"AMI_Init: max|C .so - native| = {np.max(np.abs(h_c - h_n)):.2e}")
print(f"model msg: {c_model.messages}")
c_model.close()

# --- link SNR: AMI Tx-EQ vs none, both flows ---
base = run_time_link(cfg, channel=ch).slicer_snr_db
snr = {}
for flow, gw in (("Init (LTI)", False), ("GetWave", True)):
    cm = AmiCModel(so, taps=TAPS, n_pre=N_PRE, has_getwave=gw)
    nm = NativeFirAmi(TAPS, n_pre=N_PRE, sample_spaced=False)
    nm.has_getwave = gw
    sc = run_time_link(cfg, channel=ch, tx_ami=cm).slicer_snr_db
    sn = run_time_link(cfg, channel=ch, tx_ami=nm).slicer_snr_db
    snr[flow] = (sc, sn)
    print(f"{flow:12s}: C .so SNR={sc:.3f} dB  native={sn:.3f} dB  (Δ={abs(sc-sn):.1e})")
    cm.close()

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12.4, 4.6))
t = np.arange(h.size) * cfg.dt * 1e9
sl = slice(int(0.3 * h.size), int(0.55 * h.size))
ax0.plot(t[sl], h[sl], color="C7", lw=1, alpha=0.7, label="channel h(t)")
ax0.plot(t[sl], h_n[sl], color="C1", lw=2.4, alpha=0.6, label="native FIR")
ax0.plot(t[sl], h_c[sl], "--", color="C0", lw=1.4, label="compiled .so (AMI_Init)")
ax0.set(xlabel="time [ns]", ylabel="amplitude",
        title="AMI_Init impulse transform (run in the .so)")
ax0.grid(True, alpha=0.3)
ax0.legend()

flows = list(snr)
x = np.arange(len(flows))
ax1.bar(x - 0.18, [snr[f][0] for f in flows], 0.34, label="compiled .so", color="C0")
ax1.bar(x + 0.18, [snr[f][1] for f in flows], 0.34, label="native ref", color="C1")
ax1.axhline(base, color="C3", ls=":", label=f"no Tx-EQ ({base:.1f} dB)")
ax1.set_xticks(x)
ax1.set_xticklabels(flows)
ax1.set(ylabel="slicer-input SNR [dB]", title="Tx-EQ via the AMI model")
ax1.grid(True, axis="y", alpha=0.3)
ax1.legend()

fig.tight_layout()
fig.savefig(OUT / "29_ibis_ami_exec.png", dpi=130)
print(f"wrote {OUT / '29_ibis_ami_exec.png'}")
