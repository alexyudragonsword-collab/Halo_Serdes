"""224G long-reach: how many trellis states does the reach actually need?

Example 38 bought +7.3 dB of reach with a second controlled cursor, 1 + aD +
bD^2, at the price of a 256-state Viterbi (target + memory 2 for PAM4), and
example 41 priced that trellis above any published 224G receiver. A reduced-
state trellis (``rx.mlsd.state_memory`` = K, decision-feedback sequence
estimation) keeps only the last K symbols in its state -- 4^K states -- and
cancels the trellis's other cursors with each survivor's own decisions.

Same link, receiver and seed as example 38 (21-tap LMS FFE, MM-CDR, ENOB 6.5,
1.5 mV, MMSE targets, Viterbi memory 2 behind the target), so the full-trellis
rows are example 38's own:

  1 + aD          -- 64 states (full) and 4 (K = 1: the target alone)
  1 + aD + bD^2   -- 256 (full), 16 (K = 2: the target alone), 4 (K = 1)
  + cD^3          -- memory 1: 256 (full) and 64 (K = 3: the target alone)

Reach is where the pre-FEC BER crosses KP4's threshold, refined to 0.4 dB
(``fec.refine``); the Viterbi's power is example 41's estimate
(``analysis/power.py``: public-figure coefficients, read the ratios).

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

from halo_serdes.analysis.power import power_report, trellis_states  # noqa: E402
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

AD = PrConfig(target=(1.0, 0.5), adapt="mmse")
ADBD = PrConfig(target=(1.0, 0.5, 0.0), adapt="mmse")
ADBDCD = PrConfig(target=(1.0, 0.5, 0.0, 0.0), adapt="mmse")
# name: (target, Viterbi residual memory, state memory K; None = full trellis)
CASES = {"1 + aD, full": (AD, 2, None),
         "1 + aD, K = 1": (AD, 2, 1),
         "1 + aD + bD^2, full": (ADBD, 2, None),
         "1 + aD + bD^2, K = 2": (ADBD, 2, 2),
         "1 + aD + bD^2, K = 1": (ADBD, 2, 1),
         "+ cD^3 (memory 1), full": (ADBDCD, 1, None),
         "+ cD^3 (memory 1), K = 3": (ADBDCD, 1, 3)}


def make_cfg(length_m: float, pr: PrConfig, memory: int = 2, k: int | None = None,
             n_sym: int = N_SYM) -> LinkConfig:
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
                    mlsd=MlsdConfig(kind="viterbi", memory=memory, state_memory=k),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"),
        pr=pr,
    )


P_STAR = fec_threshold("kp4")


def loss_db(length_m: float) -> float:
    return -ChannelModel.from_config(make_cfg(length_m, PrConfig(), n_sym=1000)).loss_at(56e9)


print("224 Gb/s PAM4 LR, 21-tap LMS FFE + Viterbi, MMSE targets: full and reduced-state trellises")
print(f"reach: post-KP4 1e-15 <=> pre-FEC BER <= {P_STAR:.2e}")
sweep, reach, states, pj = {}, {}, {}, {}
for name, (pr, mem, k) in CASES.items():
    t0 = time.time()
    shown = {}

    def measure(L, pr=pr, mem=mem, k=k, shown=shown):
        r = run_time_link(make_cfg(L, pr, mem, k))
        shown[L] = (loss_db(L), max(r.ber.ber, 0.5 / r.ber.n_checked))   # no errors: half a count
        return shown[L][0], r.ber.ber

    r = refine({L: measure(L) for L in LENGTHS}, measure, P_STAR, tol=0.4)
    sweep[name] = shown
    reach[name] = r.value if r.value is not None else r.hi if r.note == "below sweep" else r.lo
    cfg = make_cfg(LENGTHS[0], pr, mem, k, n_sym=1000)
    states[name] = trellis_states(cfg)
    rep = power_report(cfg)
    pj[name] = (rep.pj("host.rx.mlsd"), rep.pj("host.rx"))
    print(f"  {name:<26} " + " ".join(f"{shown[L][0]:4.1f}dB:{shown[L][1]:8.1e}" for L in LENGTHS)
          + f"  [{time.time() - t0:.0f}s]", flush=True)
    extra = sorted(set(shown) - set(LENGTHS))
    if extra:
        print(f"  {'':<26} " + " ".join(f"{shown[L][0]:4.1f}dB:{shown[L][1]:8.1e}" for L in extra)
              + "  (refined)")

print("\nsummary                     states   reach [dB]  vs full   Viterbi [pJ/b]  receiver [pJ/b]")
full_of = {"1 + aD": "1 + aD, full", "1 + aD + bD^2": "1 + aD + bD^2, full",
           "+ cD^3 (memory 1)": "+ cD^3 (memory 1), full"}
for name in CASES:
    ref = full_of[name.split(",")[0]]
    gain = round(reach[name] - reach[ref], 2) + 0.0       # no "-0.00" for a 1e-3 dB difference
    print(f"  {name:<26} {states[name]:6d}   {reach[name]:8.2f}   {gain:+6.2f}"
          f"   {pj[name][0]:10.2f}       {pj[name][1]:10.2f}")

# direction, as measured (cairn/DSP发端与PR.md §18)
assert abs(reach["1 + aD + bD^2, K = 2"] - reach["1 + aD + bD^2, full"]) < 0.4, reach
assert reach["1 + aD + bD^2, K = 2"] > reach["1 + aD, full"], reach

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
for name, style in zip(CASES, ("o-C0", "o:C0", "^-C3", "^--C3", "^:C3", "D-C1", "D--C1")):
    axes[0].semilogy(*zip(*(sweep[name][L] for L in sorted(sweep[name]))), style,
                     label=f"{name} ({states[name]}): {reach[name]:.1f} dB")
axes[0].axhline(P_STAR, color="r", ls="--", lw=1, label=f"KP4 1e-15 ({P_STAR:.1e})")
axes[0].set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="pre-FEC BER after Viterbi",
            title="Full and reduced-state trellises (224 Gb/s PAM4)")
axes[0].legend(fontsize=7)
axes[0].grid(True, which="both", alpha=0.3)
for name, marker in zip(CASES, "oo^^^DD"):
    axes[1].scatter(pj[name][0], reach[name], marker=marker, s=50)
    axes[1].annotate(f"{name} ({states[name]})", (pj[name][0], reach[name]), fontsize=7,
                     xytext=(4, 2), textcoords="offset points")
axes[1].set_xscale("log")
axes[1].set(xlabel="Viterbi power estimate [pJ/b] (example 41's model)", ylabel="reach [dB]",
            title="Reach against the trellis's cost")
axes[1].grid(True, which="both", alpha=0.3)
fig.tight_layout()
out = OUT / "42_reduced_state_mlsd.png"
fig.savefig(out, dpi=130)
print(f"wrote {out}")
