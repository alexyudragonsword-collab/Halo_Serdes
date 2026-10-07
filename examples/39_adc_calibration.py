"""TI-ADC background calibration: what the step buys and what it costs.

``adc.calibrated = True`` is the ideal model -- the lane mismatch is drawn
and zeroed, so it is the same link with none. ``adc.cal.mode = "background"`` is an algorithm: each lane's
offset (its running mean) and gain (its running power against the lanes'
mean) are estimated from the data and corrected after the quantizer, every
conversion of that lane, with a one-pole step mu (part 2 adds lane skew and
the per-lane delay trims, ``adc.cal.mu_skew``; part 3 the power-up
foreground mode). Two things follow:

* settling: one time constant is 1 / mu conversions of a lane, i.e.
  n_lanes / mu symbols -- 16k symbols at 2^-10 for 16 lanes;
* residual: the estimates keep a share of the data's own variance, about
  sqrt(mu / 2) of the signal's rms for the offset -- the step halves the
  residual for every factor of 4 it is made smaller.

Steady state is read with the first three quarters of each run unscored
(``sim.warmup_discard``). Same 112 GBd PAM4 receiver as the unit tests:
16 lanes, offset sigma 10 mV, gain sigma 3 %; part 1 without skew.

Pass ``--quick`` for a smoke run (fewer symbols, fewer steps).
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from halo_serdes.channel import ChannelModel
from halo_serdes.config import (
    AdcCalConfig,
    AdcConfig,
    CdrConfig,
    ChannelConfig,
    CtleConfig,
    DfeConfig,
    FfeConfig,
    LinkConfig,
    RxConfig,
    SimConfig,
    TxConfig,
)
from halo_serdes.engine import run_time_link

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)
QUICK = "--quick" in sys.argv

N = 200_000 if QUICK else 1_000_000
STEPS = (8, 12) if QUICK else (8, 10, 12, 14)
LANES = 16
MISMATCH = dict(offset_sigma=0.01, gain_sigma=0.03)


def make_cfg(**adc) -> LinkConfig:
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.12, rdc=5.0, r_skin=2.0e-3,
                              loss_tangent=0.012, n_freq=4096),
        tx=TxConfig(swing=1.0, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                    adc=AdcConfig(n_bits=8, n_lanes=LANES, fullscale=0.6,
                                  **{"enob": 6.5, **MISMATCH, **adc}),
                    ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                    dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=N, seed=3, pattern="prbs13q", warmup_discard=3 * N // 4))


cm = ChannelModel.from_config(make_cfg())
raw = run_time_link(make_cfg(), channel=cm)
ideal = run_time_link(make_cfg(calibrated=True), channel=cm)
a = raw.extras["adc"]
off0 = np.std(a.offsets)
g0 = np.std(a.gains / a.gains.mean())
print(f"== TI-ADC background calibration, {N:,} symbols, last quarter scored ==")
print(f"  drawn mismatch: offset rms {off0 * 1e3:.2f} mV, gain rms {g0 * 100:.2f} %")
print(f"  uncalibrated        SNR {raw.slicer_snr_db:6.2f} dB")
print(f"  ideal (calibrated)  SNR {ideal.slicer_snr_db:6.2f} dB")
print(f"  {'step':>6} {'tau [sym]':>10} {'SNR [dB]':>9} {'offset left':>12} {'gain left':>10}")
rows = []
for e in STEPS:
    mu = 2.0 ** -e
    r = run_time_link(make_cfg(cal=AdcCalConfig("background", mu, mu)), channel=cm)
    cal = r.extras["adc_cal"]
    g = a.gains * cal[2]
    off = float(np.std(a.offsets - cal[0]))
    gain = float(np.std(g / g.mean()))
    rows.append((mu, r.slicer_snr_db, off, gain))
    print(f"  2^-{e:<3d} {LANES / mu:10,.0f} {r.slicer_snr_db:9.2f} {off * 1e3:9.2f} mV "
          f"{gain * 100:8.2f} %")

mu, snr, off, gain = (np.array(x) for x in zip(*rows))
fig, ax = plt.subplots(1, 2, figsize=(10, 3.8))
ax[0].semilogx(mu, snr, "o-", base=2, label="background")
ax[0].axhline(ideal.slicer_snr_db, color="g", ls="--", label="ideal")
ax[0].axhline(raw.slicer_snr_db, color="r", ls=":", label="uncalibrated")
ax[0].set(xlabel="step mu", ylabel="steady-state slicer SNR [dB]", title="what the step costs")
ax[0].legend()
ax[1].loglog(mu, off * 1e3, "o-", base=2, label="offset [mV]")
ax[1].loglog(mu, gain * 100, "s-", base=2, label="gain [%]")
ax[1].loglog(mu, off[-1] * 1e3 * np.sqrt(mu / mu[-1]), "k:", base=2, label="sqrt(mu)")
ax[1].set(xlabel="step mu", ylabel="residual rms", title="what is left")
ax[1].legend()
fig.tight_layout()
fig.savefig(OUT / "39_adc_calibration.png", dpi=130)
print(f"  wrote {OUT / '39_adc_calibration.png'}")

# --------------------------------------------------- part 2: skew as well ---
# Add 0.04 UI rms of lane skew. Offset + gain alone leave it in (they can
# only get to the ideal offset / gain model); the skew trims -- each lane's
# own Mueller-Muller detector, kept zero-mean against the CDR -- take it out.
SKEW = 0.04
print(f"\n== with {SKEW} UI rms lane skew as well (offset / gain step 2^-12) ==")
part2 = {
    "uncalibrated": make_cfg(skew_sigma_ui=SKEW),
    "ideal offset / gain": make_cfg(skew_sigma_ui=SKEW, calibrated=True),
    "offset + gain": make_cfg(skew_sigma_ui=SKEW,
                              cal=AdcCalConfig("background", 2.0 ** -12, 2.0 ** -12)),
    "offset + gain + skew 2^-8": make_cfg(
        skew_sigma_ui=SKEW, cal=AdcCalConfig("background", 2.0 ** -12, 2.0 ** -12, 2.0 ** -8)),
}
for name, c in part2.items():
    r = run_time_link(c, channel=cm)
    s = f"  {name:27s} SNR {r.slicer_snr_db:6.2f} dB"
    if r.extras["adc_cal"] is not None and r.extras["adc_cal"][4].any():
        a = r.extras["adc"]
        true = (a.skews - a.skews.mean()) / 16
        s += (f"   skew {np.std(true):.4f} UI rms -> "
              f"{np.std(true - r.extras['adc_cal'][4] / 16):.4f} UI left")
    print(s)

# ------------------------------------------- part 3: foreground instead ---
# ``adc.cal.mode = "foreground"`` measures each lane once at power-up -- a
# shorted input for the offset, +/- fg_ref of half full scale for the gain --
# through the lane's own quantizer and noise, then freezes the correction.
# The ENOB noise dithers the quantizer, so averaging fg_samples conversions
# keeps paying; with no noise every conversion of a constant gives the same
# code and the estimate stops at the quantizer's own error (about q / sqrt 12).
FG = (64, 1024) if QUICK else (64, 1024, 16384)
for enob in (6.5, None):
    print(f"\n== foreground calibration, ENOB {enob or 'off (quantizer only)'} ==")
    for name, extra in (("ideal offset / gain", dict(calibrated=True)),
                        ("background 2^-12", dict(cal=AdcCalConfig("background", 2.0 ** -12,
                                                                    2.0 ** -12)))):
        r = run_time_link(make_cfg(enob=enob, **extra), channel=cm)
        print(f"  {name:27s} SNR {r.slicer_snr_db:6.2f} dB")
    for m in FG:
        r = run_time_link(make_cfg(enob=enob, cal=AdcCalConfig("foreground", fg_samples=m)),
                          channel=cm)
        a = r.extras["adc"]
        print(f"  foreground {m:6d} samples    SNR {r.slicer_snr_db:6.2f} dB   offset left "
              f"{np.std(a.offsets) * 1e3:5.2f} mV, gain left {np.std(a.gains / a.gains.mean()) * 100:.3f} %")
