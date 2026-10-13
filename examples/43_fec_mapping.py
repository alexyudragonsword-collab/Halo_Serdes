"""224G: where a receiver's errors land in the RS code -- multiplexing,
interleaving, and what that does to the KP4 reach.

Every reach in these examples reads the pre-FEC BER against KP4's threshold
(``fec_threshold``), which projects post-FEC performance as if each bit error
were independent. A Viterbi behind a partial-response target makes its errors
in runs instead, and what a run costs the RS(544,514) code depends on how the
lane's bits reach its 10-bit symbols and codewords (``fec/interleave.py``):
consecutively on one lane, bit-multiplexed from several FEC lanes (400GBASE-R),
or symbol-pair multiplexed with four codewords interleaved (200G per lane).
The structures are modelled from second-hand summaries of 802.3, not from the
clause text (``cairn/多lane建模.md``).

Example 42's link (224 Gb/s PAM4, 21-tap LMS FFE, MM-CDR, ENOB 6.5, 1.5 mV)
with five receivers:

  FFE + DFE1                -- its tap engaged (~0.6): an error propagates, ~3 per event
  FFE + DFE1, precoded      -- 1/(1+D): a run of them becomes about two
  1 + aD, Viterbi           -- example 42's first row
  1 + aD, Viterbi, precoded
  1 + aD + bD^2, Viterbi    -- example 42's third row (256 states)

Part 1 puts independent bit errors through each layout: the method's own
floor (an event model reads a little pessimistic: two errors that fall close
together by chance count as one event). Part 2 profiles each receiver from one
long run near the threshold: errors per event, the RS symbol error rate
against the independent-bit projection's, and the pre-FEC BER at which the
layout's frame error rate equals the projection's at its threshold (with a
bootstrap over events). Part 3 reads the reach at each of those thresholds
off the same sweep as example 42.

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
from halo_serdes.fec import fec_threshold, refine  # noqa: E402
from halo_serdes.fec.interleave import (  # noqa: E402
    BIT_MUX_2CW, SEQUENTIAL, SYMBOL_PAIR_4CW, FecLayout, bit_errors, burst_profile,
)

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

QUICK = "--quick" in sys.argv
N_SYM = 100_000 if QUICK else 400_000             # the sweep's, example 42's
N_PROFILE = 400_000 if QUICK else 4_000_000       # one run per receiver, for its events
N_BOOT = 50 if QUICK else 200

AD = PrConfig(target=(1.0, 0.5), adapt="mmse")
ADBD = PrConfig(target=(1.0, 0.5, 0.0), adapt="mmse")
# name: (PR target, Viterbi?, precoded?, sweep lengths [m], profile length [m])
DFE_LENGTHS = [0.18, 0.19, 0.20, 0.21, 0.22, 0.23, 0.24]
PR_LENGTHS = [0.18, 0.20, 0.22, 0.24, 0.26, 0.28, 0.30]
CASES = {"FFE + DFE1": (PrConfig(), False, False, DFE_LENGTHS, 0.225),
         "FFE + DFE1, precoded": (PrConfig(), False, True, DFE_LENGTHS, 0.225),
         "1 + aD, Viterbi": (AD, True, False, PR_LENGTHS, 0.24),
         "1 + aD, Viterbi, precoded": (AD, True, True, PR_LENGTHS, 0.24),
         "1 + aD + bD^2, Viterbi": (ADBD, True, False, PR_LENGTHS, 0.255)}
if QUICK:
    CASES = {k: v[:3] + (v[3][::2],) + v[4:] for k, v in CASES.items()}

LAYOUTS = {"one lane": SEQUENTIAL,
           "bit mux 4:1, 2 cw": BIT_MUX_2CW,
           "bit mux 8:1, 2 cw": FecLayout(lanes=2, pcs_lanes=8, mux_bits=1, interleave=2, stagger=1),
           "sym pair 8:1, 4 cw": SYMBOL_PAIR_4CW}


def make_cfg(length_m: float, pr: PrConfig, viterbi: bool, precode: bool,
             n_sym: int = N_SYM) -> LinkConfig:
    """Example 42's link; the DFE receivers swap its Viterbi for one DFE tap."""
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
                    dfe=DfeConfig(n_taps=0 if viterbi else 1),
                    mlsd=MlsdConfig(kind="viterbi", memory=2) if viterbi else MlsdConfig(),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"),
        pr=pr, precode=precode,
    )


def gray_ber(r) -> tuple[np.ndarray, int]:
    """Bit errors as the RS code sees them: the Gray bits of each symbol."""
    return bit_errors(r.extras["decisions"], r.extras["reference"])


def loss_db(length_m: float) -> float:
    return -ChannelModel.from_config(make_cfg(length_m, PrConfig(), False, False, 1000)).loss_at(56e9)


def fmt(x: float | None, spec: str) -> str:
    return "n/a" if x is None else format(x, spec)


P_STAR = fec_threshold("kp4")
rng = np.random.default_rng(43)
print("224 Gb/s PAM4 LR (example 42's link): KP4 projected per layout")
print(f"independent-bit projection: post-KP4 1e-15 <=> pre-FEC BER <= {P_STAR:.2e}")
print("threshold ratio: pre-FEC BER at which the layout's frame error rate equals the projection's,")
print("over P*; above 1 the projection is pessimistic for these errors")

# ---- 1. the floor: independent bit errors ------------------------------------
print(f"\n1. independent bit errors at P* ({2 * N_PROFILE / 1e6:g} Mbit): the method's floor")
n_bits = 2 * N_PROFILE
iid = np.flatnonzero(rng.random(n_bits) < P_STAR)
floor = {}
for name, lay in LAYOUTS.items():
    floor[name] = burst_profile(iid, n_bits, lay).threshold() / P_STAR
print("  " + "   ".join(f"{name}: {floor[name]:.3f}" for name in LAYOUTS))

# ---- 2. each receiver's events, near the threshold ---------------------------
print(f"\n2. each receiver near the threshold ({N_PROFILE / 1e6:g}M symbols): its events, and the threshold"
      " ratio per layout\n   (bootstrap 90 % over events in brackets)")
ratio, band, profile_at = {}, {}, {}
for case, (pr, vit, pre, _, length) in CASES.items():
    t0 = time.time()
    r = run_time_link(make_cfg(length, pr, vit, pre, N_PROFILE))
    err, nb = gray_ber(r)
    base = burst_profile(err, nb)
    profile_at[case] = (length, err.size / nb)
    print(f"  {case:<26} {loss_db(length):4.1f} dB  BER {err.size / nb:.2e}  events {base.n_events:5d}  "
          f"errors/event {err.size / max(base.n_events, 1):.2f}  [{time.time() - t0:.0f}s]", flush=True)
    if base.n_events < 20:
        print(f"  {'':<26} too few events to extrapolate from")
        continue
    ser_binom = 1.0 - (1.0 - err.size / nb) ** 10
    for name, lay in LAYOUTS.items():
        prof = burst_profile(err, nb, lay)
        ratio[case, name] = prof.threshold() / P_STAR
        boot = [prof.resample(rng).threshold() / P_STAR for _ in range(N_BOOT)]
        band[case, name] = tuple(np.percentile(boot, [5, 95]))
        pmf = prof.pmf
        print(f"  {'':<4}{name:<22} RS symbols/independent {prof.symbol_error_rate / ser_binom:4.2f}  "
              f"per codeword 1/2/3+: {pmf[1]:.3f}/{pmf[2:3].sum():.3f}/{pmf[3:].sum():.3f}  "
              f"threshold ratio {ratio[case, name]:5.2f} [{band[case, name][0]:.2f}-{band[case, name][1]:.2f}]")

# ---- 3. the reach at each threshold ------------------------------------------
print("\n3. reach [dB] where the sweep's pre-FEC BER crosses each threshold (refined to 0.4 dB)")
reach = {}
for case, (pr, vit, pre, lengths, _) in CASES.items():
    t0 = time.time()

    def measure(L, pr=pr, vit=vit, pre=pre):
        err, nb = gray_ber(run_time_link(make_cfg(L, pr, vit, pre)))
        return loss_db(L), err.size / nb

    points = {L: measure(L) for L in lengths}
    rows = [("independent bits", P_STAR)] + [(name, ratio[case, name] * P_STAR)
                                              for name in LAYOUTS if (case, name) in ratio]
    for name, th in rows:
        rr = refine(points, measure, th, tol=0.4)
        reach[case, name] = rr.value
    print(f"  {case:<26} " + " ".join(f"{points[L][0]:4.1f}dB:{max(points[L][1], 0.5 / (2 * N_SYM)):8.1e}"
                                     for L in sorted(points)) + f"  [{time.time() - t0:.0f}s]", flush=True)

print(f"\n  {'':<26} {'independent':>11} " + " ".join(f"{name:>18}" for name in LAYOUTS))
for case in CASES:
    ind = reach.get((case, "independent bits"))
    cells = []
    for name in LAYOUTS:
        v = reach.get((case, name))
        cells.append(f"{fmt(v, '6.2f')} ({fmt(None if v is None or ind is None else round(v - ind, 2) + 0.0, '+.2f')})")
    print(f"  {case:<26} {fmt(ind, '11.2f')} " + " ".join(f"{c:>18}" for c in cells))

print("\n  differences of reach, per layout [dB]: the PR target's gain over the DFE, and what precoding does")
for label, a, b in (("1 + aD over DFE1", "1 + aD, Viterbi", "FFE + DFE1"),
                    ("1 + aD + bD^2 over DFE1", "1 + aD + bD^2, Viterbi", "FFE + DFE1"),
                    ("precoding the DFE1", "FFE + DFE1, precoded", "FFE + DFE1"),
                    ("precoding 1 + aD", "1 + aD, Viterbi, precoded", "1 + aD, Viterbi")):
    cells = []
    for name in ("independent bits",) + tuple(LAYOUTS):
        va, vb = reach.get((a, name)), reach.get((b, name))
        cells.append(fmt(None if va is None or vb is None else round(va - vb, 2) + 0.0, "+.2f"))
    print(f"  {label:<26} {cells[0]:>11} " + " ".join(f"{c:>18}" for c in cells[1:]))

# direction, as measured (cairn/多lane建模.md §6); only on a full-size run. The
# DFE's errors were isolated (a ratio of ~1 everywhere) while its tap stayed at
# 0 (ROADMAP P1 #21); engaged, it makes runs like the Viterbi's.
if not QUICK:
    missing = [k for k in ((c, n) for c in CASES for n in LAYOUTS) if k not in ratio]
    if missing:
        print(f"direction checks skipped: no threshold ratio for {missing}")
    else:
        assert all(0.9 < f <= 1.0 for f in floor.values()), floor
        for case in ("FFE + DFE1", "1 + aD, Viterbi", "1 + aD + bD^2, Viterbi"):
            assert ratio[case, "sym pair 8:1, 4 cw"] > 1.5, ratio        # pessimistic
            assert ratio[case, "one lane"] < 0.8, ratio                  # optimistic
            assert ratio[case, "bit mux 4:1, 2 cw"] < 0.4, ratio
            assert ratio[case, "bit mux 8:1, 2 cw"] < ratio[case, "bit mux 4:1, 2 cw"], ratio

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
w = 0.8 / len(LAYOUTS)
x = np.arange(len(CASES))
for k, name in enumerate(LAYOUTS):
    vals = [ratio.get((c, name), np.nan) for c in CASES]
    lo = [v - band.get((c, name), (v, v))[0] for c, v in zip(CASES, vals)]
    hi = [band.get((c, name), (v, v))[1] - v for c, v in zip(CASES, vals)]
    axes[0].bar(x + (k - 1.5) * w, vals, w, yerr=[lo, hi], capsize=2, label=name)
axes[0].axhline(1.0, color="k", lw=1)
axes[0].set_yscale("log")
axes[0].set_xticks(x, list(CASES), rotation=15, fontsize=7)
axes[0].set(ylabel="threshold / P*", title="KP4 threshold per layout (>1: independent-bit projection pessimistic)")
axes[0].legend(fontsize=7)
axes[0].grid(True, which="both", axis="y", alpha=0.3)
for k, name in enumerate(LAYOUTS):
    vals = [(reach.get((c, name)) or np.nan) - (reach.get((c, "independent bits")) or np.nan) for c in CASES]
    axes[1].bar(x + (k - 1.5) * w, vals, w, label=name)
axes[1].axhline(0.0, color="k", lw=1)
axes[1].set_xticks(x, list(CASES), rotation=15, fontsize=7)
axes[1].set(ylabel="reach - independent-bit reach [dB]", title="What the mapping does to the reach")
axes[1].legend(fontsize=7)
axes[1].grid(True, axis="y", alpha=0.3)
fig.tight_layout()
out = OUT / "43_fec_mapping.png"
fig.savefig(out, dpi=130)
print(f"wrote {out}")
