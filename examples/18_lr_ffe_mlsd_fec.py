"""224G long-reach: where MLSD earns its keep, and where it has nothing to do.

MLSD (Viterbi MLSE) works on the ISI the linear equaliser leaves behind. An
LMS-adapted FFE converges to the MMSE solution, which leaves almost none: on
this channel a 21-tap FFE leaves residual cursors of 0.002 or less and MLSD
memory-2 adds nothing at any loss. Cut the FFE to 3 taps and it leaves a real
residual (second postcursor -0.06 to -0.11 from -28.8 dB of loss on); MLSD
then recovers 2.4-4.3x in symbol errors there -- but the 3-tap FFE + MLSD still
does not beat the 21-tap FFE alone.

So in this model MLSD substitutes for FFE taps rather than adding reach. Where
MLSD really adds reach is an FFE that equalises to a partial-response target
(e.g. 1 + 0.75D) instead of to a delta, so it enhances less noise and leaves
the MLSE a known, controlled ISI: example 36.

The sweep runs both FFE lengths across the LR channel lengths and reports, at
each loss: the slicer's SER before the engine's memory-2 Viterbi and after it
(both over every scored symbol, ``extras["ser_slicer"]`` and ``ser``), their
ratio, and the BER after it with its KP4 projection. The residual cursors are
a least-squares fit on the 20k slicer samples the engine keeps
(``y_slicer``), which is plenty for three numbers. Until 2026-10-09 the
example also counted errors on those 20k: a few to a few dozen per point.

History (2026-10-03): this example used to show a 29x MLSD gain at -27 dB with
the 21-tap FFE. That gain came from the receiver, not the channel -- the
starting equaliser did not see the Tx FFE and MM-CDR read the raw ADC samples,
locking off the eye's peak.
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
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    MlsdConfig, RxConfig, SimConfig, ClockConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.static_link import make_pattern  # noqa: E402
from halo_serdes.fec import fec_threshold, pre_to_post_fec_ber  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

KP4 = fec_threshold("kp4")  # pre-FEC BER at post-KP4 1e-15


# (label, n_pre, n_post): the 21-tap FFE is the receiver examples 19 and 35 reuse
FFES = (("21-tap FFE", 6, 14), ("3-tap FFE", 1, 1))


def make_cfg(length_m: float, n_pre: int = 6, n_post: int = 14,
             n_sym: int = 400_000) -> LinkConfig:
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,  # 224 Gb/s
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1, clock=ClockConfig(rj_ui=0.004)),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=n_pre, n_post=n_post, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    mlsd=MlsdConfig(kind="viterbi", memory=2),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"),
    )


def evaluate(length_m: float, n_pre: int, n_post: int):
    cfg = make_cfg(length_m, n_pre, n_post)
    cm = ChannelModel.from_config(cfg)
    loss = cm.loss_at(56e9)
    res = run_time_link(cfg, channel=cm)
    levels = res.extras["levels"]
    warm = res.extras["warmup"]
    y = res.y_slicer
    ref = make_pattern(cfg)[warm: warm + y.size]

    # residual channel at the slicer (LMMSE fit of the captured slicer stream)
    ld = levels[ref]
    cols = [ld] + [np.concatenate([np.zeros(i), ld[:-i]]) for i in (1, 2, 3)]
    coef, *_ = np.linalg.lstsq(np.vstack(cols).T, y, rcond=None)

    # the slicer before the Viterbi and the Viterbi's output, both scored over
    # every symbol: the same decisions, so the ratio is the MLSD gain
    return {"loss": loss, "snr": res.slicer_snr_db, "resid": coef[:4] / coef[0],
            "ser_ffe": res.extras["ser_slicer"], "ser_mlsd": res.ser, "ber_mlsd": res.ber.ber}


lengths = [0.15, 0.17, 0.18, 0.19, 0.20, 0.22]
results = {}
print("224 Gb/s PAM4 LR: FFE-only vs FFE + MLSD (memory 2), two FFE lengths, + KP4 projection")
for label, n_pre, n_post in FFES:
    print(f"== {label} ({n_pre} pre / {n_post} post) ==")
    print(f"{'Loss@Nyq':>9} {'Class':>5} {'SNR':>6} {'FFE SER':>10} {'MLSD SER':>10} "
          f"{'MLSD gain':>8} {'MLSD BER':>10}  {'post-KP4(MLSD)':>13}  residual h1..h3")
    rows = []
    for L in lengths:
        t0 = time.time()
        r = evaluate(L, n_pre, n_post)
        rows.append(r)
        reach = "C2M" if r["loss"] > -25 else ("MR" if r["loss"] > -35 else "LR")
        gain = f"{r['ser_ffe'] / r['ser_mlsd']:6.1f}x" if r["ser_mlsd"] > 0 else "      -"
        post = pre_to_post_fec_ber(max(r["ber_mlsd"], 1e-9), "kp4")
        resid = " ".join(f"{c:+.3f}" for c in r["resid"][1:4])
        print(f"{r['loss']:8.1f}dB {reach:>5} {r['snr']:5.1f}dB {r['ser_ffe']:9.2e} "
              f"{r['ser_mlsd']:9.2e} {gain} {r['ber_mlsd']:9.2e}  {post:12.1e}  {resid}  [{time.time()-t0:.0f}s]")
    results[label] = rows

# ------------------------------------------------------------------ plot ---
from matplotlib.ticker import FuncFormatter  # noqa: E402

_fmt = FuncFormatter(lambda v, _: f"{v:.0e}")
_nofmt = FuncFormatter(lambda v, _: "")
colors = {"21-tap FFE": "C0", "3-tap FFE": "C1"}

fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
ax = axes[0]
for label, rows in results.items():
    loss = np.array([r["loss"] for r in rows])
    ffe = np.array([max(r["ser_ffe"], 5e-7) for r in rows])
    mlsd = np.array([max(r["ser_mlsd"], 5e-7) for r in rows])
    ax.semilogy(-loss, ffe, "o--", color=colors[label], label=f"{label} only")
    ax.semilogy(-loss, mlsd, "s-", color=colors[label], label=f"{label} + MLSD (memory 2)")
# Gray-coded PAM4: one symbol error is mostly one bit of two, so SER ~ 2 BER
ax.axhline(2 * KP4, color="r", ls="--", lw=1, label=f"KP4 1e-15: BER {KP4:.2e}, SER ~{2 * KP4:.1e}")
ax.text(22.8, 6e-7, "no errors in 400k symbols (plotted at 5e-7)", fontsize=7, color="gray")
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="pre-FEC SER",
       title="MLSD recovers what a short FFE leaves;\na 21-tap MMSE FFE leaves it nothing (224 Gb/s PAM4)")
ax.yaxis.set_major_formatter(_fmt)
ax.yaxis.set_minor_formatter(_nofmt)
ax.legend(fontsize=7.5)
ax.grid(True, which="both", alpha=0.3)

ax = axes[1]
for label, rows in results.items():
    pts = [(-r["loss"], r["ser_ffe"] / r["ser_mlsd"]) for r in rows
           if r["ser_mlsd"] > 0 and r["ser_ffe"] > 0]
    if pts:
        x, g = zip(*pts)
        ax.plot(x, g, "s-", color=colors[label], label=label)
ax.axhline(1.0, color="gray", lw=1)
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="MLSD gain (FFE SER / MLSD SER)",
       title="MLSD gain by FFE length\n(points where either detector made no errors omitted)")
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3)

fig.tight_layout()
fig.savefig(OUT / "18_lr_ffe_mlsd_fec.png", dpi=130)
print(f"wrote {OUT / '18_lr_ffe_mlsd_fec.png'}")
