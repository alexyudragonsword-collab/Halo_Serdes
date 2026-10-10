"""DSP-based transmitter: what a finite DAC and a compressing driver cost.

The 224 Gb/s PAM4 transmitter of example 18 (112 GBd, 3-tap FFE) is built
through ``tx.pipeline.TxPipeline`` with a DAC of N bits (full scale = the
FFE's peak output, so nothing clips) and a driver whose compression c is
the same parameter as an E/O's ``li_compression`` (``core.static_curve``).

1. TX SNDR vs DAC bits x driver compression, measured on the Tx output at
   the symbol centres against the same Tx with an ideal DAC and a linear
   driver (``analysis.tx_metrics``), next to the sine closed form
   6.02 N + 1.76 dB. A PAM4 + FFE waveform does not fill the DAC the way a
   full-scale sine does, so its SNDR does not sit on the sine line: the
   steps between word lengths are uneven (a segment boundary can land on a
   level), and the overall slope (about 6 dB per bit) is the check.
2. Reach on example 18's channel sweep (FFE + memory-2 MLSD + KP4) for
   DAC 6 / 7 / 8 bits and ideal, and for the 7-bit DAC with a compressing
   driver: where the pre-FEC BER crosses KP4's threshold (post-KP4 1e-15),
   the sweep refined to 0.4 dB around it. The engine's Viterbi scores every
   symbol; until 2026-10-09 the example ran its own on the 20k symbols the
   engine keeps in ``y_slicer``.

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
    MlsdConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.fec import fec_threshold, refine  # noqa: E402
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
                    mlsd=MlsdConfig(kind="viterbi", memory=2),
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


P_STAR = fec_threshold("kp4")
print(f"\nreach: post-KP4 1e-15 <=> pre-FEC BER <= {P_STAR:.2e} (after MLSD)")
reach, sweeps = {}, {}
for name, (n, c) in TXS.items():
    t0 = time.time()

    def measure(L, n=n, c=c):
        cfg = make_cfg(L, n, c)
        return -ChannelModel.from_config(cfg).loss_at(56e9), run_time_link(cfg).ber.ber

    sweep = sweeps[name] = {L: measure(L) for L in LENGTHS}
    print(f"  {name:<14} " + " ".join(f"{x:4.1f}dB:{max(b, 0.5 / N_SYM):8.1e}" for x, b in sweep.values())
          + f"  [{time.time() - t0:.0f}s]")
    r = refine(sweep, measure, P_STAR, tol=0.4)
    extra = sorted(set(sweep) - set(LENGTHS))
    if extra:
        print(f"  {'':<14} " + " ".join(f"{sweep[L][0]:4.1f}dB:{max(sweep[L][1], 0.5 / N_SYM):8.1e}" for L in extra)
              + "  (refined)")
    reach[name] = r.value if r.value is not None else r.note

print("\nreach [dB @ 56 GHz]:")
for name in TXS:
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
    pts = [sweeps[name][L] for L in sorted(sweeps[name])]
    ax.semilogy([x for x, _ in pts], [max(b, 0.5 / N_SYM) for _, b in pts],
                "o-" if "c=" not in name else "s--", label=name)
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
