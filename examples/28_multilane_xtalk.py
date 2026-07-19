"""Multi-lane crosstalk: margin vs number of aggressors.

A realistic package has many coupled lanes. This sweeps the number of FEXT+NEXT
aggressors and shows two things degrade together as lanes are added:

* the integrated crosstalk noise (ICN, the behavioral MDFEXT/MDNEXT power sum),
  which grows ~sqrt(N) as independent aggressors add in power;
* the faithful 802.3 COM (the same aggressor bank flows straight into the COM
  engine as σ_XT), which falls as the crosstalk floor rises.

One aggressor bank drives both — the unified XtalkAggressor object.
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
from halo_serdes.channel import ChannelModel, aggressor_bank, icn_rms  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

cfg = LinkConfig(
    modulation="pam4", symbol_rate=53.125e9, osr=32,
    channel=ChannelConfig(kind="analytic", length_m=0.16, rdc=5.0, r_skin=2e-3,
                          loss_tangent=0.012, n_freq=8192),
    tx=TxConfig(swing=1.0, fir_taps=(-0.1, 1.0, -0.15), fir_n_pre=1, rj_ui=0.005),
    rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                dfe=DfeConfig(n_taps=1), noise_rms=0.002),
    sim=SimConfig(n_symbols=1000, seed=1, pattern="prbs13q"))
ch = ChannelModel.from_config(cfg)
il = -ch.loss_at(cfg.f_nyquist)
print(f"channel IL @ Nyquist = {il:.1f} dB")

counts = [0, 1, 2, 4, 6, 8, 12]
icn, com = [], []
for nlane in counts:
    if nlane == 0:
        bank = []
    else:
        nf = (nlane + 1) // 2
        bank = aggressor_bank(nf, nlane - nf, -30.0, cfg.ui, cfg.dt,
                              modulation="pam4", base_seed=1)
    pulses = [a.pulse(cfg.osr) for a in bank]
    icn.append(icn_rms(bank, cfg.osr, modulation="pam4") * 1e3)   # mV
    com.append(compute_com(ch, cfg, xtalk_pulses=pulses).com_db)
    print(f"  {nlane:2d} aggressors: ICN={icn[-1]:6.2f} mV   COM={com[-1]:6.2f} dB")

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12.2, 4.6))
ax0.plot(counts, icn, "o-", color="C3")
# sqrt(N) reference anchored at the first nonzero point
ref_n = np.array(counts[1:], dtype=float)
ax0.plot(ref_n, icn[1] * np.sqrt(ref_n / ref_n[0]), "k--", alpha=0.5,
         label="~sqrt(N)")
ax0.set(xlabel="number of aggressor lanes", ylabel="ICN [mV rms]",
        title="Integrated crosstalk noise vs lanes")
ax0.grid(True, alpha=0.3)
ax0.legend()

ax1.plot(counts, com, "s-", color="C0")
ax1.axhline(3.0, color="C2", ls=":", label="≈3 dB pass")
ax1.set(xlabel="number of aggressor lanes", ylabel="COM [dB]",
        title=f"802.3 COM vs lanes (IL={il:.0f} dB)")
ax1.grid(True, alpha=0.3)
ax1.legend()

fig.tight_layout()
fig.savefig(OUT / "28_multilane_xtalk.png", dpi=130)
print(f"wrote {OUT / '28_multilane_xtalk.png'}")
