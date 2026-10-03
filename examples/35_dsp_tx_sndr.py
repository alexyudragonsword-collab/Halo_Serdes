"""DSP-based transmitter: what a finite DAC and a compressing driver cost.

The 224 Gb/s PAM4 transmitter of example 18 (112 GBd, 3-tap FFE) is built
through ``tx.pipeline.TxPipeline`` with a DAC of N bits (full scale = the
FFE's peak output, so nothing clips) and a driver whose compression c is
the same parameter as an E/O's ``li_compression`` (``core.static_curve``).

1. TX SNDR vs DAC bits x driver compression, measured on the Tx output at
   the symbol centres against the same Tx with an ideal DAC and a linear
   driver (``analysis.tx_metrics``), next to the sine closed form
   6.02 N + 1.76 dB. A PAM4 + FFE waveform does not fill the DAC the way a
   full-scale sine does, so its SNDR sits below the sine line by a fixed
   amount; the slope (6 dB per bit) is the check.
2. Reach on example 18's channel sweep (FFE + memory-2 MLSD + KP4) for
   DAC 6 / 7 / 8 bits and ideal, and for the 7-bit DAC with a compressing
   driver: the longest channel whose post-KP4 BER stays at 1e-15.

Approximate, unsourced: 802.3dj specifies a TX SNDR and an R_LM for its
200G/lane electrical transmitters (order of 30+ dB and 0.95); the clause was
not reachable here, so those values are not drawn as limits.
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
warnings.filterwarnings("ignore", message=".*marginal zone.*")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.analysis.tx_metrics import tx_report  # noqa: E402
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig,
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.prbs import symbol_checker  # noqa: E402
from halo_serdes.dsp import viterbi_mlsd  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.static_link import make_pattern  # noqa: E402
from halo_serdes.fec import pre_to_post_fec_ber  # noqa: E402
from halo_serdes.tx.dac import TxDac  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)
QUICK = "--quick" in sys.argv
N_SYM = 100_000 if QUICK else 400_000


def make_cfg(length_m: float, dac_bits=None, c: float = 0.0, n_sym: int = N_SYM) -> LinkConfig:
    """Example 18's link, with the Tx DAC and driver set."""
    drv = dict(drv_nl="curve", drv_compression=c) if c > 0 else {}
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1,
                    clock=ClockConfig(rj_ui=0.004), dac_bits=dac_bits, **drv),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=14, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"))


# ------------------------------------------------------- 1. TX SNDR table ---
BITS = [5, 6, 7, 8]
COMP = [0.0, 0.1, 0.2, 0.3]
sndr = np.zeros((len(BITS) + 1, len(COMP)))
rlm = np.zeros(len(COMP))
print("TX SNDR [dB] at the Tx output (PAM4 112 GBd, FFE (-0.06, 1, -0.12)), vs ideal Tx")
print(f"{'DAC':>7} " + " ".join(f"{'c=' + format(c, 'g'):>8}" for c in COMP) + "   sine 6.02N+1.76 / measured sine")
for i, n in enumerate(BITS + [None]):
    for j, c in enumerate(COMP):
        r = tx_report(make_cfg(0.1, n, c, n_sym=32_768))
        sndr[i, j] = r.sndr_db
        if n is None:
            rlm[j] = r.rlm
    name = f"{n} bit" if n else "ideal"
    sine = f"   {6.02 * n + 1.76:5.2f} / {TxDac(n, 1.0).sqnr_of_sine():5.2f}" if n else ""
    print(f"{name:>7} " + " ".join(f"{v:8.2f}" for v in sndr[i]) + sine)
print("measured R_LM vs c (ideal DAC): " + "  ".join(f"{c:g}:{v:.3f}" for c, v in zip(COMP, rlm)))

# ------------------------------------------------------------ 2. reach ------
LENGTHS = [0.15, 0.17, 0.18, 0.19, 0.20, 0.22]
if QUICK:
    LENGTHS = LENGTHS[::2]
TXS = {"ideal DAC": (None, 0.0), "8-bit DAC": (8, 0.0), "7-bit DAC": (7, 0.0),
       "6-bit DAC": (6, 0.0), "7-bit + c=0.1": (7, 0.1), "7-bit + c=0.2": (7, 0.2),
       "ideal + c=0.2": (None, 0.2)}


def mlsd_ber(cfg: LinkConfig) -> tuple[float, float]:
    """Example 18's receiver read-out: FFE slicer stream, LMMSE residual fit,
    memory-2 Viterbi; returns (pre-FEC BER after MLSD, slicer SNR)."""
    res = run_time_link(cfg)
    levels, warm, y = res.extras["levels"], res.extras["warmup"], res.y_slicer
    ref = make_pattern(cfg)[warm: warm + y.size]
    ld = levels[ref]
    cols = [ld] + [np.concatenate([np.zeros(i), ld[:-i]]) for i in (1, 2, 3)]
    coef, *_ = np.linalg.lstsq(np.vstack(cols).T, y, rcond=None)
    dec = viterbi_mlsd(y.astype(np.float64), levels.astype(np.float64), coef[:3])
    return symbol_checker(ref, dec).ber, res.slicer_snr_db


def kp4_threshold(target: float = 1e-15) -> float:
    lo, hi = -8.0, -2.0                       # log10 pre-FEC BER
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if pre_to_post_fec_ber(10 ** mid, "kp4") > target:
            hi = mid
        else:
            lo = mid
    return 10 ** lo


P_STAR = kp4_threshold()
loss = np.array([ChannelModel.from_config(make_cfg(L)).loss_at(56e9) for L in LENGTHS])
ber = {}
print(f"\nreach: post-KP4 1e-15 <=> pre-FEC BER <= {P_STAR:.2e} (after MLSD)")
for name, (n, c) in TXS.items():
    t0 = time.time()
    rows = [mlsd_ber(make_cfg(L, n, c)) for L in LENGTHS]
    ber[name] = np.array([max(b, 0.5 / N_SYM) for b, _ in rows])
    print(f"  {name:<14} " + " ".join(f"{-ls:4.1f}dB:{b:8.1e}" for ls, b in zip(loss, ber[name]))
          + f"  [{time.time() - t0:.0f}s]")


def reach_db(b: np.ndarray) -> float | str:
    lb, lt = np.log10(b), np.log10(P_STAR)
    il = -loss
    for k in range(len(il) - 1):
        if lb[k] <= lt < lb[k + 1]:
            return float(il[k] + (lt - lb[k]) / (lb[k + 1] - lb[k]) * (il[k + 1] - il[k]))
    return "beyond sweep" if lb[-1] <= lt else "below sweep"


print("\nreach [dB @ 56 GHz]:")
reach = {}
for name in TXS:
    reach[name] = reach_db(ber[name])
    v = reach[name]
    d = (f"{v - reach['ideal DAC']:+.2f} dB" if isinstance(v, float) and isinstance(reach["ideal DAC"], float)
         else "")
    print(f"  {name:<14} {v if isinstance(v, str) else f'{v:.2f}':>12}  {d}")

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
ax = axes[0]
for j, c in enumerate(COMP):
    ax.plot(BITS, sndr[:-1, j], "o-", label=f"PAM4 + FFE, driver c = {c:g}")
ax.plot(BITS, [6.02 * n + 1.76 for n in BITS], "k--", lw=1, label="full-scale sine 6.02N + 1.76")
ax.set(xlabel="DAC bits", ylabel="TX SNDR [dB]", title="TX SNDR vs DAC bits and driver compression")
ax.set_xticks(BITS)
ax.grid(True, alpha=0.3)
ax.legend(fontsize=7)
ax = axes[1]
for name in TXS:
    ax.semilogy(-loss, ber[name], "o-" if "c=" not in name else "s--", label=name)
ax.axhline(P_STAR, color="r", ls=":", lw=1, label=f"KP4 1e-15 ({P_STAR:.1e})")
ax.set(xlabel="channel loss @ 56 GHz [dB]", ylabel="pre-FEC BER after MLSD",
       title="Reach on example 18's sweep (224 Gb/s PAM4)")
from matplotlib.ticker import FuncFormatter  # noqa: E402

ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0e}"))
ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
ax.grid(True, which="both", alpha=0.3)
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "35_dsp_tx_sndr.png", dpi=130)
print(f"wrote {OUT / '35_dsp_tx_sndr.png'}")
