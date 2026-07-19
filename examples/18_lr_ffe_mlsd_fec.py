"""Realistic 224G long-reach: FFE + MLSD + KP4 FEC.

At medium/long-reach loss (27-35 dB @ 56 GHz Nyquist), a linear-EQ-only
ADC receiver leaves pre-FEC BER above the KP4 waterfall (~2.4e-4): FFE that
fully inverts the channel enhances noise too much, and DFE propagates errors.
The 224G-LR answer is FFE-to-a-short-residual + MLSD (Viterbi MLSE over the
residual ISI, no error propagation, sequence gain) + KP4 FEC.

This sweep runs the ADC link across LR channel lengths and compares, at each
loss: FFE-only slicer BER vs FFE+MLSD BER, against the KP4 pre-FEC waterfall,
with post-KP4 projection.
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
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.prbs import symbol_checker  # noqa: E402
from halo_serdes.dsp import viterbi_mlsd  # noqa: E402
from halo_serdes.dsp.kernels import slice_nearest  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.static_link import make_pattern  # noqa: E402
from halo_serdes.fec import pre_to_post_fec_ber  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

KP4_WATERFALL = 2.4e-4  # pre-FEC BER below which KP4 -> post-FEC << 1e-12


def make_cfg(length_m: float, n_sym: int = 400_000) -> LinkConfig:
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,  # 224 Gb/s
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, rj_ui=0.004, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    # FFE tuned to leave a short residual for the MLSD; no DFE
                    ffe=FfeConfig(n_pre=6, n_post=14, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"),
    )


def evaluate(length_m: float):
    cfg = make_cfg(length_m)
    cm = ChannelModel.from_config(cfg)
    loss = cm.loss_at(56e9)
    res = run_time_link(cfg, channel=cm)
    levels = res.extras["levels"]
    warm = res.extras["warmup"]
    y = res.y_slicer
    ref = make_pattern(cfg)[warm: warm + y.size]

    # slicer (FFE-only) BER
    ber_ffe = symbol_checker(ref, slice_nearest(y, levels)).ber

    # residual channel estimate (LMMSE fit of the slicer stream)
    ld = levels[ref]
    cols = [ld] + [np.concatenate([np.zeros(i), ld[:-i]]) for i in (1, 2, 3)]
    coef, *_ = np.linalg.lstsq(np.vstack(cols).T, y, rcond=None)

    # MLSD (Viterbi MLSE) over memory-2 residual
    dec_m = viterbi_mlsd(y.astype(np.float64), levels.astype(np.float64), coef[:3])
    ber_mlsd = symbol_checker(ref, dec_m).ber

    return {"loss": loss, "snr": res.slicer_snr_db, "resid": coef[:4] / coef[0],
            "ber_ffe": ber_ffe, "ber_mlsd": ber_mlsd}


lengths = [0.15, 0.17, 0.18, 0.19, 0.20, 0.22]
rows = []
print("224 Gb/s PAM4 LR: FFE-only vs FFE+MLSD (memory 2), + KP4 projection")
print(f"{'Loss@Nyq':>9} {'Class':>5} {'SNR':>6} {'FFE BER':>10} {'MLSD BER':>10} "
      f"{'MLSD gain':>8}  {'post-KP4(MLSD)':>13}")
for L in lengths:
    t0 = time.time()
    r = evaluate(L)
    rows.append(r)
    reach = "C2M" if r["loss"] > -25 else ("MR" if r["loss"] > -35 else "LR")
    gain = r["ber_ffe"] / max(r["ber_mlsd"], 1e-9)
    post = pre_to_post_fec_ber(max(r["ber_mlsd"], 1e-9), "kp4")
    print(f"{r['loss']:8.1f}dB {reach:>5} {r['snr']:5.1f}dB {r['ber_ffe']:9.2e} "
          f"{r['ber_mlsd']:9.2e} {gain:6.1f}x  {post:12.1e}  [{time.time()-t0:.0f}s]")

# ------------------------------------------------------------------ plot ---
loss = np.array([r["loss"] for r in rows])
ffe = np.array([max(r["ber_ffe"], 5e-7) for r in rows])
mlsd = np.array([max(r["ber_mlsd"], 5e-7) for r in rows])
post_ffe = np.array([pre_to_post_fec_ber(max(r["ber_ffe"], 1e-9), "kp4") for r in rows])
post_mlsd = np.array([pre_to_post_fec_ber(max(r["ber_mlsd"], 1e-9), "kp4") for r in rows])

from matplotlib.ticker import FuncFormatter  # noqa: E402

_fmt = FuncFormatter(lambda v, _: f"{v:.0e}")
_nofmt = FuncFormatter(lambda v, _: "")

fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
ax = axes[0]
ax.semilogy(-loss, ffe, "o-", color="C1", label="FFE-only (slicer)")
ax.semilogy(-loss, mlsd, "s-", color="C0", label="FFE + MLSD (memory 2)")
ax.axhline(KP4_WATERFALL, color="r", ls="--", lw=1, label="KP4 pre-FEC waterfall 2.4e-4")
ax.axvspan(35, 46, color="gray", alpha=0.10)
ax.text(35.3, 2e-6, "LR (>35 dB)", fontsize=7, color="gray")
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="pre-FEC BER",
       title="Deep ISI beyond FFE's reach is compensated by MLSD\n(224 Gb/s PAM4, ADC arch)")
ax.yaxis.set_major_formatter(_fmt)
ax.yaxis.set_minor_formatter(_nofmt)
ax.legend(fontsize=8)
ax.grid(True, which="both", alpha=0.3)

ax = axes[1]
ax.semilogy(-loss, np.maximum(post_ffe, 1e-30), "o-", color="C1",
            label="post-KP4 (FFE-only)")
ax.semilogy(-loss, np.maximum(post_mlsd, 1e-30), "s-", color="C0",
            label="post-KP4 (FFE + MLSD)")
ax.axhline(1e-15, color="green", ls=":", lw=1, label="link target 1e-15")
ax.set(xlabel="Channel insertion loss @ 56 GHz Nyquist [dB]", ylabel="post-FEC BER (KP4)",
       title="MLSD pulls pre-FEC below the waterfall,\nso KP4 can reach 1e-15")
ax.yaxis.set_major_formatter(_fmt)
ax.yaxis.set_minor_formatter(_nofmt)
ax.legend(fontsize=8)
ax.grid(True, which="both", alpha=0.3)

fig.tight_layout()
fig.savefig(OUT / "18_lr_ffe_mlsd_fec.png", dpi=130)
print(f"wrote {OUT / '18_lr_ffe_mlsd_fec.png'}")
