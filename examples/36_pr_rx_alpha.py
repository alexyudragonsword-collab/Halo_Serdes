"""224G long-reach: equalise to 1 + aD instead of a delta, then let Viterbi resolve a.

Example 18 found that MLSD behind a 21-tap MMSE FFE adds nothing: the FFE
inverts the channel down to a delta and leaves the trellis no ISI to work
over, at the price of the noise it enhances doing so. A partial-response
target (``pr.target = (1.0, a)``) asks the FFE for less -- the first
postcursor stays at ``a`` -- so it enhances less noise, and the Viterbi
detector resolves the controlled cursor it left.

Four parts, all on example 18's receiver (21-tap LMS FFE, MM-CDR, memory-2
Viterbi, ENOB 6.5, 1.5 mV receiver noise):

1. BER vs a at three losses. The control is a = 0: the same Viterbi
   detector behind a delta-target FFE (example 18's 21-tap row).
2. Reach (post-KP4 1e-15) of the control and of each a, across a length
   sweep; the best a against the control is the table that answers whether
   receive PR is worth it.
3. a = 1 with and without 1/(1+D) precoding: the per-symbol decisions the
   LMS and CDR use propagate errors without it; the BER after Viterbi is
   what the precoding changes.
4. a chosen by the receiver (``pr.adapt = "lms"``): the monic MMSE target of
   the start-up pulse, then tracked by LMS with the FFE -- does it land
   where the best fixed a of part 2 is?

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

ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
LOSS_LENGTHS = (0.19, 0.22, 0.25)            # part 1: three losses
LENGTHS = [0.18, 0.20, 0.22, 0.24, 0.26, 0.28]
if QUICK:
    LENGTHS = LENGTHS[::2]


def make_cfg(length_m: float, alpha: float, precode: bool = False,
             n_sym: int = N_SYM, adapt: str = "none") -> LinkConfig:
    """Example 18's 21-tap receiver, a memory-2 Viterbi detector, and the
    receive target ``(1, alpha)`` (``alpha`` 0 is the delta target)."""
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16, precode=precode,
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
        pr=PrConfig(target=(1.0,) if alpha == 0.0 else (1.0, alpha), adapt=adapt),
    )


def run(length_m: float, alpha: float, precode: bool = False) -> float:
    res = run_time_link(make_cfg(length_m, alpha, precode))
    return max(res.ber.ber, 0.5 / res.ber.n_checked)   # 0 errors -> half a count


def kp4_threshold(target: float = 1e-15) -> float:
    lo, hi = -8.0, -2.0                       # log10 pre-FEC BER
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if pre_to_post_fec_ber(10 ** mid, "kp4") > target:
            hi = mid
        else:
            lo = mid
    return 10 ** lo


def loss_db(length_m: float) -> float:
    return -ChannelModel.from_config(make_cfg(length_m, 0.0, n_sym=1000)).loss_at(56e9)


# --------------------------------------------------- 1. BER vs alpha --------
print("224 Gb/s PAM4 LR, 21-tap LMS FFE + memory-2 Viterbi; receive target (1, a)")
part1 = {}
for L in LOSS_LENGTHS:
    t0 = time.time()
    part1[L] = np.array([run(L, a) for a in ALPHAS])
    best = ALPHAS[int(np.argmin(part1[L]))]
    print(f"  {loss_db(L):5.1f} dB  " + "  ".join(f"a={a:<4g} {b:8.1e}" for a, b in zip(ALPHAS, part1[L]))
          + f"   best a = {best:g}  [{time.time() - t0:.0f}s]")

# ------------------------------------------------------------ 2. reach -------
P_STAR = kp4_threshold()
il = np.array([loss_db(L) for L in LENGTHS])
print(f"\nreach: post-KP4 1e-15 <=> pre-FEC BER <= {P_STAR:.2e}")
sweep = {}
for a in ALPHAS:
    t0 = time.time()
    sweep[a] = np.array([run(L, a) for L in LENGTHS])
    print(f"  a={a:<4g} " + " ".join(f"{x:4.1f}dB:{b:8.1e}" for x, b in zip(il, sweep[a]))
          + f"  [{time.time() - t0:.0f}s]")


def reach_db(b: np.ndarray) -> float:
    lb, lt = np.log10(b), np.log10(P_STAR)
    for k in range(len(il) - 1):
        if lb[k] <= lt < lb[k + 1]:
            return float(il[k] + (lt - lb[k]) / (lb[k + 1] - lb[k]) * (il[k + 1] - il[k]))
    return float(il[-1]) if lb[-1] <= lt else float(il[0])   # clipped to the sweep


reach = {a: reach_db(sweep[a]) for a in ALPHAS}
a_best = max(ALPHAS[1:], key=lambda a: reach[a])
print("\nreach [dB @ 56 GHz]:")
for a in ALPHAS:
    tag = "  (control: delta target + Viterbi)" if a == 0.0 else ("  <- best" if a == a_best else "")
    print(f"  a={a:<4g} {reach[a]:6.2f} dB  {reach[a] - reach[0.0]:+5.2f} dB{tag}")

# ---------------------------------------------------- 3. precoding at a=1 ----
L_pc = LOSS_LENGTHS[1]
b_plain, b_pre = run(L_pc, 1.0, False), run(L_pc, 1.0, True)
print(f"\na = 1 at {loss_db(L_pc):.1f} dB: plain {b_plain:.2e}, precoded {b_pre:.2e}")

# ------------------------------------------------- 4. a chosen by the receiver --
print("\nreceiver-chosen a (pr.adapt = 'lms': MMSE start, LMS-tracked):")
ber_ad, a_ad = [], []
for x, L in zip(il, LENGTHS):
    res = run_time_link(make_cfg(L, a_best, adapt="lms"))
    ber_ad.append(max(res.ber.ber, 0.5 / res.ber.n_checked))
    a_ad.append(res.extras["pr_alpha"])
    print(f"  {x:4.1f} dB  a {a_ad[-1][0]:.3f} -> {a_ad[-1][1]:.3f}  BER {ber_ad[-1]:8.1e}")
ber_ad = np.array(ber_ad)
reach_ad = reach_db(ber_ad)
print(f"  reach {reach_ad:6.2f} dB  ({reach_ad - reach[a_best]:+.2f} dB vs the best fixed a = {a_best:g})")

# direction, as measured (cairn/DSP发端与PR.md has the numbers): on the
# deepest loss some a > 0 beats the delta target, the best a buys reach, and
# the receiver-chosen a gets within half a dB of the best fixed one
deep = part1[LOSS_LENGTHS[-1]]
assert deep[1:].min() < deep[0], deep
assert reach[a_best] > reach[0.0], reach
assert reach_ad > reach[a_best] - 0.5, (reach_ad, reach[a_best])

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
ax = axes[0]
for L in LOSS_LENGTHS:
    ax.semilogy(ALPHAS, part1[L], "o-", label=f"{loss_db(L):.1f} dB")
ax.axhline(P_STAR, color="r", ls="--", lw=1, label=f"KP4 1e-15 ({P_STAR:.1e})")
ax.set(xlabel="target a in (1, a)", ylabel="pre-FEC BER after Viterbi",
       title="BER vs receive target (a = 0: delta target + Viterbi)")
ax.legend(fontsize=8)
ax.grid(True, which="both", alpha=0.3)
ax = axes[1]
for a in ALPHAS:
    ax.semilogy(il, sweep[a], "o-" if a else "s--k", label=f"a = {a:g}" + (" (control)" if a == 0 else ""))
ax.semilogy(il, ber_ad, "^:k", label="a chosen by the receiver (MMSE + LMS)")
ax.axhline(P_STAR, color="r", ls="--", lw=1)
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="pre-FEC BER after Viterbi",
       title=f"reach: control {reach[0.0]:.1f} dB, a = {a_best:g} {reach[a_best]:.1f} dB")
ax.legend(fontsize=8)
ax.grid(True, which="both", alpha=0.3)
fig.tight_layout()
out = OUT / "36_pr_rx_alpha.png"
fig.savefig(out, dpi=130)
print(f"wrote {out}")
