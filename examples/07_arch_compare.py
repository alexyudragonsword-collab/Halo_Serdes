"""Phase 5 flagship: mixed-signal vs ADC-based RX on the same channels.

Sweeps channel loss (trace length) at 106.25 GBd PAM4 and runs both
architectures on identical Tx/channel/noise conditions:

- mixed-signal: CTLE (high peaking) + 3-tap adaptive analog DFE + BB-CDR;
- ADC-based: light CTLE + 8b/16-lane TI-ADC + 15-tap FFE + 1-tap DFE + MM-CDR
  (802.3dj reference receiver configuration).

Reports slicer SNR, SER, pre/post-FEC BER, and the power/complexity proxy
(taps x bit width). The expected physics: mixed-signal collapses beyond
~15 dB Nyquist loss while ADC/DSP keeps working — the reason the industry
moved to ADC-based architectures at 224G.
"""

import dataclasses
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

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

RATE = 106.25e9
N_SYM = 300_000

MS_RX = RxConfig(
    arch="mixed_signal",
    ctle=CtleConfig(enable=True, peak_db=9.0),
    dfe=DfeConfig(n_taps=3, adapt="sign_sign", mu=5e-4),
    cdr=CdrConfig(kind="bang_bang", kp_shift=5, ki_shift=12),
    noise_rms=0.0015,
)
ADC_RX = RxConfig(
    arch="adc_dsp",
    ctle=CtleConfig(enable=True, peak_db=4.0),
    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6,
                  skew_sigma_ui=0.005, offset_sigma=0.001),
    ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
    dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
    noise_rms=0.0015,
)


def channel_cfg(length_m: float) -> ChannelConfig:
    return ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                         r_skin=1.8e-3, loss_tangent=0.010, n_freq=8192)


def power_proxy(rx: RxConfig) -> int:
    """Complexity proxy: DSP multiplies x bit width (0 for analog taps)."""
    if rx.arch == "adc_dsp":
        n_ffe = rx.ffe.n_pre + 1 + rx.ffe.n_post
        return n_ffe * 10 * rx.adc.n_bits // 8 + rx.dfe.n_taps * 10
    return 0  # analog CTLE+DFE: no digital multiplies (different power axis)


lengths = [0.04, 0.08, 0.12, 0.16, 0.20]
rows = []
for L in lengths:
    ch_cfg = channel_cfg(L)
    probe = LinkConfig(modulation="pam4", symbol_rate=RATE, osr=16, channel=ch_cfg)
    cm = ChannelModel.from_config(probe)
    loss = cm.loss_at(RATE / 2)
    row = {"L": L, "loss": loss}
    for name, rx in (("ms", MS_RX), ("adc", ADC_RX)):
        cfg = LinkConfig(
            modulation="pam4", symbol_rate=RATE, osr=16,
            channel=ch_cfg,
            tx=TxConfig(swing=1.0, rj_ui=0.004, fir_taps=(-0.05, 1.0), fir_n_pre=1),
            rx=rx,
            sim=SimConfig(n_symbols=N_SYM, seed=21, pattern="prbs13q"),
        )
        res = run_time_link(cfg, channel=cm)
        row[name] = res
    rows.append(row)
    ms, adc = row["ms"], row["adc"]
    print(f"loss@Nyq {loss:6.1f} dB | MS : SNR {ms.slicer_snr_db:5.1f} dB SER {ms.ser:.2e} "
          f"| ADC: SNR {adc.slicer_snr_db:5.1f} dB SER {adc.ser:.2e}")

print()
print(f"power/complexity proxy (digital multiplies x bits): "
      f"MS = {power_proxy(MS_RX)}, ADC = {power_proxy(ADC_RX)}")
print("post-FEC (KP4) projection at the deepest common operating point:")
for name in ("ms", "adc"):
    pre = max(rows[-2][name].ber.ber, 1e-7)
    print(f"  {name}: pre-FEC {pre:.2e} -> post-FEC {pre_to_post_fec_ber(pre):.2e}")

# ------------------------------------------------------------------ plots ---
loss_axis = [r["loss"] for r in rows]
fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
for name, marker, label in (("ms", "o", "mixed-signal (CTLE+DFE)"),
                            ("adc", "s", "ADC-based (15T FFE+1T DFE)")):
    ser = [max(r[name].ser, 3e-6) for r in rows]
    snr = [r[name].slicer_snr_db for r in rows]
    axes[0].semilogy(loss_axis, ser, marker + "-", label=label)
    axes[1].plot(loss_axis, snr, marker + "-", label=label)
axes[0].axhline(2.4e-4, ls="--", c="gray", lw=0.8)
axes[0].text(loss_axis[0], 3e-4, "KP4 pre-FEC limit", fontsize=7, color="gray")
axes[0].set(xlabel="insertion loss @ 53.1 GHz [dB]", ylabel="SER",
            title="SER vs channel loss (106.25 GBd PAM4)")
axes[1].set(xlabel="insertion loss @ 53.1 GHz [dB]", ylabel="slicer SNR [dB]",
            title="Slicer SNR vs channel loss")
for ax in axes:
    ax.invert_xaxis()
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "07_arch_compare.png", dpi=130)
print(f"wrote {OUT / '07_arch_compare.png'}")
