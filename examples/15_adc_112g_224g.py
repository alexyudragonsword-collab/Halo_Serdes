"""ADC-based PAM4 results at 112 Gb/s (56 GBd) and 224 Gb/s (112 GBd).

Same physical trace for both rates (loss scales with Nyquist), 802.3dj
reference receiver: light CTLE -> 8b/16-lane TI-ADC (ENOB 6.5) ->
15-tap LMS FFE + 1-tap DFE -> MM-CDR. 1e6 symbols each.

Per rate: slicer histogram (the ADC-arch primary view), per-lane SER
(TI mismatch diagnostics), converged FFE taps; printed BER + KP4 projection.
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
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.fec import pre_to_post_fec_ber  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

TRACE = ChannelConfig(kind="analytic", length_m=0.12, rdc=5.0,
                      r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192)


def make_cfg(rate_gbps: float) -> LinkConfig:
    return LinkConfig(
        modulation="pam4", symbol_rate=rate_gbps * 1e9 / 2, osr=16,
        channel=TRACE,
        tx=TxConfig(swing=1.0, rj_ui=0.005, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=4.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6,
                                  skew_sigma_ui=0.005, offset_sigma=0.001),
                    ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                    dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=1_000_000, seed=3, pattern="prbs13q"),
    )


fig, axes = plt.subplots(2, 3, figsize=(15.5, 8))
for row, rate in enumerate((112.0, 224.0)):
    cfg = make_cfg(rate)
    cm = ChannelModel.from_config(cfg)
    loss = cm.loss_at(cfg.f_nyquist)
    t0 = time.time()
    res = run_time_link(cfg, channel=cm)
    wall = time.time() - t0
    pre = max(res.ber.ber, 1e-9)
    post = pre_to_post_fec_ber(pre, "kp4")

    print(f"== PAM4 {rate:.0f} Gb/s ({cfg.symbol_rate / 1e9:.0f} GBd, "
          f"{loss:.1f} dB @ Nyquist) ==")
    print(f"  {res.summary()}  [{wall:.1f}s]")
    print(f"  lane SER: min {res.extras['lane_ser'].min():.2e}  "
          f"max {res.extras['lane_ser'].max():.2e}")
    print(f"  pre-FEC BER {pre:.2e} -> post-KP4 {post:.2e}"
          f"  {'(PASS: below 1e-15)' if post < 1e-15 else ''}")

    ax = axes[row, 0]
    ax.hist(res.y_slicer, bins=300, color="C0")
    for lv in res.extras["levels"]:
        ax.axvline(lv, ls="--", c="r", alpha=0.5)
    ax.set(xlabel="V", title=f"{rate:.0f} Gb/s: slicer histogram "
                             f"(SNR {res.slicer_snr_db:.1f} dB, "
                             f"SER {res.ser:.1e})")

    ax = axes[row, 1]
    from matplotlib.ticker import FuncFormatter

    lane_ser = np.maximum(res.extras["lane_ser"], 2e-7)
    ax.bar(np.arange(16), lane_ser, color="C1")
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0e}"))
    ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
    ax.set(xlabel="TI lane", ylabel="SER",
           title=f"{rate:.0f} Gb/s: per-lane SER (skew/offset mismatch)")

    ax = axes[row, 2]
    taps = res.ffe_taps
    k = np.arange(taps.size) - cfg.rx.ffe.n_pre
    ax.stem(k, taps)
    ax.set(xlabel="tap position [UI]", ylabel="weight",
           title=f"{rate:.0f} Gb/s: converged FFE 15 taps "
                 f"(DFE1={res.dfe_taps[0]:+.3f})")

for ax in axes.flat:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "15_adc_112g_224g.png", dpi=130)
print(f"wrote {OUT / '15_adc_112g_224g.png'}")
