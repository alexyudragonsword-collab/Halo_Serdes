"""Full-stack deep LR: best DSP + better ADC + concatenated FEC together.

Examples 19 and 20 showed the ADC lever (+6 dB) and the FEC lever (+6 dB)
separately. This combines them. Four cumulative configs, same channel sweep:

  A. KP4 + ADC ENOB 6.5              (baseline, ~29 dB)
  B. + concatenated BCH inner FEC     (FEC lever)
  C. + better ADC ENOB 7.5            (ADC lever)
  D. both: ADC 7.5 + concatenated FEC (full stack)

Reach = where post-FEC crosses 1e-15. The question: does the full stack reach
into the 802.3dj LR band (35-45 dB @ 56 GHz Nyquist)?
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
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.prbs import symbol_checker  # noqa: E402
from halo_serdes.dsp import viterbi_mlsd  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.static_link import make_pattern  # noqa: E402
from halo_serdes.fec import concatenated_post_fec_ber, pre_to_post_fec_ber  # noqa: E402

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
        tx=TxConfig(swing=1.0, rj_ui=0.004, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=8.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=enob, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=4, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=8, adapt="lms", mu=3e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"))
    cm = ChannelModel.from_config(cfg)
    res = run_time_link(cfg, channel=cm)
    lv = res.extras["levels"]; warm = res.extras["warmup"]
    y = res.y_slicer; ref = make_pattern(cfg)[warm: warm + y.size]
    ld = lv[ref]
    cols = [ld] + [np.concatenate([np.zeros(i), ld[:-i]]) for i in range(1, 4)]
    coef, *_ = np.linalg.lstsq(np.vstack(cols).T, y, rcond=None)
    dec = viterbi_mlsd(y.astype(np.float64), lv.astype(np.float64), coef[:3])
    return cm.loss_at(56e9), symbol_checker(ref, dec).ber


lengths = [0.18, 0.20, 0.22, 0.24, 0.26, 0.28, 0.30]
print("Sweeping pre-FEC BER, two ADC grades...")
loss65, pre65, loss75, pre75 = [], [], [], []
for L in lengths:
    lo, pb = pre_fec(L, enob=6.5, noise=0.0015)
    loss65.append(lo); pre65.append(pb)
    lo2, pb2 = pre_fec(L, enob=7.5, noise=0.0008)
    loss75.append(lo2); pre75.append(pb2)
    print(f"  {lo:6.1f}dB: pre-FEC(ENOB6.5) {pb:.2e} | (ENOB7.5) {pb2:.2e}")
loss = np.array(loss65)
pre65 = np.array(pre65); pre75 = np.array(pre75)

BCH_N, BCH_T = 255, 5  # inner code for the concatenated schemes

CONFIGS = [
    ("A. KP4 + ADC 6.5 (baseline)", pre65,
     lambda p: pre_to_post_fec_ber(max(p, 1e-9), "kp4"), "C1", "o"),
    ("B. + concatenated FEC (ADC 6.5)", pre65,
     lambda p: concatenated_post_fec_ber(max(p, 1e-9), BCH_N, BCH_T), "C0", "s"),
    ("C. + better ADC 7.5 (KP4 only)", pre75,
     lambda p: pre_to_post_fec_ber(max(p, 1e-9), "kp4"), "C2", "^"),
    ("D. full stack: ADC 7.5 + concatenated FEC", pre75,
     lambda p: concatenated_post_fec_ber(max(p, 1e-9), BCH_N, BCH_T), "C3", "D"),
]


def reach(post):
    x = -loss
    lp = np.log10(np.maximum(post, 1e-300))
    for i in range(len(x) - 1):
        if lp[i] < -15 <= lp[i + 1]:
            t = (-15 - lp[i]) / (lp[i + 1] - lp[i])
            return x[i] + t * (x[i + 1] - x[i])
    return x[0] if lp[0] >= -15 else None


fig, ax = plt.subplots(figsize=(8.5, 5.4))
print("\n== reach summary (post-FEC < 1e-15) ==")
for label, pre, fn, c, m in CONFIGS:
    post = np.array([fn(p) for p in pre])
    rc = reach(post)
    tag = f"{label}  (reach {rc:.1f} dB)" if rc else label
    ax.semilogy(-loss, np.maximum(post, 1e-30), m + "-", color=c, label=tag)
    print(f"  {label:28s}: reach {rc:.1f} dB" if rc else f"  {label}: <start")
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
