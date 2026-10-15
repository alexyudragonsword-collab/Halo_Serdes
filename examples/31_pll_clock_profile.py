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
import warnings
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

# kp_shift 8-9 ring longer than the run: the statistical engine says so, and
# the "steady" column below is what it says
warnings.filterwarnings("ignore", message="the CDR loop rings")

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


def fs(x: float) -> str:
    return "n/a" if np.isnan(x) else f"{x:.0f}"


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
        # the whole scored window, as the model takes it: a loop that rings
        # up keeps growing through the run, so its second half reads high
        err = cdr_tracking_error_s(cfg, res)
        stat = run_statistical(cfg, channel=cm, ffe_taps=res.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
        sol = stat.extras.get("clock_loop")
        rows.append({
            "clock": clock_name, "kp_shift": kp,
            "ber": res.ber.ber, "n_err": res.ber.n_errors,
            "eye_v": inner_eye_height(res.y_slicer, res.extras["levels"]),
            "resid_meas_fs": float(np.std(err)) * 1e15,
            # no model for a white clock: its RJ reaches the statistical
            # engine edge by edge, as noise, not as a sampling error
            "resid_model_fs": (sol.sigma_ui * cfg.ui * 1e15) if sol is not None else float("nan"),
            # the same loop once it has rung up: a longer run, the link in service
            "steady_fs": (sol.sigma_steady_ui * cfg.ui * 1e15) if sol is not None else float("nan"),
            "bw_mhz": sol.bandwidth_hz / 1e6 if sol is not None else float("nan"),
            "stat_ber": stat.ber,
        })
        r = rows[-1]
        print(f"{clock_name:14s} kp_shift={kp}: BER {r['ber']:.2e} ({r['n_err']})  eye {r['eye_v'] * 1e3:.1f} mV  "
              f"CDR error {r['resid_meas_fs']:.0f} fs (model {fs(r['resid_model_fs'])}, steady {fs(r['steady_fs'])})  "
              f"bw {r['bw_mhz']:.2f} MHz  stat BER {r['stat_ber']:.1e}  [{time.time() - t0:.1f}s]")

print(f"\n{len(rows)} runs in {time.time() - t_all:.0f}s\n")
print(f"{'clock':14s} | best kp_shift | BER at best | eye height | CDR error meas / model [fs] | loop bw")
print("-" * 100)
for clock_name in CLOCKS:
    sub = [r for r in rows if r["clock"] == clock_name]
    best = min(sub, key=lambda r: (r["ber"], -r["eye_v"]))
    print(f"{clock_name:14s} | {best['kp_shift']:13d} | {best['ber']:.2e}    | {best['eye_v'] * 1e3:6.1f} mV  "
          f"| {best['resid_meas_fs']:6.0f} / {fs(best['resid_model_fs']):>6s}            | {best['bw_mhz']:.2f} MHz")
print("\nThe white clock's CDR error is the loop's own hunting plus the white edge jitter it\n"
      "cannot follow; there is no model column for it: the statistical engine builds no CDR for\n"
      "kind=white and takes the transmitter's RJ edge by edge, as noise through the channel,\n"
      "not as a sampling error. For the PLL clocks the model column is the linearised loop's\n"
      "sampling-instant sigma over a run this long, the loop starting from rest, and 'steady'\n"
      "the same loop once it has rung up. From kp_shift 7 the loop is underdamped (its error\n"
      "response peaks 4-16x near 1.1 MHz) and rings up far slower than 200k symbols last, so a\n"
      "run holds 0.58-0.86x of its steady state. The measured column is one seed, and what the\n"
      "loop leaves sits within a decade of its 0.7 MHz bandwidth, a few cycles a run: one seed\n"
      "scatters 0.32-1.02 samples at kp_shift 9. This one reads 1.10-1.38x the model; the\n"
      "16-seed RMS is 1.00-1.04x (until 2026-10-15 the model was the steady state, 0.81-1.07x\n"
      "this seed's second half by chance and 1.14-1.70x over the 16 seeds at kp_shift 7-9;\n"
      "ROADMAP P2 #28). Its Mueller-Muller detector reads the slicer input after the DFE's\n"
      "feedback, as the kernel does, and a loop this slow sees the detector noise's\n"
      "low-frequency density -- on PRBS13Q the receiver noise's alone, the pattern's share\n"
      "sitting on lines from 13.7 MHz up (ROADMAP P2 #24). The statistical BER, its taps held\n"
      "at the lock point under the smear and the curve taken between samples, is 1.2-1.6x the\n"
      "time engine's 4-6 errors at kp_shift 8 and 0.8-1.4x its 11-17 at 9; over twelve seeds\n"
      "0.69-0.85x, the time engine's LMS FFE following part of the slow wander. On the white\n"
      "clock it is 4.5e-6 against the time engine's 4-5 errors here and 8.2e-6 over six seeds\n"
      "(ROADMAP P2 #25: as a sampling-instant smear the jitter read 2.4e-7, a thirtieth).")

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
