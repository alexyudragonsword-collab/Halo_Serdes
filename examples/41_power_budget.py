"""What the levers cost: a per-block power estimate next to the reach studies.

The reach studies (examples 07, 19, 21, 33, 36, 38) say what each lever buys
in dB or metres; this one prices the same receivers and links in pJ/bit with
``analysis/power.py``. Every coefficient there is an estimate from public
figures (``cairn/功耗建模.md``: the Murmann ADC survey, Horowitz's 45 nm
arithmetic energies, published transceivers and measured 800G modules), so
read the ratios, not the totals.

1. Checks against published totals: a 112 Gb/s DSP transceiver, a 224 Gb/s
   ADC receiver, and the gap between retimed and linear (LPO) 800G modules.
2. The two receiver architectures of example 07, block by block.
3. The 224G long-reach levers of examples 19 / 21 / 36 / 38: ADC resolution,
   DSP depth, partial-response target length (16 to 1024 trellis states).
4. LPO / CPO / retimed (example 33's links), per link direction.
5. Sensitivity: the comparisons above under the ends of each coefficient's
   range, to see which conclusions survive the uncertainty.
"""

import dataclasses
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.analysis.power import PowerCoefficients, power_report, trellis_states  # noqa: E402
from halo_serdes.config import LinkConfig, PrConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, CtleConfig, DfeConfig, FfeConfig, MlsdConfig, OpticalConfig,
    RxConfig, TopologyConfig, TxConfig,
)

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)
K = PowerCoefficients()


def adc_rx(enob, n_pre, n_post, n_dfe=0, mlsd="none", memory=1) -> RxConfig:
    return RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=enob, fullscale=0.6),
                    ffe=FfeConfig(n_pre=n_pre, n_post=n_post, adapt="lms"),
                    dfe=DfeConfig(n_taps=n_dfe, adapt="lms" if n_dfe else "none"),
                    mlsd=MlsdConfig(kind=mlsd, memory=memory),
                    cdr=CdrConfig(kind="mueller_muller"))


def pam4(baud, rx, **kw) -> LinkConfig:
    return LinkConfig(modulation="pam4", symbol_rate=baud, rx=rx, **kw)


print("Power model (analysis/power.py): coefficients are public-figure estimates; read ratios")

# ---- 1. checks against published totals --------------------------------
print("\n== 1. checks against published totals")
xcvr = power_report(pam4(56e9, adc_rx(5.5, 4, 10, n_dfe=1)), K)
print(f"  112 Gb/s DSP transceiver (56 GBd, ENOB 5.5, 15-tap FFE + 1-tap DFE): "
      f"model {xcvr.pj_per_bit:.2f} pJ/b, published 4.63 (5 nm)")
rx224 = power_report(pam4(112e9, adc_rx(4.5, 4, 10, n_dfe=1)), K)
print(f"  224 Gb/s ADC receiver (112 GBd, ENOB 4.5, 15-tap FFE + 1-tap DFE): "
      f"model {rx224.pj('host.rx'):.2f} pJ/b, published 1.41")


def topo_cfg(retimer: str, enob: float = 5.5, rx_common=None) -> LinkConfig:
    """Example 33's link (53.125 GBd PAM4, VCSEL + OM4, 17-tap ADC receivers),
    its ideal 10-bit quantisers priced at ``enob``."""
    rx = adc_rx(enob, 4, 12)
    return pam4(53.125e9, rx, tx=TxConfig(swing=1.0), topology=TopologyConfig(
        retimer=retimer, retimer_rx=rx, retimer_tx=TxConfig(swing=1.0),
        optical=OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0)))


lpo = power_report(topo_cfg("none"), K)
ret = power_report(topo_cfg("both"), K)
print(f"  retimed minus LPO (53.125 GBd, two retimers, ENOB 5.5, 17-tap FFE): "
      f"model {ret.pj_per_bit - lpo.pj_per_bit:.2f} pJ/b, measured 800G modules 7.4-8.5")

# ---- 2. the two receiver architectures ----------------------------------
print("\n== 2. receivers of example 07 (106.25 GBd PAM4), pJ/b")
MS_RX = RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=9.0),
                 dfe=DfeConfig(n_taps=3, adapt="sign_sign"), cdr=CdrConfig(kind="bang_bang"))
MS_RX_U = dataclasses.replace(MS_RX, dfe=DfeConfig(n_taps=3, adapt="sign_sign",
                                                   tap1_mode="unrolled"))
ADC_RX = RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                  adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                  ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms"),
                  dfe=DfeConfig(n_taps=1, adapt="lms"), cdr=CdrConfig(kind="mueller_muller"))
ARCH = {"mixed-signal (3-tap DFE)": MS_RX, "mixed-signal, tap 1 unrolled": MS_RX_U,
        "ADC (ENOB 6.5, 15+1 taps)": ADC_RX}
arch_rep = {name: power_report(pam4(106.25e9, rx), K) for name, rx in ARCH.items()}
blocks = ["common", "comparators", "dfe", "adc", "ffe"]
print(f"  {'receiver':<30}" + "".join(f"{b:>12}" for b in blocks) + f"{'receiver':>10}")
for name, r in arch_rep.items():
    print(f"  {name:<30}" + "".join(f"{r.pj('host.rx.' + b):12.2f}" for b in blocks)
          + f"{r.pj('host.rx'):10.2f}")
ms_pj = arch_rep["mixed-signal (3-tap DFE)"].pj("host.rx")
adc_pj = arch_rep["ADC (ENOB 6.5, 15+1 taps)"].pj("host.rx")
print(f"  ADC receiver / mixed-signal receiver: {adc_pj / ms_pj:.2f}x "
      f"(the ADC alone {arch_rep['ADC (ENOB 6.5, 15+1 taps)'].pj('host.rx.adc'):.2f} pJ/b)")

# ---- 3. 224G long-reach levers ------------------------------------------
print("\n== 3. 224G LR receivers (112 GBd PAM4), pJ/b")
LEVERS = {
    # examples 18 / 19 / 36's control: 21-tap FFE, memory-2 Viterbi
    "FFE21 + Viterbi mem2 (ex 19, 36 control)": (adc_rx(6.5, 6, 14, 0, "viterbi", 2), PrConfig()),
    "FFE11 + DFE8 + Viterbi mem3 (ex 19, 21)": (adc_rx(6.5, 6, 4, 8, "viterbi", 3), PrConfig()),
    "same, ADC ENOB 7.5 (ex 19, 21)": (adc_rx(7.5, 6, 4, 8, "viterbi", 3), PrConfig()),
    "RX PR 1 + aD, mem2 (ex 36)": (adc_rx(6.5, 6, 14, 0, "viterbi", 2), PrConfig(target=(1.0, 0.75))),
    "1 + aD + bD^2, mem2 (ex 38)": (adc_rx(6.5, 6, 14, 0, "viterbi", 2),
                                    PrConfig(target=(1.0, 0.5, 0.0))),
    "+ cD^3, mem1 (ex 38)": (adc_rx(6.5, 6, 14, 0, "viterbi", 1),
                             PrConfig(target=(1.0, 0.5, 0.0, 0.0))),
    "+ cD^3, mem2 (ex 38)": (adc_rx(6.5, 6, 14, 0, "viterbi", 2),
                             PrConfig(target=(1.0, 0.5, 0.0, 0.0))),
}
lever_rep = {}
print(f"  {'receiver':<42}{'states':>7}{'ADC':>8}{'FFE+DFE':>9}{'Viterbi':>9}{'receiver':>10}{'vs first':>10}")
for name, (rx, pr) in LEVERS.items():
    cfg = pam4(112e9, rx, pr=pr)
    r = lever_rep[name] = power_report(cfg, K)
    base = lever_rep[next(iter(LEVERS))].pj("host.rx")
    print(f"  {name:<42}{trellis_states(cfg):7d}{r.pj('host.rx.adc'):8.2f}"
          f"{r.pj('host.rx.ffe') + r.pj('host.rx.dfe'):9.2f}{r.pj('host.rx.mlsd'):9.2f}"
          f"{r.pj('host.rx'):10.2f}{r.pj('host.rx') - base:+10.2f}")
v = [lever_rep[n].pj("host.rx.mlsd") for n in LEVERS]
print(f"  Viterbi per controlled cursor: x{v[3] / v[0]:.2f} (16 -> 64 states), "
      f"x{v[4] / v[3]:.2f} (64 -> 256)")

# ---- 4. LPO / CPO / retimed ---------------------------------------------
print("\n== 4. example 33's links (53.125 GBd PAM4, ADC ENOB 5.5), per link direction, pJ/b")
TOPOS = {"LPO": topo_cfg("none"), "CPO": topo_cfg("none"), "DSP retimed": topo_cfg("both")}
topo_rep = {name: power_report(cfg, K) for name, cfg in TOPOS.items()}
print(f"  {'topology':<14}{'host TX+RX':>12}{'optics':>9}{'retimers':>10}{'total':>9}")
for name, r in topo_rep.items():
    print(f"  {name:<14}{r.pj('host'):12.2f}{r.pj('optics'):9.2f}{r.pj('retimer'):10.2f}"
          f"{r.pj_per_bit:9.2f}")
print("  LPO and CPO price the same here: the model has no term for what CPO removes "
      "(driving the host trace, the module's own overhead)")

# ---- 5. sensitivity -----------------------------------------------------
print("\n== 5. sensitivity: the same comparisons at the ends of each coefficient's range")
VARIANTS = {
    "default": K,
    "ADC FOM 38 fJ": dataclasses.replace(K, adc_fom_walden_j=38e-15),
    "ADC FOM 120 fJ": dataclasses.replace(K, adc_fom_walden_j=120e-15),
    "ADC Schreier 146 dB": dataclasses.replace(K, adc_fom="schreier"),
    "ADC Schreier 151 dB": dataclasses.replace(K, adc_fom="schreier", adc_fom_schreier_db=151.0),
    "digital x0.5": dataclasses.replace(K, digital_overhead=0.5),
    "digital x3": dataclasses.replace(K, digital_overhead=3.0),
    "comparator x3": dataclasses.replace(K, comparator_j=300e-15, analog_dfe_tap_j=300e-15),
    "RX common 0.57 pJ/b": dataclasses.replace(K, rx_common_j_per_bit=0.57e-12),
}
names = list(LEVERS)
print(f"  {'coefficients':<22}{'ADC/MS':>8}{'ENOB+1':>9}{'PR 16->64':>11}{'64->256':>9}"
      f"{'ret-LPO':>9}{'ret/LPO':>9}")
for vname, k in VARIANTS.items():
    def rx_pj(name, k=k):
        rx, pr = LEVERS[name]
        return power_report(pam4(112e9, rx, pr=pr), k).pj("host.rx")
    a = power_report(pam4(106.25e9, ADC_RX), k).pj("host.rx")
    m = power_report(pam4(106.25e9, MS_RX), k).pj("host.rx")
    lp = power_report(topo_cfg("none"), k).pj_per_bit
    rt = power_report(topo_cfg("both"), k).pj_per_bit
    print(f"  {vname:<22}{a / m:8.2f}{rx_pj(names[2]) - rx_pj(names[1]):+9.2f}"
          f"{rx_pj(names[3]) - rx_pj(names[0]):+11.2f}{rx_pj(names[4]) - rx_pj(names[3]):+9.2f}"
          f"{rt - lp:+9.2f}{rt / lp:9.2f}")

# ---- plot ---------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
parts = [("common", "host.rx.common"), ("ADC", "host.rx.adc"), ("FFE + DFE", ("host.rx.ffe", "host.rx.dfe")),
         ("Viterbi", "host.rx.mlsd")]
labels = [n.split(" (")[0] for n in LEVERS]
bottom = [0.0] * len(LEVERS)
for label, key in parts:
    keys = key if isinstance(key, tuple) else (key,)
    vals = [sum(lever_rep[n].pj(kk) for kk in keys) for n in LEVERS]
    axes[0].bar(range(len(vals)), vals, bottom=bottom, label=label)
    bottom = [b + x for b, x in zip(bottom, vals)]
axes[0].set_xticks(range(len(labels)), labels, rotation=30, ha="right", fontsize=7)
axes[0].set(ylabel="receiver [pJ/b]", title="224G LR receivers (estimate)")
axes[0].set_yscale("log")
axes[0].legend(fontsize=8)
bottom = [0.0] * len(TOPOS)
for label, key in (("host TX + RX", "host"), ("optics", "optics"), ("retimers", "retimer")):
    vals = [topo_rep[n].pj(key) for n in TOPOS]
    axes[1].bar(range(len(vals)), vals, bottom=bottom, label=label)
    bottom = [b + x for b, x in zip(bottom, vals)]
axes[1].set_xticks(range(len(TOPOS)), list(TOPOS))
axes[1].set(ylabel="per link direction [pJ/b]", title="LPO / CPO / retimed (estimate)")
axes[1].legend(fontsize=8)
for ax in axes:
    ax.grid(True, alpha=0.3, axis="y")
fig.tight_layout()
fig.savefig(OUT / "41_power_budget.png", dpi=120)
print("wrote examples/output/41_power_budget.png")
