"""TDECQ of a modelled optical transmitter: where ER, laser bandwidth and
L-I compression put it against the 802.3 limits.

TDECQ (transmitter and dispersion eye closure, quaternary) is the figure an
802.3 PAM4 optical transmitter is accepted on: how much Gaussian noise its
eye could still absorb, after a 0.5 x baud fourth-order Bessel-Thomson
reference receiver and a short reference FFE, before the symbol error ratio
at 0.45 and 0.55 UI reaches 4.8e-4 (``analysis/tdecq.py`` for the details
and their sources).

Two transmitters, each measured after its own fibre (the "D" in TDECQ):

- 100G/lambda VCSEL: 53.125 GBd, 100 m OM4, 5-tap FFE; 802.3db
  100GBASE-SR1 TDECQ max 4.4 dB (task-force baseline, Table 167-7/8).
- 200G/lambda EML: 113.4375 GBd, 500 m SMF at -1.9 ps/(nm km), 53.125 GHz
  reference receiver, 15-tap FFE with up to 3 precursors (802.3dj also adds
  a 1-tap DFE, not modelled); 200GBASE-DR1 TDECQ max 3.4 dB.

Three sweeps per transmitter: extinction ratio (more ER, less RIN per unit
OMA), laser bandwidth (VCSEL relaxation frequency / EML 3 dB bandwidth),
and L-I compression (``li_compression``: the inner levels move, R_LM falls).
The table gives where each sweep crosses its limit.
"""

import dataclasses
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.analysis.tdecq import tdecq  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, OpticalConfig, SimConfig, TopologyConfig, TxConfig,
)
from halo_serdes.engine.optical_stage import transmitter_power  # noqa: E402
from halo_serdes.optical import optical_rlm  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)
QUICK = "--quick" in sys.argv
N_SYM = 2 * 8191                       # two PRBS13Q periods


def link(baud: float, optical: OpticalConfig) -> LinkConfig:
    seg = ChannelConfig(kind="analytic", length_m=0.05, rdc=5.0, r_skin=2e-3,
                        loss_tangent=0.012, n_freq=4096)
    return LinkConfig(modulation="pam4", symbol_rate=baud, osr=16, tx=TxConfig(swing=1.0),
                      channel=ChannelConfig(kind="analytic"),
                      sim=SimConfig(n_symbols=N_SYM, seed=3, pattern="prbs13q"),
                      topology=TopologyConfig(seg_a=seg, optical=optical, seg_b=seg))


TRANSMITTERS = {
    "VCSEL 100G/λ (SR1)": dict(
        cfg=link(53.125e9, OpticalConfig(kind="vcsel_mmf", f_r_hz=22e9, damping_hz=30e9,
                                         er_db=4.0, oma_dbm=1.0, rin_db_hz=-140.0,
                                         length_m=100.0, modal_bw_mhz_km=4700.0)),
        measure=dict(n_taps=5, pre_options=(1, 2)), limit=4.4, bw_field="f_r_hz",
        bw_sweep=np.array([16, 18, 20, 22, 25, 28, 32]) * 1e9, bw_label="VCSEL f_r [GHz]"),
    "EML 200G/λ (DR1)": dict(
        cfg=link(113.4375e9, OpticalConfig(kind="eml_smf", f_r_hz=55e9, er_db=4.5, oma_dbm=2.0,
                                           rin_db_hz=-145.0, length_m=500.0,
                                           dispersion_ps_nm_km=-1.9)),
        measure=dict(n_taps=15, pre_options=(1, 2, 3), f_ref_hz=53.125e9), limit=3.4,
        bw_field="f_r_hz", bw_sweep=np.array([30, 35, 40, 45, 55, 65, 80]) * 1e9,
        bw_label="EML 3 dB bandwidth [GHz]"),
}
ER_SWEEP = np.array([2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0])
LI_SWEEP = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
if QUICK:
    ER_SWEEP, LI_SWEEP = ER_SWEEP[::3], LI_SWEEP[::2]
    for t in TRANSMITTERS.values():
        t["bw_sweep"] = t["bw_sweep"][::3]


def with_optical(cfg: LinkConfig, **kw) -> LinkConfig:
    return dataclasses.replace(cfg, topology=dataclasses.replace(
        cfg.topology, optical=dataclasses.replace(cfg.topology.optical, **kw)))


def measure(cfg: LinkConfig, kw: dict):
    power, line = transmitter_power(cfg, include_seg_a=False, through_fibre=True)
    return tdecq(power, cfg.dt, cfg.symbol_rate, line, **kw)


def crossing(x, y, limit):
    """First x where y crosses the limit (linear interpolation), or None."""
    y = np.asarray(y, float)
    ok = y <= limit
    for i in range(len(x) - 1):
        if ok[i] != ok[i + 1]:
            return float(np.interp(limit, [y[i], y[i + 1]], [x[i], x[i + 1]])
                         if y[i + 1] != y[i] else x[i])
    return None


results = {}
for name, t in TRANSMITTERS.items():
    cfg, kw = t["cfg"], t["measure"]
    t0 = time.time()
    base = measure(cfg, kw)
    print(f"{name}: TDECQ {base.tdecq_db:.2f} dB (limit {t['limit']} dB), OMA "
          f"{10 * np.log10(base.oma_outer_w * 1e3):+.2f} dBm, ER {base.er_db:.2f} dB, "
          f"R_LM {base.rlm:.3f}, C_eq {base.ceq:.2f}  [{time.time() - t0:.1f}s]")
    er = [measure(with_optical(cfg, er_db=e), kw).tdecq_db for e in ER_SWEEP]
    bw = [measure(with_optical(cfg, **{t["bw_field"]: b}), kw).tdecq_db for b in t["bw_sweep"]]
    li_meas = [measure(with_optical(cfg, li_compression=c), kw) for c in LI_SWEEP]
    li = [m.tdecq_db for m in li_meas]
    rlm_curve = [optical_rlm(with_optical(cfg, li_compression=c).topology.optical) for c in LI_SWEEP]
    results[name] = dict(er=er, bw=bw, li=li, rlm=[m.rlm for m in li_meas], rlm_curve=rlm_curve)
    for label, xs, ys in (("ER [dB]", ER_SWEEP, er), (t["bw_label"], t["bw_sweep"] / 1e9, bw),
                          ("li_compression", LI_SWEEP, li)):
        row = "  ".join(f"{x:g}:{y:.2f}" for x, y in zip(xs, ys))
        print(f"  {label:<26} {row}")
    print("  measured R_LM vs compression: "
          + "  ".join(f"{c:g}:{r:.3f}" for c, r in zip(LI_SWEEP, results[name]["rlm"])))

print()
print(f"{'transmitter':<20} {'limit':>6} {'min ER':>8} {'min bandwidth':>14} {'max compression':>16}")
for name, t in TRANSMITTERS.items():
    r = results[name]
    er_x = crossing(ER_SWEEP, r["er"], t["limit"])
    bw_x = crossing(t["bw_sweep"] / 1e9, r["bw"], t["limit"])
    li_x = crossing(LI_SWEEP, r["li"], t["limit"])

    def fmt(v, unit):
        return f"{v:.2f}{unit}" if v is not None else "no crossing"
    print(f"{name:<20} {t['limit']:5.1f}dB {fmt(er_x, ' dB'):>8} {fmt(bw_x, ' GHz'):>14} "
          f"{fmt(li_x, ''):>16}")

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
colors = {"VCSEL 100G/λ (SR1)": "C0", "EML 200G/λ (DR1)": "C3"}
for name, t in TRANSMITTERS.items():
    r, col = results[name], colors[name]
    axes[0].plot(ER_SWEEP, r["er"], "o-", color=col, label=name)
    axes[1].plot(t["bw_sweep"] / 1e9, r["bw"], "o-", color=col, label=name)
    axes[2].plot(LI_SWEEP, r["li"], "o-", color=col, label=f"{name} TDECQ")
    for ax in axes:
        ax.axhline(t["limit"], color=col, ls=":", lw=1)
for ax, xl, title in zip(axes, ["extinction ratio [dB]", "laser bandwidth [GHz]", "L-I compression"],
                         ["TDECQ vs ER (RIN per unit OMA)", "TDECQ vs laser bandwidth",
                          "TDECQ vs large-signal compression"]):
    ax.set(xlabel=xl, ylabel="TDECQ [dB]", title=title)
    ax.set_ylim(0, 8)
    ax.axhspan(0, 3.4, color="green", alpha=0.05)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
ax2 = axes[2].twinx()
for name in TRANSMITTERS:
    ax2.plot(LI_SWEEP, results[name]["rlm_curve"], "--", color=colors[name], lw=1)
ax2.set_ylabel("R_LM of the curve (dashed)")
ax2.set_ylim(0, 1.05)
fig.suptitle("Dotted lines: 802.3db SR1 4.4 dB (blue) and 802.3dj DR1 3.4 dB (red) TDECQ limits")
fig.tight_layout()
fig.savefig(OUT / "34_tdecq.png", dpi=130)
print(f"wrote {OUT / '34_tdecq.png'}")
