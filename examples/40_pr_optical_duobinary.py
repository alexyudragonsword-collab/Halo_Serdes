"""Optical duobinary: on a band-limited modulator, where does partial response go?

Example 37 found transmit-side partial response worse than none on an
electrical link: the noise sits at the receiver, the receive FFE still has to
invert the channel down to a delta, and the transmitter pays the shaped
signal's peak. The case left open was a transmitter that is itself the
bandwidth limit -- the duobinary picture of an optical modulator, whose own
low-pass is the 1 + D filter -- with the noise either after it (relative
intensity noise, photodiode and TIA noise) or inside the transmitter, ahead of
the modulator's band limit (``tx.noise_rms``: white per UI at the DAC output).

The link is example 32's 850 nm VCSEL over 100 m of OM4, 53.125 GBd PAM4,
ADC receiver with a 17-tap LMS FFE, MM-CDR and a memory-2 Viterbi, no
electrical segments: the VCSEL's relaxation-oscillation frequency f_r is the
bandwidth limit. Five receivers / transmitters:

- control: delta target + Viterbi;
- RX PR a = 1 with 1/(1+D) precoding -- the classic duobinary receiver, the
  modulator's own low-pass doing the shaping;
- RX PR with the start-up MMSE a;
- TX PR a = 0.5 and a = 1 (precoded), shaped digitally before the DAC and
  peak-normalised.

1. Noise after the modulator: the lowest f_r at which the pre-FEC BER still
   meets KP4's threshold, without and with the host Tx FFE (pre-emphasis).
2. Noise in the transmitter (``tx.noise_rms``; RIN and TIA noise made
   negligible): the largest Tx noise at which KP4 still closes, at f_r = 12
   and 22 GHz.

Pass ``--quick`` for a smoke run (fewer symbols, coarser sweeps).
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

from halo_serdes.config import LinkConfig, PrConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, CtleConfig, DfeConfig, FfeConfig, MlsdConfig, OpticalConfig,
    RxConfig, SimConfig, TopologyConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.fec import fec_threshold, refine  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

QUICK = "--quick" in sys.argv
N_SYM = 60_000 if QUICK else 200_000
BAUD = 53.125e9
HOST_FFE = (-0.06, 0.68, -0.26)       # example 32's host Tx FFE
P_STAR = fec_threshold("kp4")

CASES = {
    "control (delta + Viterbi)": dict(),
    "RX PR a=1, precoded": dict(target=(1.0, 1.0), precode=True),
    "RX PR, MMSE a": dict(target=(1.0, 0.5), adapt="mmse"),
    "TX PR a=0.5": dict(target=(1.0, 0.5), at="tx"),
    "TX PR a=1, precoded": dict(target=(1.0, 1.0), at="tx", precode=True),
}


def make_cfg(f_r: float, target=(1.0,), at: str = "rx", adapt: str = "none",
             precode: bool = False, host_ffe: bool = False, tx_noise: float = 0.0,
             rin: float = -145.0, tia: float = 12.0, rx_noise: float = 1e-4,
             n_sym: int = N_SYM) -> LinkConfig:
    fir = HOST_FFE if host_ffe else (1.0,)
    return LinkConfig(
        modulation="pam4", symbol_rate=BAUD, osr=16, precode=precode,
        tx=TxConfig(swing=1.0, fir_taps=fir, fir_n_pre=1 if host_ffe else 0, noise_rms=tx_noise),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=3.0),
                    adc=AdcConfig(n_bits=10, n_lanes=16, enob=None, fullscale=0.3),
                    ffe=FfeConfig(n_pre=4, n_post=12, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    mlsd=MlsdConfig(kind="viterbi", memory=2),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=rx_noise),
        sim=SimConfig(n_symbols=n_sym, seed=7, pattern="prbs13q"),
        topology=TopologyConfig(optical=OpticalConfig(
            kind="vcsel_mmf", f_r_hz=f_r, damping_hz=30e9, er_db=4.0, oma_dbm=1.0,
            rin_db_hz=rin, length_m=100.0, modal_bw_mhz_km=4700.0, responsivity_a_w=0.7,
            tia_bw_hz=40e9, tia_noise_pa_sqrthz=tia, tz_ohm=2000.0)),
        pr=PrConfig(target=target, at=at, adapt=adapt))


def ber_of(cfg: LinkConfig) -> float:
    r = run_time_link(cfg)
    return max(r.ber.ber, 0.5 / r.ber.n_checked)      # no errors: half a count


def crossing(grid, measure, tol):
    """Where the pre-FEC BER crosses KP4's threshold along ``grid`` (the swept
    value increasing with the BER), refined to ``tol``."""
    r = refine({p: measure(p) for p in grid}, measure, P_STAR, tol=tol)
    if r.value is not None:
        return r.value, ""
    return (r.hi, "below sweep") if r.note == "below sweep" else (r.lo, "above sweep")


print("53.125 GBd PAM4, VCSEL + 100 m OM4, ADC RX + memory-2 Viterbi; KP4 pre-FEC "
      f"threshold {P_STAR:.2e}")

# ---- 1. noise after the modulator: how narrow can the VCSEL be? ----------
FR_GRID = [9, 11, 13, 15, 17, 20] if not QUICK else [10, 14, 18]
FR_GRID_FFE = [5, 6, 7, 8, 9, 10, 12] if not QUICK else [6, 9, 12]
min_fr = {}
for host_ffe, grid in ((False, FR_GRID), (True, FR_GRID_FFE)):
    tag = "with host Tx FFE" if host_ffe else "no Tx FFE"
    print(f"\n== noise after the modulator (RIN -145 dB/Hz, TIA 12 pA/rtHz), {tag}: lowest f_r")
    for name, kw in CASES.items():
        t0 = time.time()

        def measure(p, kw=kw, host_ffe=host_ffe):   # p = -f_r [GHz]: BER grows with it
            return p, ber_of(make_cfg(-p * 1e9, host_ffe=host_ffe, **kw))

        v, note = crossing([-g for g in grid], measure, tol=0.5)
        min_fr[(host_ffe, name)] = -v
        print(f"  {name:<26} f_r >= {-v:5.2f} GHz  {note}  [{time.time() - t0:.0f}s]", flush=True)

# ---- 2. noise in the transmitter: how much of it is tolerated? -----------
TXN_GRID = [0.02, 0.03, 0.04, 0.05, 0.06, 0.07] if not QUICK else [0.02, 0.04, 0.06]
max_txn = {}
for f_r in (12e9, 22e9):
    print(f"\n== noise in the transmitter (tx.noise_rms; RIN -165, TIA 1 pA/rtHz), "
          f"f_r {f_r / 1e9:.0f} GHz: largest Tx noise [V rms per UI]")
    for name, kw in CASES.items():
        t0 = time.time()

        def measure(s, kw=kw, f_r=f_r):
            return s, ber_of(make_cfg(f_r, tx_noise=s, rin=-165.0, tia=1.0, rx_noise=1e-5, **kw))

        v, note = crossing(TXN_GRID, measure, tol=0.002)
        max_txn[(f_r, name)] = v
        print(f"  {name:<26} tx.noise_rms <= {v:6.4f} V  {note}  [{time.time() - t0:.0f}s]", flush=True)

ctrl = "control (delta + Viterbi)"
print("\nsummary: f_r the modulator needs [GHz] (noise after it), and the Tx noise tolerated [mV rms]")
print("(noise in it); vs control in GHz and in dB (20 log10 of the noise ratio)")
print("                             no Tx FFE        host Tx FFE      Tx noise, f_r 12 GHz   f_r 22 GHz")
for name in CASES:
    a, b = min_fr[(False, name)], min_fr[(True, name)]
    c, d = max_txn[(12e9, name)], max_txn[(22e9, name)]
    print(f"  {name:<26} {a:6.2f} ({a - min_fr[(False, ctrl)]:+5.2f})  "
          f"{b:6.2f} ({b - min_fr[(True, ctrl)]:+5.2f})  "
          f"{1e3 * c:5.1f} ({20 * np.log10(c / max_txn[(12e9, ctrl)]):+5.1f} dB)  "
          f"{1e3 * d:5.1f} ({20 * np.log10(d / max_txn[(22e9, ctrl)]):+5.1f} dB)")

# direction, as measured (cairn/DSP发端与PR.md §16 has the numbers): with the
# noise after the modulator receive PR needs less bandwidth than the control
# and transmit PR more; with it in the transmitter the band limit costs
# nothing and no PR helps; transmit PR is never ahead
for ffe in (False, True):
    assert min_fr[(ffe, "RX PR a=1, precoded")] < min_fr[(ffe, ctrl)], min_fr
    assert min(min_fr[(ffe, n)] for n in CASES if n.startswith("TX")) > min_fr[(ffe, ctrl)], min_fr
for f_r in (12e9, 22e9):
    assert all(max_txn[(f_r, n)] <= max_txn[(f_r, ctrl)] * 1.05 for n in CASES), max_txn
assert abs(max_txn[(12e9, ctrl)] / max_txn[(22e9, ctrl)] - 1) < 0.15, max_txn

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
names = list(CASES)
y = np.arange(len(names))
colors = ["0.4", "C0", "C0", "C1", "C1"]
ax = axes[0]
ax.barh(y - 0.2, [min_fr[(False, n)] for n in names], 0.4, color=colors, label="no Tx FFE")
ax.barh(y + 0.2, [min_fr[(True, n)] for n in names], 0.4, color=colors, alpha=0.5,
        label="host Tx FFE")
ax.set_yticks(y, names, fontsize=8)
ax.invert_yaxis()
ax.set(xlabel="VCSEL f_r needed for KP4 [GHz] (lower is better)",
       title="noise after the modulator (RIN, PD/TIA)")
ax.legend(fontsize=8)
ax.grid(True, axis="x", alpha=0.3)
ax = axes[1]
ax.barh(y - 0.2, [1e3 * max_txn[(12e9, n)] for n in names], 0.4, color=colors, label="f_r 12 GHz")
ax.barh(y + 0.2, [1e3 * max_txn[(22e9, n)] for n in names], 0.4, color=colors, alpha=0.5,
        label="f_r 22 GHz")
ax.set_yticks(y, names, fontsize=8)
ax.invert_yaxis()
ax.set(xlabel="Tx noise tolerated at KP4 [mV rms per UI] (higher is better)",
       title="noise in the transmitter (tx.noise_rms)")
ax.legend(fontsize=8)
ax.grid(True, axis="x", alpha=0.3)
fig.tight_layout()
out = OUT / "40_pr_optical_duobinary.png"
fig.savefig(out, dpi=130)
print(f"wrote {out}")
