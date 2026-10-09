"""Full-stack deep LR: best DSP + better ADC + concatenated FEC together.

Examples 19 and 20 showed the ADC lever (~+5.5 dB) and the FEC lever (~+3 dB)
separately. This combines them. Four cumulative configs, same channel sweep:

  A. KP4 + ADC ENOB 6.5              (baseline, ~33 dB)
  B. + concatenated BCH inner FEC     (FEC lever)
  C. + better ADC ENOB 7.5            (ADC lever)
  D. both: ADC 7.5 + concatenated FEC (full stack)

Reach = where the pre-FEC BER crosses the FEC's threshold (post-FEC 1e-15),
with the sweep refined to 0.4 dB around each crossing (``fec.refine``). The
question: does the full stack reach into the 802.3dj LR band (35-45 dB @ 56
GHz Nyquist)?

Until 2026-10-09 this read the crossing off log post-FEC BER on the 3 dB grid
alone: the baseline came out at 33.2 dB (its last point below had no errors,
so the answer was the stand-in 1e-9's) and the full stack at 44.2 dB (the
concatenated code's post-FEC map is far from linear across a 3 dB step),
and it ran its own Viterbi on the 20k symbols the engine keeps in
``y_slicer`` -- about nine errors at KP4's threshold. The engine's Viterbi
(memory 3, as labelled; the old fit used 3 taps, memory 2) now scores every
symbol.
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


def pre_fec(length_m, enob, noise, n_sym=500_000):
    """Best-DSP pre-FEC BER (FFE + DFE8 + MLSD mem3) at a given ADC quality."""
    cfg = LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1, clock=ClockConfig(rj_ui=0.004)),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=8.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=enob, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=4, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=8, adapt="lms", mu=3e-5),
                    mlsd=MlsdConfig(kind="viterbi", memory=3),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"))
    cm = ChannelModel.from_config(cfg)
    # the engine's Viterbi, scored over every symbol after warm-up (see
    # example 19: an offline one on res.y_slicer sees only the first 20k)
    return cm.loss_at(56e9), run_time_link(cfg, channel=cm).ber.ber


lengths = [0.18, 0.20, 0.22, 0.24, 0.26, 0.28, 0.30]
GRADES = {6.5: 0.0015, 7.5: 0.0008}  # ADC ENOB -> input-referred noise rms
print("Sweeping pre-FEC BER, two ADC grades...")
sweeps = {e: {} for e in GRADES}     # channel length -> (loss dB, pre-FEC BER)
for L in lengths:
    for e, noise in GRADES.items():
        lo, pb = pre_fec(L, enob=e, noise=noise)
        sweeps[e][L] = (-lo, pb)
    print(f"  {-sweeps[6.5][L][0]:6.1f}dB: pre-FEC(ENOB6.5) {sweeps[6.5][L][1]:.2e} | (ENOB7.5) {sweeps[7.5][L][1]:.2e}")

BCH_N, BCH_T = 255, 5  # inner code for the concatenated schemes
KP4 = fec_threshold("kp4")
CONCAT = fec_threshold("kp4", inner=(BCH_N, BCH_T))
print(f"\nreach: post-FEC 1e-15 <=> pre-FEC BER <= {KP4:.2e} (KP4), {CONCAT:.2e} (+ BCH({BCH_N},{BCH_N - 8 * BCH_T}))")

CONFIGS = [
    ("A. KP4 + ADC 6.5 (baseline)", 6.5, KP4, lambda p: pre_to_post_fec_ber(max(p, 1e-9), "kp4"), "C1", "o"),
    ("B. + concatenated FEC (ADC 6.5)", 6.5, CONCAT,
     lambda p: concatenated_post_fec_ber(max(p, 1e-9), BCH_N, BCH_T), "C0", "s"),
    ("C. + better ADC 7.5 (KP4 only)", 7.5, KP4, lambda p: pre_to_post_fec_ber(max(p, 1e-9), "kp4"), "C2", "^"),
    ("D. full stack: ADC 7.5 + concatenated FEC", 7.5, CONCAT,
     lambda p: concatenated_post_fec_ber(max(p, 1e-9), BCH_N, BCH_T), "C3", "D"),
]


def measure(enob):
    def run(L):
        lo, pb = pre_fec(L, enob=enob, noise=GRADES[enob])
        return -lo, pb
    return run


reaches = {label: refine(sweeps[e], measure(e), thr, tol=0.4) for label, e, thr, *_ in CONFIGS}
print("refined around the crossings:")
for e in GRADES:
    for L in sorted(set(sweeps[e]) - set(lengths)):
        print(f"  {-sweeps[e][L][0]:6.1f}dB: pre-FEC(ENOB{e}) {sweeps[e][L][1]:.2e}")

fig, ax = plt.subplots(figsize=(8.5, 5.4))
print("\n== reach summary (post-FEC < 1e-15) ==")
for label, e, thr, fn, c, m in CONFIGS:
    pts = [sweeps[e][L] for L in sorted(sweeps[e])]
    post = np.array([fn(b) for _, b in pts])
    rc = reaches[label].value
    tag = f"{label}  (reach {rc:.1f} dB)" if rc is not None else label
    ax.semilogy([x for x, _ in pts], np.maximum(post, 1e-30), m + "-", color=c, label=tag)
    r = reaches[label]
    print(f"  {label:28s}: reach {rc:.1f} dB" if rc is not None
          else f"  {label}: {r.note} ({r.lo} - {r.hi} dB)")
ax.axhline(1e-15, color="green", ls=":", lw=1, label="link target 1e-15")
ax.axvspan(35, 46, color="gray", alpha=0.10)
ax.text(35.3, 1e-27, "802.3dj LR\n(35-45 dB)", fontsize=8, color="dimgray")
ax.set(xlabel="Channel loss @ 56 GHz Nyquist [dB]", ylabel="post-FEC BER",
       title="Full-stack deep LR: DSP + ADC + concatenated FEC together\n(224 Gb/s PAM4)")
ax.yaxis.set_major_formatter(_fmt); ax.yaxis.set_minor_formatter(_nofmt)
ax.legend(fontsize=8, loc="lower right")
ax.grid(True, which="both", alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "21_full_stack_lr.png", dpi=130)
print(f"wrote {OUT / '21_full_stack_lr.png'}")
