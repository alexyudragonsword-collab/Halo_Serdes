"""224G long-reach: a second controlled cursor, 1 + aD + bD^2.

Example 36 left the FFE one controlled cursor (1 + aD) and Viterbi resolved
it: +5.0 dB of reach over the delta target. On a lossy channel the pulse has
a long tail, so the next step is to leave the FFE a second one -- the target
(1, a, b), up to EPR4's 1 + 2D + D^2 -- at the price of a trellis N times
bigger (memory 2 behind the target: 256 states for PAM4).

Both targets are the receiver's own choice (``pr.adapt = "mmse"``: the monic
target that minimises the FFE's mean-square error for the start-up pulse),
so the comparison is not of hand-picked numbers; the last row lets LMS track
(a, b) from that start with the FFE. Same receiver as examples
18 / 36 (21-tap LMS FFE, MM-CDR, memory-2 Viterbi, ENOB 6.5, 1.5 mV):

  control  -- delta target + Viterbi
  1 + aD   -- MMSE a
  1 + aD + bD^2 -- MMSE (a, b)

Reach is where the pre-FEC BER crosses KP4's threshold (post-KP4 1e-15),
the length sweep refined to 0.4 dB around it (``fec.refine``).

Pass ``--quick`` for a smoke run (fewer symbols, half the sweep).
"""

import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

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
from halo_serdes.fec import fec_threshold, refine  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

QUICK = "--quick" in sys.argv
N_SYM = 100_000 if QUICK else 400_000
LENGTHS = [0.18, 0.20, 0.22, 0.24, 0.26, 0.28, 0.30]
if QUICK:
    LENGTHS = LENGTHS[::2]

CASES = {"control (delta + Viterbi)": PrConfig(),
         "1 + aD (MMSE a)": PrConfig(target=(1.0, 0.5), adapt="mmse"),
         "1 + aD + bD^2 (MMSE a, b)": PrConfig(target=(1.0, 0.5, 0.0), adapt="mmse"),
         "1 + aD + bD^2 (LMS-tracked)": PrConfig(target=(1.0, 0.5, 0.0), adapt="lms")}


def make_cfg(length_m: float, pr: PrConfig, n_sym: int = N_SYM) -> LinkConfig:
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
        pr=pr,
    )


P_STAR = fec_threshold("kp4")


def loss_db(length_m: float) -> float:
    return -ChannelModel.from_config(make_cfg(length_m, PrConfig(), 1000)).loss_at(56e9)


print("224 Gb/s PAM4 LR, 21-tap LMS FFE + memory-2 Viterbi: delta, 1 + aD, 1 + aD + bD^2")
print(f"reach: post-KP4 1e-15 <=> pre-FEC BER <= {P_STAR:.2e}")
sweep, targets, reach = {}, {}, {}
for name, pr in CASES.items():
    t0 = time.time()
    shown, targets[name] = {}, {}

    def measure(L, pr=pr, shown=shown, tg=targets[name]):
        r = run_time_link(make_cfg(L, pr))
        shown[L] = (loss_db(L), max(r.ber.ber, 0.5 / r.ber.n_checked))   # no errors: half a count
        tg[L] = r.extras["pr_target"]
        return shown[L][0], r.ber.ber

    # the length sweep refined to 0.4 dB at the KP4 crossing
    r = refine({L: measure(L) for L in LENGTHS}, measure, P_STAR, tol=0.4)
    sweep[name] = shown
    reach[name] = r.value if r.value is not None else r.hi if r.note == "below sweep" else r.lo
    print(f"  {name:<27} " + " ".join(f"{shown[L][0]:4.1f}dB:{shown[L][1]:8.1e}" for L in LENGTHS)
          + f"  [{time.time() - t0:.0f}s]")
    if targets[name][LENGTHS[0]] is not None:
        print("  " + " " * 27 + " targets: " + "  ".join(
            "(" + ", ".join(f"{c:.2f}" for c in targets[name][L]) + ")" for L in LENGTHS))
    extra = sorted(set(shown) - set(LENGTHS))
    if extra:
        print(f"  {'':<27} " + " ".join(f"{shown[L][0]:4.1f}dB:{shown[L][1]:8.1e}" for L in extra)
              + "  (refined)")
ctrl = reach["control (delta + Viterbi)"]
print("\nreach [dB @ 56 GHz]:")
for name, v in reach.items():
    print(f"  {name:<27} {v:6.2f} dB  {v - ctrl:+5.2f} dB")

# direction, as measured (cairn/DSP发端与PR.md §10 has the numbers)
names = list(CASES)
assert reach[names[2]] > reach[names[1]] > ctrl, reach
assert abs(reach[names[3]] - reach[names[2]]) < 0.5, reach

# ------------------------------------------------------------------ plot ---
fig, ax = plt.subplots(figsize=(7.5, 4.8))
for name, style in zip(CASES, ("s--k", "o-C0", "^-C3", "v:C2")):
    ax.semilogy(*zip(*(sweep[name][L] for L in sorted(sweep[name]))), style,
                label=f"{name}: {reach[name]:.1f} dB")
ax.axhline(P_STAR, color="r", ls="--", lw=1, label=f"KP4 1e-15 ({P_STAR:.1e})")
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="pre-FEC BER after Viterbi",
       title="A second controlled cursor (224 Gb/s PAM4, MMSE targets)")
ax.legend(fontsize=8)
ax.grid(True, which="both", alpha=0.3)
fig.tight_layout()
out = OUT / "38_pr_three_cursor.png"
fig.savefig(out, dpi=130)
print(f"wrote {out}")
