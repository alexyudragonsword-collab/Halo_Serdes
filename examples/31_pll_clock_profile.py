"""Three transmit clocks with the same 200 fs RMS jitter on one PAM4 112 GBd link.

The same number on a datasheet, three different clocks:

    white    independent Gaussian edge noise (``rj_ui``), the spec-sheet model
    SSPLL    pll_simulator's sub-sampling PLL anchor (Markulic 2016, 10.24 GHz),
             flat in-band to a 40 MHz loop bandwidth then 1/f^2, scaled to 200 fs
    CPPLL    pll_simulator's charge-pump PLL anchor (Dadalt 2003, 2.488 GHz),
             a much narrower loop, so most of the 200 fs sits close to the carrier

A CDR is a high-pass to clock jitter: wander inside its bandwidth is followed,
the rest lands on the sampler. So the three clocks are swept against the CDR's
proportional gain (``kp_shift``) and, per point, the time engine reports BER,
inner eye height at the slicer and the measured CDR tracking error, while the
statistical engine reports the sampling-instant sigma its linearised loop
model predicts (``cdr/linear.py``). The table at the end is the deliverable:
which clock, at which loop gain, and how far the two engines are apart.

The two PLL profiles are written to ``examples/output/`` scaled from the
shipped files (``data/clock_profiles/``) by a uniform dB shift, so their
*shape* is what differs.
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

import dataclasses  # noqa: E402

from halo_serdes.analysis.cdr_tracking import cdr_tracking_error_s  # noqa: E402
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig,
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.statistical import run_statistical  # noqa: E402
from halo_serdes.tx.clock import ClockProfile  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

TARGET_RMS_S = 200e-15
N_SYMBOLS = 200_000
KP_SHIFTS = (4, 5, 6, 7, 8, 9)

# The 224 Gb/s PAM4 stress link (112 GBd, UI 8.93 ps): light CTLE -> 16-lane
# TI-ADC -> LMS FFE + 1-tap DFE -> MM-CDR. Same as configs/pam4_224g_112g_adc.yaml.
BASE = LinkConfig(
    modulation="pam4", symbol_rate=112.0e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.13, rdc=5.0, r_skin=2.5e-3,
                          l_per_m=3.0e-7, c_per_m=1.2e-10, loss_tangent=0.015, n_freq=8192),
    tx=TxConfig(swing=1.0, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
    rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=5.0),
                adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5),
                ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms"),
                dfe=DfeConfig(n_taps=1, adapt="lms"),
                cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15, pd_input="ffe"),
                noise_rms=0.0015),
    sim=SimConfig(n_symbols=N_SYMBOLS, seed=3, pattern="prbs13q"))

F_LO = BASE.symbol_rate / N_SYMBOLS      # lowest offset a run of N UI resolves


def scaled_profile(name: str) -> str:
    prof = ClockProfile.load(REPO / "data" / "clock_profiles" / f"{name}.yaml")
    scaled = prof.scaled_to_rms(TARGET_RMS_S, F_LO)
    path = OUT / f"31_{name}_200fs.yaml"
    scaled.save(path, source=f"{prof.source}; scaled to {TARGET_RMS_S * 1e15:.0f} fs RMS "
                             f"over [{F_LO / 1e6:.2f} MHz, f0/2] by examples/31")
    return str(path)


CLOCKS = {
    "white 200 fs": ClockConfig(rj_ui=TARGET_RMS_S / BASE.ui),
    "SSPLL 200 fs": ClockConfig(kind="profile",
                                file=scaled_profile("bench_markulic16_sspll_40m_10p24g")),
    "CPPLL 200 fs": ClockConfig(kind="profile",
                                file=scaled_profile("bench_dadalt03_cppll_311m_2p488g")),
}


def inner_eye_height(y_slicer: np.ndarray, levels: np.ndarray) -> float:
    """Smallest gap between adjacent decision clusters at the slicer, using the
    1st/99th percentiles so a few errors do not close it to zero."""
    lv = np.sort(levels)
    dec = np.argmin(np.abs(y_slicer[:, None] - lv[None, :]), axis=1)
    gaps = []
    for j in range(lv.size - 1):
        lo, hi = y_slicer[dec == j], y_slicer[dec == j + 1]
        if lo.size and hi.size:
            gaps.append(np.percentile(hi, 1) - np.percentile(lo, 99))
    return float(min(gaps)) if gaps else float("nan")


cm = ChannelModel.from_config(BASE)
rows = []
t_all = time.time()
for clock_name, clock in CLOCKS.items():
    for kp in KP_SHIFTS:
        cfg = dataclasses.replace(
            BASE, tx=dataclasses.replace(BASE.tx, clock=clock),
            rx=dataclasses.replace(BASE.rx, cdr=dataclasses.replace(BASE.rx.cdr, kp_shift=kp)))
        t0 = time.time()
        res = run_time_link(cfg, channel=cm)
        err = cdr_tracking_error_s(cfg, res, skip=N_SYMBOLS // 2)
        stat = run_statistical(cfg, channel=cm, ffe_taps=res.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
        sol = stat.extras.get("clock_loop")
        rows.append({
            "clock": clock_name, "kp_shift": kp,
            "ber": res.ber.ber, "n_err": res.ber.n_errors,
            "eye_v": inner_eye_height(res.y_slicer, res.extras["levels"]),
            "resid_meas_fs": float(np.std(err)) * 1e15,
            "resid_model_fs": (sol.sigma_ui * cfg.ui * 1e15) if sol is not None
                              else stat.extras["jitter_sigma_ui"] * cfg.ui * 1e15,
            "bw_mhz": sol.bandwidth_hz / 1e6 if sol is not None else float("nan"),
            "stat_ber": stat.ber,
        })
        r = rows[-1]
        print(f"{clock_name:14s} kp_shift={kp}: BER {r['ber']:.2e} ({r['n_err']})  eye {r['eye_v'] * 1e3:.1f} mV  "
              f"CDR error {r['resid_meas_fs']:.0f} fs (model {r['resid_model_fs']:.0f})  "
              f"bw {r['bw_mhz']:.2f} MHz  stat BER {r['stat_ber']:.1e}  [{time.time() - t0:.1f}s]")

print(f"\n{len(rows)} runs in {time.time() - t_all:.0f}s\n")
print(f"{'clock':14s} | best kp_shift | BER at best | eye height | CDR error meas / model [fs] | loop bw")
print("-" * 100)
for clock_name in CLOCKS:
    sub = [r for r in rows if r["clock"] == clock_name]
    best = min(sub, key=lambda r: (r["ber"], -r["eye_v"]))
    print(f"{clock_name:14s} | {best['kp_shift']:13d} | {best['ber']:.2e}    | {best['eye_v'] * 1e3:6.1f} mV  "
          f"| {best['resid_meas_fs']:6.0f} / {best['resid_model_fs']:6.0f}            | {best['bw_mhz']:.2f} MHz")
print("\nThe white clock's CDR error is the loop's own hunting plus untracked white noise; the\n"
      "model column for it is the rj_ui smear the statistical engine applies (no CDR model for\n"
      "kind=white, by design). For the PLL clocks the model column is the linearised loop's\n"
      "sampling-instant sigma; the statistical BER is listed for the record -- on this ADC link\n"
      "it is not expected to match the time engine (TI-ADC, quantisation and adaptation are\n"
      "approximations there), the comparison that is in scope is the CDR error.")

# ---------------------------------------------------------------- figure
fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
for clock_name, color in zip(CLOCKS, ("C7", "C0", "C3")):
    sub = [r for r in rows if r["clock"] == clock_name]
    kps = [r["kp_shift"] for r in sub]
    axes[0].semilogy(kps, [max(r["ber"], 1e-7) for r in sub], "o-", color=color, label=clock_name)
    axes[1].plot(kps, [r["eye_v"] * 1e3 for r in sub], "o-", color=color, label=clock_name)
    axes[2].plot(kps, [r["resid_meas_fs"] for r in sub], "o-", color=color, label=f"{clock_name} measured")
    if "PLL" in clock_name:
        axes[2].plot(kps, [r["resid_model_fs"] for r in sub], "s--", color=color, alpha=0.6,
                     label=f"{clock_name} model")
axes[0].set(xlabel="kp_shift (loop gain 2^-kp_shift)", ylabel="BER", title="BER vs CDR gain")
axes[1].set(xlabel="kp_shift", ylabel="inner eye height at slicer [mV]", title="Eye height")
axes[2].set(xlabel="kp_shift", ylabel="CDR tracking error [fs RMS]",
            title="Sampling jitter: time engine vs loop model")
for ax in axes:
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
fig.suptitle("Same 200 fs RMS, three clocks, PAM4 112 GBd ADC link")
fig.tight_layout()
fig.savefig(OUT / "31_pll_clock_profile.png", dpi=130)
print(f"wrote {OUT / '31_pll_clock_profile.png'}")
