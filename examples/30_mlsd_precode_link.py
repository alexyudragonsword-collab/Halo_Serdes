"""MLSD and 1+D precoding as link-level options (not just standalone kernels).

Both are now configuration switches the engines honour end-to-end:

* ``rx.mlsd`` — a sequence detector re-decides the stream over the residual ISI
  the FFE/DFE left behind (``sliding`` = DragonPHY's low-cost error-event
  corrector, ``viterbi`` = full MLSE over ``memory`` postcursors);
* ``precode`` — 1/(1+D) mod-N precoding at the Tx, undone at the Rx. It
  terminates DFE error bursts at the cost of turning each isolated symbol error
  into two.

The link here is deliberately under-equalized (DFE off) so real postcursors
survive — MLSD has nothing to do on an already-clean eye, which is itself the
point: it buys margin where equalization is cheap, not where it is generous.

Panel 1: SER vs noise for slicer / sliding / Viterbi.
Panel 2: the measured MLSD gain against the closed-form asymptotic bound
(``mlse_gain_over_dfe_db``), plus the precoding penalty.
"""

import dataclasses as dc
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
    CdrConfig, ChannelConfig, CtleConfig, DfeConfig, MlsdConfig, RxConfig,
    SimConfig, TxConfig,
)
from halo_serdes.dsp.mlsd import mlse_gain_over_dfe_db  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)


def cfg_for(noise: float) -> LinkConfig:
    return LinkConfig(
        modulation="nrz", symbol_rate=20e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.30, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(1.0,), fir_n_pre=0),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=3.0),
                    dfe=DfeConfig(n_taps=0),          # under-equalized on purpose
                    cdr=CdrConfig(kind="bang_bang", kp_shift=7, ki_shift=14),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=150_000, seed=3, pattern="prbs13"))


def with_mlsd(cfg, kind, memory=2):
    return dc.replace(cfg, rx=dc.replace(cfg.rx,
                                         mlsd=MlsdConfig(kind=kind, memory=memory)))


noises = [0.040, 0.045, 0.050, 0.055, 0.060]
rows = {"slicer": [], "sliding": [], "viterbi": []}
resid = None
for nz in noises:
    cfg = cfg_for(nz)
    ch = ChannelModel.from_config(cfg)
    base = run_time_link(cfg, channel=ch)
    sld = run_time_link(with_mlsd(cfg, "sliding"), channel=ch)
    vit = run_time_link(with_mlsd(cfg, "viterbi"), channel=ch)
    resid = vit.extras["mlsd_resid"]
    rows["slicer"].append(max(base.ser, 1e-7))
    rows["sliding"].append(max(sld.ser, 1e-7))
    rows["viterbi"].append(max(vit.ser, 1e-7))
    print(f"noise={nz:.3f}: slicer={base.ser:.3e}  sliding={sld.ser:.3e}  "
          f"viterbi={vit.ser:.3e}  (gain {base.ser / max(vit.ser, 1e-12):.2f}x)")

cursors = np.concatenate([[1.0], np.asarray(resid)])
bound_db = mlse_gain_over_dfe_db(cursors)
print(f"\nresidual cursors {np.round(cursors, 3).tolist()}")
print(f"closed-form asymptotic MLSE gain over an IDEAL DFE: {bound_db:.2f} dB")
print("  NOTE: that bound and the measured curve use different baselines. The"
      "\n  measured gain is against the memoryless slicer (this link runs with"
      "\n  the DFE off), which pays the full ISI penalty; the closed-form number"
      "\n  is against a DFE that already cancels those postcursors perfectly."
      "\n  Small residual cursors -> little left for MLSE to win over ideal DFE,"
      "\n  yet a lot to win over a slicer that ignores them entirely.")

# precoding: cost on a clean link, benefit is burst termination
cfg = cfg_for(0.050)
ch = ChannelModel.from_config(cfg)
plain = run_time_link(cfg, channel=ch)
pre = run_time_link(dc.replace(cfg, precode=True), channel=ch)
print(f"precoding @ noise=0.050: plain SER={plain.ser:.3e} -> "
      f"precoded SER={pre.ser:.3e} ({pre.ser / max(plain.ser, 1e-12):.2f}x, "
      f"the known isolated-error doubling)")

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12.4, 4.6))
for name, style in (("slicer", "o-"), ("sliding", "s--"), ("viterbi", "^-")):
    ax0.semilogy(noises, rows[name], style, label=name)
ax0.set(xlabel="input-referred noise sigma [V]", ylabel="SER",
        title="Sequence detection on an under-equalized link (DFE off)")
ax0.grid(True, which="both", alpha=0.3)
ax0.legend()

gains = [s / v for s, v in zip(rows["slicer"], rows["viterbi"])]
ax1.plot(noises, gains, "^-", color="C0", label="Viterbi vs memoryless slicer")
ax1.axhline(1.0, color="C7", ls=":", label="no gain")
ax1.set(xlabel="input-referred noise sigma [V]", ylabel="SER improvement [x]",
        title="MLSD gain vs the slicer (grows as ISI-driven errors dominate)")
ax1.text(0.03, 0.05, f"vs an *ideal DFE* the asymptotic bound is only "
                     f"{bound_db:.2f} dB\n(different baseline: the DFE already "
                     f"cancels these cursors)",
         transform=ax1.transAxes, fontsize=7.5, color="#555")
ax1.grid(True, alpha=0.3)
ax1.legend()

fig.tight_layout()
fig.savefig(OUT / "30_mlsd_precode_link.png", dpi=130)
print(f"wrote {OUT / '30_mlsd_precode_link.png'}")
