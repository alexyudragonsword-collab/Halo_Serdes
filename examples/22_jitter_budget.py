"""Per-stage jitter budget — wire calc_jitter into the link pipeline.

PyBERT reports a jitter decomposition (ISI / DCD / Pj / Rj) at every stage of
the signal chain on each run. Here the same three-layer decomposition
(pattern-averaging -> spectral Pj/Rj split -> dual-Dirac tail) runs as an
opt-in pass over waveforms the time engine captures at three observation
points:

    tx    Tx driver output      — Tx RJ/SJ/DCD only, channel-clean
    chnl  channel output        — ISI explodes on a lossy channel
    ctle  after CTLE + VGA      — linear EQ claws some ISI back

A repeating short PRBS (period must divide the run several times over, or
pattern-averaging can't separate data-dependent jitter) is used so the
decomposition is well-posed. Output: the ASCII budget table + a stacked bar
of the deterministic components and the extrapolated total jitter at 1e-12.
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

from halo_serdes.analysis import (  # noqa: E402
    format_jitter_budget, total_jitter,
)
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

# 16 GBd NRZ, product mixed-signal default. Inject a known Tx jitter cocktail
# so the 'tx' row is a sanity anchor (Rj ~ rj_ui, DCD ~ dcd_ui, SJ -> Pj).
PERIOD = 127  # PRBS7 symbol period
cfg = LinkConfig(
    modulation="nrz", symbol_rate=16e9, osr=32,
    channel=ChannelConfig(kind="analytic", length_m=0.30, rdc=5.0,
                          r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
    tx=TxConfig(swing=1.0, rj_ui=0.008, sj_ui=0.012, sj_freq=4e6, dcd_ui=0.020),
    rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                dfe=DfeConfig(n_taps=4), noise_rms=0.004),
    sim=SimConfig(n_symbols=PERIOD * 16, seed=3, pattern="prbs7"))

cm = ChannelModel.from_config(cfg)
res = run_time_link(cfg, channel=cm, collect_jitter=True)
jb = res.extras["jitter_budget"]

print(f"16 GBd NRZ, 0.30 m analytic channel — loss @ Nyquist "
      f"{cm.loss_at(cfg.f_nyquist):.1f} dB")
print(f"post-DFE: BER={res.ber.ber:.2e}  slicer SNR={res.slicer_snr_db:.1f} dB\n")
print("injected Tx jitter: Rj=0.80%UI  SJ=1.20%UI  DCD=2.00%UI\n")
print(format_jitter_budget(jb, cfg.ui))

# --- stacked bar: deterministic split + total jitter at 1e-12 ---
stages = [s for s in ("tx", "chnl", "ctle") if s in jb]
u = 100.0 / cfg.ui
isi = np.array([jb[s].isi * u for s in stages])
dcd = np.array([jb[s].dcd * u for s in stages])
pj = np.array([jb[s].pj * u for s in stages])
rj = np.array([jb[s].rj * u for s in stages])
tj = np.array([total_jitter(jb[s], 1e-12) * u for s in stages])

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(11, 4.6))
x = np.arange(len(stages))
labels = {"tx": "Tx driver output", "chnl": "Channel output", "ctle": "After CTLE+VGA"}
xt = [labels.get(s, s) for s in stages]

b = np.zeros(len(stages))
for vals, lab, c in [(isi, "ISI (data-dependent)", "C0"), (dcd, "DCD", "C1"),
                     (pj, "Pj (periodic)", "C2"), (rj * 14.07, "Rj->1e-12 tail", "C3")]:
    ax0.bar(x, vals, bottom=b, label=lab, color=c, width=0.6)
    b += vals
ax0.plot(x, tj, "k_", ms=28, mew=2, label="TJ@1e-12 (total)")
ax0.set_xticks(x); ax0.set_xticklabels(xt)
ax0.set_ylabel("Jitter [%UI]")
ax0.set_title("Per-stage jitter budget (16 GBd NRZ)\nDecomposition: ISI + DCD + Pj + Rj tail")
ax0.legend(fontsize=8, loc="upper left")
ax0.grid(True, axis="y", alpha=0.3)

# Rj-only zoom (the deterministic bars dwarf it on the closed pre-DFE eye)
ax1.bar(x, rj, color="C3", width=0.6)
for xi, v in zip(x, rj):
    ax1.text(xi, v, f"{v:.2f}%", ha="center", va="bottom", fontsize=9)
ax1.set_xticks(x); ax1.set_xticklabels(xt)
ax1.set_ylabel("Rj (rms) [%UI]")
ax1.set_title("Random jitter (rms) per stage\nTx stage should ≈ injected 0.80%UI")
ax1.grid(True, axis="y", alpha=0.3)

fig.tight_layout()
fig.savefig(OUT / "22_jitter_budget.png", dpi=130)
print(f"\nwrote {OUT / '22_jitter_budget.png'}")
