"""Deep 224G long-reach: how far can FFE + DFE + deeper MLSD + KP4 reach?

Pushing past the ~31.3 dB reach of example 18's receiver by adding DFE (cancels far
postcursors without FFE's noise enhancement) and deeper MLSD memory. The
honest finding: at deep LR the link is SNR-limited, not ISI/DSP-depth-limited.

- adding DFE + MLSD memory-3 over FFE+memory-2 buys under 2 dB (the residual
  ISI is already small; the wall is noise, and FFE-inverting a 30 dB channel
  enhances it);
- the big lever is ADC sampling quality — better ENOB / lower noise floor
  buys ~5.5 dB of reach with the SAME DSP (31.3 -> 33.0 dB from DSP depth, but
  33.0 -> 38.5 dB from the ADC; recomputed 2026-10-09). The earlier
  28 -> 29 -> 35.5 dB ladder was taken with MM-CDR off the peak.

The sequence detector is the engine's (``mlsd``), scored over every symbol.
Until 2026-10-09 the example ran its own Viterbi on ``res.y_slicer``, which
the engine caps at the first 20k symbols: a 500k-symbol run scored on 40k
bits, about nine errors at KP4's threshold, and a BER that did not fall
monotonically with loss.

Two panels: (1) DSP-depth comparison at a fixed ADC; (2) ADC-quality
comparison at the best DSP. Reach = where the pre-FEC BER crosses KP4's
threshold (post-KP4 1e-15), the sweep refined to 0.4 dB around each crossing
(``fec.refine``).
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
from halo_serdes.fec import fec_threshold, pre_to_post_fec_ber, refine  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)
_fmt = FuncFormatter(lambda v, _: f"{v:.0e}")
_nofmt = FuncFormatter(lambda v, _: "")


def evaluate(length_m, n_pre, n_post, n_dfe, peak, mem, enob, noise,
             n_sym=500_000):
    cfg = LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1, clock=ClockConfig(rj_ui=0.004)),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=peak),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=enob, fullscale=0.6),
                    ffe=FfeConfig(n_pre=n_pre, n_post=n_post, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=n_dfe, adapt="lms" if n_dfe else "none", mu=3e-5),
                    mlsd=MlsdConfig(kind="viterbi", memory=mem),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"))
    cm = ChannelModel.from_config(cfg)
    loss = cm.loss_at(56e9)
    # the engine's Viterbi scores every symbol after warm-up; this example
    # used to run its own on res.y_slicer, which holds only the first 20k
    # (SLICER_CAPTURE_SYMBOLS) -- ~9 errors at KP4's threshold
    ber = run_time_link(cfg, channel=cm).ber.ber
    return loss, pre_to_post_fec_ber(max(ber, 1e-9), "kp4"), ber


CONFIGS = {
    # (label, n_pre, n_post, n_dfe, peak, mem, enob, noise)
    "FFE + MLSD mem2 (example 18)": dict(n_pre=6, n_post=14, n_dfe=0, peak=6, mem=2,
                                      enob=6.5, noise=0.0015),
    "FFE + DFE8 + MLSD mem3":   dict(n_pre=6, n_post=4, n_dfe=8, peak=8, mem=3,
                                      enob=6.5, noise=0.0015),
    "same + better ADC (ENOB7.5)": dict(n_pre=6, n_post=4, n_dfe=8, peak=8, mem=3,
                                       enob=7.5, noise=0.0008),
}
lengths = [0.16, 0.18, 0.19, 0.20, 0.21, 0.22, 0.24, 0.26, 0.28]

KP4 = fec_threshold("kp4")
results, reaches = {}, {}
for label, kw in CONFIGS.items():
    print(f"== {label} ==")
    rows = {}
    for L in lengths:
        rows[L] = evaluate(length_m=L, **kw)
        loss, post, pre = rows[L]
        print(f"  {loss:6.1f}dB: pre-FEC {pre:.2e} -> post-KP4 {post:.1e}")
    sweep = {L: (-r[0], r[2]) for L, r in rows.items()}

    def measure(L, kw=kw, rows=rows):
        rows[L] = evaluate(length_m=L, **kw)
        return -rows[L][0], rows[L][2]

    reaches[label] = refine(sweep, measure, KP4, tol=0.4)
    for L in sorted(set(rows) - set(lengths)):
        loss, post, pre = rows[L]
        print(f"  {loss:6.1f}dB: pre-FEC {pre:.2e} -> post-KP4 {post:.1e}  (refined)")
    results[label] = np.array([rows[L] for L in sorted(rows)])


def tag(label):
    v = reaches[label].value
    return f"{label} (reach {v:.1f} dB)" if v is not None else label


fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
colors = {"FFE + MLSD mem2 (example 18)": "C1", "FFE + DFE8 + MLSD mem3": "C0",
          "same + better ADC (ENOB7.5)": "C2"}
markers = {"FFE + MLSD mem2 (example 18)": "o", "FFE + DFE8 + MLSD mem3": "s",
           "same + better ADC (ENOB7.5)": "^"}

# panel 1: DSP depth (fixed ADC ENOB 6.5)
ax = axes[0]
for label in ("FFE + MLSD mem2 (example 18)", "FFE + DFE8 + MLSD mem3"):
    r = results[label]
    ax.semilogy(-r[:, 0], np.maximum(r[:, 1], 1e-30), markers[label] + "-",
                color=colors[label], label=tag(label))
ax.axhline(1e-15, color="green", ls=":", lw=1, label="link target 1e-15")
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="post-KP4 BER",
       title="Add DFE + deeper MLSD: under 2 dB more at same ADC\n(residual ISI already small; deep LR is SNR-limited)")
ax.yaxis.set_major_formatter(_fmt); ax.yaxis.set_minor_formatter(_nofmt)
ax.legend(fontsize=7.5); ax.grid(True, which="both", alpha=0.3)

# panel 2: ADC quality (best DSP)
ax = axes[1]
for label in ("FFE + DFE8 + MLSD mem3", "same + better ADC (ENOB7.5)"):
    r = results[label]
    ax.semilogy(-r[:, 0], np.maximum(r[:, 1], 1e-30), markers[label] + "-",
                color=colors[label], label=tag(label))
ax.axhline(1e-15, color="green", ls=":", lw=1, label="link target 1e-15")
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="post-KP4 BER",
       title="Better ADC (ENOB 6.5->7.5, half the noise): ~5 dB more at same DSP\n(the real lever for deep LR is sampling quality)")
ax.yaxis.set_major_formatter(_fmt); ax.yaxis.set_minor_formatter(_nofmt)
ax.legend(fontsize=7.5); ax.grid(True, which="both", alpha=0.3)

fig.tight_layout()
fig.savefig(OUT / "19_deep_lr_limit.png", dpi=130)
print("\n== reach summary (max loss with post-KP4 < 1e-15) ==")
for label, r in reaches.items():
    if r.value is not None:
        print(f"  {label}: {r.value:.1f} dB")
    elif r.note == "below sweep":
        print(f"  {label}: < {r.hi:.1f} dB (fails at the first point)")
    elif r.note == "beyond sweep":
        print(f"  {label}: > {r.lo:.1f} dB (no crossing inside the sweep)")
    else:
        print(f"  {label}: {r.lo:.1f} - {r.hi:.1f} dB ({r.note})")
print(f"wrote {OUT / '19_deep_lr_limit.png'}")
