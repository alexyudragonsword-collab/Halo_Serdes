"""Time-domain FEXT/NEXT crosstalk injection.

The statistical engine already convolved aggressor pulse-PDFs; the time engine
had no crosstalk. Now a list of XtalkAggressor objects injects independent,
filtered aggressor data at the victim slicer node. The *same* aggressor object
drives both engines (time: xtalk=[...]; statistical: xtalk_pulses=[a.pulse()]),
so crosstalk stays inside the dual-engine cross-validation.

This sweeps aggressor coupling strength and shows the SNR/BER collapse, and
overlays the two-engine BER: they track in trend, with the statistical engine
sitting above as the conservative (worst-case-ISI) upper bound.
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

from halo_serdes.channel import ChannelModel, synthetic_aggressor  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.statistical import run_statistical  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)
_fmt = FuncFormatter(lambda v, _: f"{v:.0e}")

cfg = LinkConfig(
    modulation="nrz", symbol_rate=28e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.28, rdc=5.0,
                          r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
    tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.85, -0.05), fir_n_pre=1, rj_ui=0.004),
    rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                dfe=DfeConfig(n_taps=2), noise_rms=0.005),
    sim=SimConfig(n_symbols=200_000, seed=6, pattern="prbs13"))
cm = ChannelModel.from_config(cfg)
loss = -cm.loss_at(cfg.f_nyquist)
print(f"28 GBd NRZ, 0.28 m — loss @ Nyquist {loss:.1f} dB\n")

# one FEXT + one NEXT aggressor; sweep a common coupling level
couplings_db = [-40, -34, -30, -26, -22, -18]
td_snr, td_ber, stat_ber = [], [], []
print("== 串扰强度扫描 (FEXT+NEXT, 时域引擎) ==")
for cdb in couplings_db:
    fext = synthetic_aggressor("fext", cdb, cfg.ui, cfg.dt, seed=11)
    nxt = synthetic_aggressor("next", cdb - 3, cfg.ui, cfg.dt, seed=22)
    r = run_time_link(cfg, channel=cm, xtalk=[fext, nxt])
    td_snr.append(r.slicer_snr_db)
    td_ber.append(max(r.ber.ber, 1e-9))
    # statistical engine with the same aggressors, as pulse responses
    s = run_statistical(cfg, channel=cm,
                        xtalk_pulses=[fext.pulse(cfg.osr), nxt.pulse(cfg.osr)])
    stat_ber.append(max(s.ber, 1e-30))
    print(f"  coupling {cdb:>4} dB : SNR {r.slicer_snr_db:5.2f} dB | "
          f"time BER {r.ber.ber:.2e} | stat BER {s.ber:.2e}")

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(11, 4.6))
x = np.array(couplings_db)

ax0.plot(x, td_snr, "o-", color="C0")
ax0.set(xlabel="串扰耦合强度 [dB]", ylabel="slicer SNR [dB]",
        title=f"串扰使 SNR 塌陷\n(28 GBd NRZ, 信道 {loss:.0f} dB)")
ax0.grid(True, alpha=0.3)

ax1.semilogy(x, td_ber, "o-", color="C0", label="时域引擎 (MC)")
ax1.semilogy(x, stat_ber, "s--", color="C3", label="统计引擎 (StatEye)")
ax1.axhline(2.4e-4, color="green", ls=":", lw=1, label="KP4 pre-FEC 门限")
ax1.set(xlabel="串扰耦合强度 [dB]", ylabel="pre-FEC BER",
        title="双引擎串扰对比(统计=保守上界)\n(同一 XtalkAggressor 驱动两引擎)")
ax1.yaxis.set_major_formatter(_fmt)
ax1.legend(fontsize=8); ax1.grid(True, which="both", alpha=0.3)

fig.tight_layout()
fig.savefig(OUT / "24_crosstalk.png", dpi=130)
print(f"\nwrote {OUT / '24_crosstalk.png'}")
