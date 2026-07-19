"""IEEE 802.3 Channel Operating Margin (COM), Clause 93A / 178A.

The faithful COM method (analysis/com.py), distinct from the transparent RSS
figure of merit in NativeCom:

1. the equalizer is optimized over a CTLE peaking grid, the DFE taps are derived
   from the cursors with a b_max bound, and the (CTLE, phase) that maximizes the
   figure of merit is selected;
2. the noise amplitude A_ni is read off the *convolved* interference-plus-noise
   PDF (residual ISI ⊗ crosstalk ⊗ Gaussian ⊗ dual-Dirac jitter) at the target
   DER — not a Gaussian RSS;
3. COM = 20 log10(A_s / A_ni).

Left: COM vs channel loss, clean vs with two crosstalk aggressors, with the
~3 dB pass line. Right: the noise-budget breakdown (σ contributions) at one
operating point, and the optimized CTLE setting is printed.
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

from halo_serdes.analysis.com import compute_com  # noqa: E402
from halo_serdes.channel import ChannelModel, synthetic_aggressor  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig, ChannelConfig,
)

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)


def cfg_for(length_m: float) -> LinkConfig:
    return LinkConfig(
        modulation="pam4", symbol_rate=53.125e9, osr=32,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.1, 1.0, -0.15), fir_n_pre=1,
                    rj_ui=0.006, dcd_ui=0.01),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                    dfe=DfeConfig(n_taps=1), noise_rms=0.002),
        sim=SimConfig(n_symbols=1000, seed=1, pattern="prbs13q"))


def aggressors(cfg):
    return [synthetic_aggressor("fext", -30, cfg.ui, cfg.dt, modulation="pam4",
                                seed=7).pulse(cfg.osr),
            synthetic_aggressor("next", -32, cfg.ui, cfg.dt, modulation="pam4",
                                seed=8).pulse(cfg.osr)]


lengths = np.linspace(0.08, 0.34, 10)
loss, com_clean, com_xt = [], [], []
for L in lengths:
    cfg = cfg_for(float(L))
    ch = ChannelModel.from_config(cfg)
    loss.append(-ch.loss_at(cfg.f_nyquist))
    com_clean.append(compute_com(ch, cfg).com_db)
    com_xt.append(compute_com(ch, cfg, xtalk_pulses=aggressors(cfg)).com_db)

# detailed operating point (mid channel, with crosstalk)
cfg = cfg_for(0.20)
ch = ChannelModel.from_config(cfg)
r = compute_com(ch, cfg, xtalk_pulses=aggressors(cfg))
print(f"@ IL={-ch.loss_at(cfg.f_nyquist):.1f} dB:  {r.summary()}")
print(f"   optimized CTLE peaking = {r.detail['ctle_peak_db']} dB, "
      f"sample phase = {r.detail['sample_phase']}, "
      f"DFE taps = {r.detail['n_dfe']}, FOM = {r.fom_db:.1f} dB")

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12.5, 4.6))
ax0.plot(loss, com_clean, "o-", label="clean", color="C0")
ax0.plot(loss, com_xt, "s--", label="+FEXT/NEXT", color="C3")
ax0.axhline(3.0, color="C2", ls=":", label="≈3 dB pass")
ax0.set(xlabel="channel insertion loss @ Nyquist [dB]", ylabel="COM [dB]",
        title="IEEE 802.3 COM vs loss (PAM4 106G, CTLE+1-DFE)")
ax0.grid(True, alpha=0.3)
ax0.legend()

labels = ["σ_ISI", "σ_XT", "σ_N", "σ_J"]
vals = [r.fom_isi, r.fom_xtalk, r.fom_noise, r.fom_jitter]
ax1.bar(labels, vals, color=["C0", "C3", "C1", "C4"])
ax1.set(ylabel="σ contribution [V rms]",
        title=f"Noise budget @ IL={-ch.loss_at(cfg.f_nyquist):.0f} dB  "
              f"(COM={r.com_db:.1f} dB)")
ax1.grid(True, axis="y", alpha=0.3)

fig.tight_layout()
fig.savefig(OUT / "26_com_802p3.png", dpi=130)
print(f"wrote {OUT / '26_com_802p3.png'}")
