"""Deep 224G LR with concatenated inner-code FEC — how far can reach go?

Example 19 showed the DSP (FFE+DFE+MLSD) is SNR-limited near ~32 dB with KP4
alone. Concatenated FEC attacks the OTHER lever: an inner hard-decision block
code corrects most raw errors, presenting a far lower BER to the RS-KP4 outer
and raising the tolerable pre-FEC BER ~31x (2.2e-4 -> 6.9e-3 for BCH(255,215)) — the deep-LR
/ 800G-1.6T approach.

Fixed DSP (FFE + DFE8 + MLSD mem3) and fixed ADC (ENOB 6.5): sweep channel
loss, then apply KP4-only vs three concatenated schemes and read off the
reach: where the pre-FEC BER crosses each scheme's threshold (post-FEC
1e-15), the sweep refined to 0.4 dB around each crossing (``fec.refine``).
Total FEC overhead is annotated (real budget <~15%). The engine's Viterbi
(memory 3) scores every symbol; until 2026-10-09 the example ran its own, with
3 taps (memory 2), on the 20k symbols the engine keeps in ``y_slicer``.
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

from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    MlsdConfig, RxConfig, SimConfig, ClockConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.fec import concatenated_post_fec_ber, fec_threshold, pre_to_post_fec_ber, refine  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)
_fmt = FuncFormatter(lambda v, _: f"{v:.0e}")
_nofmt = FuncFormatter(lambda v, _: "")


def pre_fec_ber(length_m, n_sym=500_000):
    """Best-DSP link (FFE + DFE8 + MLSD mem3) pre-FEC BER at a channel length."""
    cfg = LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1, clock=ClockConfig(rj_ui=0.004)),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=8.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=4, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=8, adapt="lms", mu=3e-5),
                    mlsd=MlsdConfig(kind="viterbi", memory=3),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"))
    cm = ChannelModel.from_config(cfg)
    # the engine's Viterbi, scored over every symbol after warm-up (see
    # example 19: an offline one on res.y_slicer sees only the first 20k)
    return cm.loss_at(56e9), run_time_link(cfg, channel=cm).ber.ber


# (label, inner (n, t) or None, overhead%)  overhead = KP4 5.8% + inner m*t/(n-m*t)
SCHEMES = {
    "KP4 only (t=15)": (None, 5.8, "C1", "o"),
    "+ inner Hamming(128,120)": ((128, 1), 5.8 + 7.1, "C0", "s"),
    "+ inner BCH(255,215,t=5)": ((255, 5), 5.8 + 18.6, "C2", "^"),
    "+ inner BCH(511,439,t=8)": ((511, 8), 5.8 + 16.4, "C3", "D"),
}


def post_fec(p, inner):
    p = max(p, 1e-9)
    return concatenated_post_fec_ber(p, *inner) if inner else pre_to_post_fec_ber(p, "kp4")


def measure(L):
    lo, pb = pre_fec_ber(L)
    return -lo, pb


lengths = [0.16, 0.18, 0.19, 0.20, 0.21, 0.22, 0.24, 0.26]
print("Sweeping link pre-FEC BER (FFE + DFE8 + MLSD mem3, ADC ENOB 6.5)...")
sweep = {}                           # channel length -> (loss dB, pre-FEC BER)
for L in lengths:
    sweep[L] = measure(L)
    print(f"  {-sweep[L][0]:6.1f}dB: pre-FEC {sweep[L][1]:.2e}")

thresholds = {label: fec_threshold("kp4", inner=inner) for label, (inner, *_) in SCHEMES.items()}
kp4 = thresholds["KP4 only (t=15)"]
print("\npre-FEC BER for post-FEC 1e-15:")
for label, thr in thresholds.items():
    print(f"  {label:26s}: {thr:.2e} ({thr / kp4:.1f}x KP4's)")
reaches = {label: refine(sweep, measure, thr, tol=0.4) for label, thr in thresholds.items()}
print("refined around the crossings:")
for L in sorted(set(sweep) - set(lengths)):
    print(f"  {-sweep[L][0]:6.1f}dB: pre-FEC {sweep[L][1]:.2e}")

fig, ax = plt.subplots(figsize=(8, 5.2))
print("\n== reach summary (post-FEC < 1e-15) ==")
pts = [sweep[L] for L in sorted(sweep)]
for label, (inner, ovh, c, m) in SCHEMES.items():
    post = np.array([post_fec(b, inner) for _, b in pts])
    r = reaches[label]
    rc = r.value
    tag = f"{label}  (reach {rc:.1f} dB, overhead {ovh:.0f}%)" if rc is not None else label
    ax.semilogy([x for x, _ in pts], np.maximum(post, 1e-30), m + "-", color=c, label=tag)
    print(f"  {label:26s}: reach {rc:.1f} dB (overhead {ovh:.0f}%)"
          if rc is not None else f"  {label}: {r.note} ({r.lo} - {r.hi} dB)")
ax.axhline(1e-15, color="green", ls=":", lw=1, label="link target 1e-15")
ax.axvspan(35, 46, color="gray", alpha=0.08)
ax.text(35.2, 1e-28, "802.3dj LR\n(35-45 dB)", fontsize=7, color="gray")
ax.set(xlabel="Channel loss @ 56 GHz Nyquist [dB]", ylabel="post-FEC BER",
       title="Concatenated inner-code FEC extends reach\n(224 Gb/s PAM4, same DSP + same ADC ENOB 6.5)")
ax.yaxis.set_major_formatter(_fmt); ax.yaxis.set_minor_formatter(_nofmt)
ax.legend(fontsize=8, loc="lower right")
ax.grid(True, which="both", alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "20_concatenated_fec_reach.png", dpi=130)
print(f"wrote {OUT / '20_concatenated_fec_reach.png'}")
