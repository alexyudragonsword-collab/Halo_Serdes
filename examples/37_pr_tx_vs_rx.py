"""224G long-reach: partial response in the transmitter, in the receiver, or not at all.

Example 36 found that equalising to 1 + aD at the receiver and letting
Viterbi resolve a buys ~4.7 dB of reach over a delta target with the same
Viterbi. This example puts the same 1 + aD in the transmitter instead
(``pr.at = "tx"``), before the Tx FFE and the DAC, scaled by 1 / (1 + a) so
the Tx peak swing is unchanged, and lines the three up on example 18's
channel and receiver (21-tap LMS FFE, MM-CDR, memory-2 Viterbi, ENOB 6.5,
1.5 mV):

1. control: delta target + Viterbi (no PR);
2. RX PR at a = 0.75 (example 36's best);
3. TX PR at a in {0.25, 0.5, 0.75, 1}, peak-normalised.

What to expect from a linear chain with the noise at the receiver: shaping
in the Tx does not change what the receive FFE has to invert (the channel,
down to a delta), so TX PR does not get the noise-enhancement saving RX PR
gets -- it is the control's equaliser with the control's noise -- and under
a peak limit it pays 20 log10(1 + a) in amplitude on top. The reach table
prints that prediction, control - 20 log10(1 + a), next to each TX row.

(Lifting the peak limit is not a clean experiment in this receiver: a bigger
swing needs a bigger ADC full scale, and the ENOB noise scales with it.)

Pass ``--quick`` for a smoke run (fewer symbols, half the sweep).
"""

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

from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.config import LinkConfig, PrConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig,
    MlsdConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.fec import pre_to_post_fec_ber  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

QUICK = "--quick" in sys.argv
N_SYM = 100_000 if QUICK else 400_000

LENGTHS = [0.14, 0.16, 0.18, 0.20, 0.22, 0.24, 0.26]
if QUICK:
    LENGTHS = LENGTHS[::2]
A_RX = 0.75
A_TX = (0.25, 0.5, 0.75, 1.0)


def make_cfg(length_m: float, alpha: float = 0.0, at: str = "rx",
             n_sym: int = N_SYM) -> LinkConfig:
    """Example 18's receiver with a memory-2 Viterbi detector and the target
    (1, alpha) shaped at ``at``."""
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,  # 224 Gb/s
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1,
                    clock=ClockConfig(rj_ui=0.004)),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=14, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    mlsd=MlsdConfig(kind="viterbi", memory=2),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"),
        pr=PrConfig(target=(1.0,) if alpha == 0.0 else (1.0, alpha), at=at),
    )


def kp4_threshold(target: float = 1e-15) -> float:
    lo, hi = -8.0, -2.0                       # log10 pre-FEC BER
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if pre_to_post_fec_ber(10 ** mid, "kp4") > target:
            hi = mid
        else:
            lo = mid
    return 10 ** lo


P_STAR = kp4_threshold()
il = np.array([-ChannelModel.from_config(make_cfg(L, n_sym=1000)).loss_at(56e9) for L in LENGTHS])

CASES = {"control (delta + Viterbi)": dict(alpha=0.0),
         f"RX PR a={A_RX:g}": dict(alpha=A_RX, at="rx")}
for a in A_TX:
    CASES[f"TX PR a={a:g}"] = dict(alpha=a, at="tx")

print("224 Gb/s PAM4 LR, 21-tap LMS FFE + memory-2 Viterbi: PR at the Tx, at the Rx, or none")
print(f"reach: post-KP4 1e-15 <=> pre-FEC BER <= {P_STAR:.2e}")
sweep, snr = {}, {}
for name, kw in CASES.items():
    t0 = time.time()
    rows = [run_time_link(make_cfg(L, **kw)) for L in LENGTHS]
    sweep[name] = np.array([max(r.ber.ber, 0.5 / r.ber.n_checked) for r in rows])
    snr[name] = np.array([r.slicer_snr_db for r in rows])
    print(f"  {name:<28} " + " ".join(f"{x:4.1f}dB:{b:8.1e}" for x, b in zip(il, sweep[name]))
          + f"  [{time.time() - t0:.0f}s]")


def reach_db(b: np.ndarray) -> float:
    lb, lt = np.log10(b), np.log10(P_STAR)
    for k in range(len(il) - 1):
        if lb[k] <= lt < lb[k + 1]:
            return float(il[k] + (lt - lb[k]) / (lb[k + 1] - lb[k]) * (il[k + 1] - il[k]))
    return float(il[-1]) if lb[-1] <= lt else float(il[0])   # clipped to the sweep


reach = {name: reach_db(b) for name, b in sweep.items()}
ctrl = reach["control (delta + Viterbi)"]
print("\nreach [dB @ 56 GHz]:                  vs control   control - 20 log10(1 + a)")
for name, v in reach.items():
    a = CASES[name]["alpha"]
    pred = (f"{ctrl - 20 * np.log10(1 + a):6.2f} dB" if CASES[name].get("at") == "tx" else "")
    print(f"  {name:<28} {v:6.2f} dB  {v - ctrl:+5.2f} dB   {pred}")

k_mid = len(LENGTHS) // 2
print(f"\nslicer SNR at {il[k_mid]:.1f} dB:")
for name in CASES:
    print(f"  {name:<28} {snr[name][k_mid]:5.1f} dB")

# direction, as measured (cairn/DSP发端与PR.md §8 has the numbers): receive
# PR beats every transmit PR, and transmit PR tracks the control less its
# peak cost
best_tx = max(reach[f"TX PR a={a:g}"] for a in A_TX)
assert reach[f"RX PR a={A_RX:g}"] > best_tx > ctrl - 3.0, reach
for a in A_TX[:3]:
    assert abs(reach[f"TX PR a={a:g}"] - (ctrl - 20 * np.log10(1 + a))) < 1.5, reach

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
ax = axes[0]
styles = {"control (delta + Viterbi)": "s--k", f"RX PR a={A_RX:g}": "o-C0"}
for name, b in sweep.items():
    ax.semilogy(il, b, styles.get(name, "o-"), label=name, alpha=1.0 if name in styles else 0.7)
ax.axhline(P_STAR, color="r", ls="--", lw=1)
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="pre-FEC BER after Viterbi",
       title="PR at the Tx vs at the Rx (224 Gb/s PAM4)")
ax.legend(fontsize=7.5)
ax.grid(True, which="both", alpha=0.3)
ax = axes[1]
names = list(reach)
ax.barh(range(len(names)), [reach[n] for n in names], color=["0.4", "C0"] + ["C1"] * len(A_TX))
ax.set_yticks(range(len(names)), names, fontsize=8)
ax.axvline(ctrl, color="k", ls="--", lw=1)
ax.set(xlabel="reach [dB @ 56 GHz], post-KP4 1e-15", title="reach")
ax.grid(True, axis="x", alpha=0.3)
fig.tight_layout()
out = OUT / "37_pr_tx_vs_rx.png"
fig.savefig(out, dpi=130)
print(f"wrote {out}")
