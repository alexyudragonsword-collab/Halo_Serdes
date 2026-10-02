"""Three optical topologies on one optical path: LPO, DSP retimed, CPO.

Host TX -> segment A -> E/O -> fibre -> O/E -> segment B -> host RX is one
chain. LPO (linear pluggable) leaves it as one link the host receiver must
equalise end to end; a DSP-retimed module puts a receiver and a transmitter
at the module's ingress and egress, cutting it into three links scored in
series (``engine/cascade.py``); CPO (co-packaged) is LPO with the segments
shrunk to what fits inside a package. Same VCSEL + OM4 for all three, same
host and retimer receivers.

Two outputs:

1. Reach over fibre length for the three topologies (time engine, KP4
   projected to 1e-15). The retimed optical segment's reach exceeds the LPO
   link's -- the direction is the claim, the number depends on the segments.
2. The lever table for docs/SUMMARY.md: electrical loss, optical noise (RIN)
   and retiming, each alone against the LPO baseline and all three together,
   in reach. Whether they add is the result, not the premise.
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
    OpticalConfig, RxConfig, SimConfig, TopologyConfig, TxConfig,
)
from halo_serdes.engine.cascade import run_cascade  # noqa: E402
from halo_serdes.fec import pre_to_post_fec_ber  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

BAUD = 53.125e9
NYQ = BAUD / 2
POST_FEC_TARGET = 1e-15
OMA_DBM = 1.0
FIBRE_M = [30, 60, 100, 140, 180, 220, 260, 300]
QUICK = "--quick" in sys.argv
N_SYM = 60_000 if QUICK else 200_000


def segment_for_loss(loss_db: float) -> ChannelConfig:
    def loss(length):
        c = ChannelConfig(kind="analytic", length_m=length, rdc=5.0, r_skin=2e-3,
                          loss_tangent=0.012, n_freq=4096)
        return -ChannelModel.from_channel_config(c, BAUD).loss_at(NYQ)
    lo, hi = 0.001, 1.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if loss(mid) < loss_db else (lo, mid)
    return ChannelConfig(kind="analytic", length_m=0.5 * (lo + hi), rdc=5.0, r_skin=2e-3,
                         loss_tangent=0.012, n_freq=4096)


def adc_rx(fullscale: float) -> RxConfig:
    return RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=3.0),
                    adc=AdcConfig(n_bits=10, n_lanes=16, enob=None, fullscale=fullscale),
                    ffe=FfeConfig(n_pre=4, n_post=12, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=1e-4)


def make_cfg(seg_db: float, fibre_m: float, retimer: str, rin_db_hz: float,
             n_sym: int = N_SYM) -> LinkConfig:
    seg = segment_for_loss(seg_db)
    # the host RX sees the whole chain (gain 0.25) unretimed, one trace (0.5)
    # retimed; the retimer's RX sees one trace or the optics -- full scales follow
    return LinkConfig(
        modulation="pam4", symbol_rate=BAUD, osr=16, tx=TxConfig(swing=1.0),
        rx=adc_rx(0.3 if retimer == "none" else 0.6),
        sim=SimConfig(n_symbols=n_sym, seed=7, pattern="prbs13q"),
        topology=TopologyConfig(
            seg_a=seg, seg_b=seg, retimer=retimer,
            retimer_rx=adc_rx(0.6), retimer_tx=TxConfig(swing=1.0),
            optical=OpticalConfig(kind="vcsel_mmf", f_r_hz=22e9, damping_hz=30e9,
                                  er_db=4.0, oma_dbm=OMA_DBM, rin_db_hz=rin_db_hz,
                                  length_m=fibre_m, modal_bw_mhz_km=4700.0,
                                  responsivity_a_w=0.7, tia_bw_hz=40e9,
                                  tia_noise_pa_sqrthz=12.0, tz_ohm=2000.0)))


def post_fec(cfg: LinkConfig) -> tuple[float, float, list[float]]:
    r = run_cascade(cfg)
    pre = max(r.ber.ber, 0.5 / max(r.ber.n_checked, 1))
    return pre, pre_to_post_fec_ber(pre, "kp4"), [s.result.ber.ber for s in r.segments]


def reach_m(fibre, post):
    fibre = np.asarray(fibre, float)
    lp = np.log10(np.maximum(post, 1e-300))
    t = np.log10(POST_FEC_TARGET)
    if lp[0] > t:
        return 0.0
    for i in range(1, lp.size):
        if lp[i] > t:
            return float(np.interp(t, [lp[i - 1], lp[i]], [fibre[i - 1], fibre[i]]))
    return float(fibre[-1])


def ladder(seg_db, retimer, rin, label):
    fibres = FIBRE_M[::3] if QUICK else FIBRE_M
    rows = []
    for L in fibres:
        t0 = time.time()
        pre, post, segs = post_fec(make_cfg(seg_db, L, retimer, rin))
        rows.append((L, pre, post, segs))
        seg_txt = " ".join(f"{p:.1e}" for p in segs) if len(segs) > 1 else ""
        print(f"  {label:<34} {L:4.0f} m  pre {pre:.2e}  post {post:.1e}  {seg_txt}  [{time.time() - t0:.0f}s]")
        if post > 1e-3:
            break
    return rows


# -------------------------------------------------------- three topologies ---
TOPOLOGIES = [
    ("LPO (8 dB segments, no retimer)", 8.0, "none", -145.0),
    ("DSP retimed (8 dB segments, both)", 8.0, "both", -145.0),
    ("CPO (4 dB segments, no retimer)", 4.0, "none", -145.0),
]
print(f"VCSEL + OM4, {BAUD / 1e9:.3f} GBd PAM4, OMA {OMA_DBM:+.0f} dBm, ER 4 dB, ADC RX + KP4")
results = {}
for label, seg_db, retimer, rin in TOPOLOGIES:
    results[label] = ladder(seg_db, retimer, rin, label)

print()
print(f"{'topology':<36} {'reach':>7}  (post-KP4 < 1e-15)")
reaches = {}
for label, rows in results.items():
    reaches[label] = reach_m([r[0] for r in rows], [r[2] for r in rows])
    print(f"{label:<36} {reaches[label]:6.0f}m")
lpo, ret, cpo = (reaches[t[0]] for t in TOPOLOGIES)
print(f"retimed optics reach > LPO reach: {ret > lpo}   (retimed {ret:.0f} m vs LPO {lpo:.0f} m; "
      f"CPO {cpo:.0f} m)")

# --------------------------------------------------------------- levers ---
# Baseline is the LPO link with RIN -143 dB/Hz, chosen so the baseline itself
# reaches somewhere (at -140 the 8 dB LPO link does not close at 30 m, and a
# gain over zero says nothing about additivity). Each lever alone: shorter
# segments (8 -> 4 dB, the CPO move), a quieter laser (RIN -143 -> -148),
# retiming; then all three.
LEVERS = [
    ("baseline: LPO 8 dB, RIN -143", 8.0, "none", -143.0),
    ("+ electrical: 4 dB segments", 4.0, "none", -143.0),
    ("+ optical noise: RIN -148", 8.0, "none", -148.0),
    ("+ retiming", 8.0, "both", -143.0),
    ("all three", 4.0, "both", -148.0),
]
print()
print("Lever table (reach at post-KP4 < 1e-15):")
lever_reach = {}
for label, seg_db, retimer, rin in LEVERS:
    rows = ladder(seg_db, retimer, rin, label)
    lever_reach[label] = reach_m([r[0] for r in rows], [r[2] for r in rows])
base = lever_reach[LEVERS[0][0]]
print()
print(f"{'lever':<34} {'reach':>7} {'gain':>8}")
for label, *_ in LEVERS:
    g = lever_reach[label] - base
    print(f"{label:<34} {lever_reach[label]:6.0f}m {g:+7.0f}m")
singles = sum(lever_reach[lv[0]] - base for lv in LEVERS[1:4])
combined = lever_reach[LEVERS[4][0]] - base
print(f"sum of single-lever gains {singles:+.0f} m vs all three together {combined:+.0f} m "
      f"-> {'roughly additive' if abs(combined - singles) < 0.25 * max(abs(singles), 1) else 'not additive'}")

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
ax = axes[0]
for label, rows in results.items():
    ax.semilogy([r[0] for r in rows], [max(r[2], 1e-30) for r in rows], "o-", label=label)
ax.axhline(POST_FEC_TARGET, color="green", ls=":", lw=1, label="target 1e-15")
ax.set(xlabel="OM4 fibre length [m]", ylabel="post-KP4 BER",
       title="Same optics, three ways to cut the chain\n(53 GBd PAM4, ADC RX)")
ax.set_ylim(1e-30, 1)
ax.grid(True, which="both", alpha=0.3)
ax.legend(fontsize=7)
ax = axes[1]
labels = [lv[0] for lv in LEVERS]
ax.barh(range(len(labels)), [lever_reach[lb] for lb in labels], color=["C7", "C0", "C1", "C2", "C3"])
ax.set_yticks(range(len(labels)))
ax.set_yticklabels(labels, fontsize=8)
ax.invert_yaxis()
ax.set(xlabel="reach [m]", title="Three levers on reach: alone and together")
ax.grid(True, axis="x", alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "33_three_topologies.png", dpi=130)
print(f"wrote {OUT / '33_three_topologies.png'}")
