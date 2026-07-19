"""Phase 4 deliverable: 224G-class PAM4 ADC-based RX.

- 106.25 GBd (802.3dj anchor) and 112 GBd (stress) full dynamic runs,
  1e6 symbols each: TI-ADC (8b/16-lane, ENOB, mismatch) -> 15-tap FFE +
  1-tap DFE (802.3dj reference receiver) -> MM-CDR;
- first-sensitivity sweeps: ADC bit width and TI skew vs slicer SNR / SER.

Primary metrics: slicer-input SNR and SER (post-DSP eyes carry little info).
"""

import dataclasses
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)


def make_cfg(rate=106.25e9, osr=32, n_symbols=1_000_000, **adc_over) -> LinkConfig:
    adc_kw = dict(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6,
                  skew_sigma_ui=0.005, offset_sigma=0.001)
    adc_kw.update(adc_over)
    return LinkConfig(
        modulation="pam4", symbol_rate=rate, osr=osr,
        channel=ChannelConfig(kind="analytic", length_m=0.12, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, rj_ui=0.005, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=4.0),
                    adc=AdcConfig(**adc_kw),
                    ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                    dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_symbols, seed=3, pattern="prbs13q"),
    )


# ---------------------------------------------------------- headline runs ---
print("== ADC-based PAM4 RX: 1e6-symbol runs ==")
results = {}
for rate, osr in ((106.25e9, 32), (112e9, 16)):
    cfg = make_cfg(rate, osr)
    t0 = time.time()
    res = run_time_link(cfg)
    dt = time.time() - t0
    results[rate] = res
    print(f"  {rate / 1e9:7.2f} GBd (osr {osr:2d}): {res.summary()}  [{dt:.1f}s]")
    print(f"           lane SER: min {res.extras['lane_ser'].min():.2e} "
          f"max {res.extras['lane_ser'].max():.2e}")

# --------------------------------------------------- sensitivity sweeps ---
print("== sensitivity sweeps (2e5 symbols each) ==")
bits_axis = [5, 6, 7, 8, 9]
snr_bits, ser_bits = [], []
for b in bits_axis:
    r = run_time_link(make_cfg(n_symbols=200_000, n_bits=b, enob=b - 1.5))
    snr_bits.append(r.slicer_snr_db)
    ser_bits.append(max(r.ser, 4e-6))
    print(f"  n_bits={b}: SNR={r.slicer_snr_db:.1f} dB, SER={r.ser:.2e}")

skew_axis = [0.0, 0.005, 0.01, 0.02, 0.04]
snr_skew, ser_skew = [], []
for s in skew_axis:
    r = run_time_link(make_cfg(n_symbols=200_000, skew_sigma_ui=s))
    snr_skew.append(r.slicer_snr_db)
    ser_skew.append(max(r.ser, 4e-6))
    print(f"  skew={s} UI: SNR={r.slicer_snr_db:.1f} dB, SER={r.ser:.2e}")

# ----------------------------------------------------------------- plots ---
fig, axes = plt.subplots(1, 3, figsize=(15, 4))
res = results[106.25e9]
axes[0].hist(res.y_slicer, bins=300, color="C0")
for lv in res.extras["levels"]:
    axes[0].axvline(lv, ls="--", c="r", alpha=0.5)
axes[0].set(title="Slicer input, 106.25 GBd (4 PAM4 levels)", xlabel="V")

ax1b = axes[1].twinx()
axes[1].plot(bits_axis, snr_bits, "o-", color="C0")
ax1b.semilogy(bits_axis, ser_bits, "s--", color="C1")
axes[1].set(xlabel="ADC bits (ENOB tracks -1.5b)", ylabel="slicer SNR [dB]",
            title="ADC resolution sensitivity")
ax1b.set_ylabel("SER", color="C1")

ax2b = axes[2].twinx()
axes[2].plot(np.asarray(skew_axis) * 100, snr_skew, "o-", color="C0")
ax2b.semilogy(np.asarray(skew_axis) * 100, ser_skew, "s--", color="C1")
axes[2].set(xlabel="TI skew sigma [%UI]", ylabel="slicer SNR [dB]",
            title="Interleave skew sensitivity")
ax2b.set_ylabel("SER", color="C1")

for ax in axes:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "05_adc_rx_pam4.png", dpi=130)
print(f"  wrote {OUT / '05_adc_rx_pam4.png'}")
