"""LPO vs CPO: one optical path, different electrical segments around it.

Host TX -> electrical segment A -> E/O -> fibre -> O/E -> segment B -> host RX.
Linear pluggable (LPO), co-packaged (CPO) and a retimed module are this
chain cut at different places; at stage 1 of the optical model they differ
only in what segments A and B lose. The optical blocks are shared: an
850 nm VCSEL into OM4 (the 100GBASE-SR1 picture, IEEE 802.3db), a 40 GHz
linear TIA, shot + RIN + TIA noise that follows the optical power of each
PAM4 level.

Two questions, both on the ADC receiver with KP4 FEC:

1. Reach ladder: with segments A and B each at 4 / 8 / 12 / 16 dB at
   Nyquist (OIF CEI-112G-LINEAR allows 13 dB host loss, the LPO MSA 16 dB),
   how far does the fibre go before post-KP4 BER crosses 1e-15?
2. Optical margin at 100 m: how far can the launched OMA drop before the
   same crossing -- and how many dB more of that does CPO keep than LPO?
3. Equalisation at 100 m: OIF CEI-112G-LINEAR lets the LPO module's driver
   and TIA each carry a CTLE (``OpticalConfig.drv_ctle_db`` /
   ``tia_ctle_db``), and the host Tx has its own FFE. Which of them buys
   margin back, given that the photodiode noise sits between them?

The reach ladder is the time engine (pre-FEC BER measured, KP4 projected);
the OMA margin is the statistical engine, which stage 1 cross-checked
against the time engine within 2x on this receiver (tests/test_optical.py).
"""

import dataclasses
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
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.optical_stage import transmitter_power  # noqa: E402
from halo_serdes.engine.statistical import run_statistical  # noqa: E402
from halo_serdes.fec import pre_to_post_fec_ber  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

BAUD = 53.125e9                 # 100G/lambda PAM4
NYQ = BAUD / 2
POST_FEC_TARGET = 1e-15
OMA_NOMINAL_DBM = 1.0           # inside the 802.3db SR1 OMA_outer window (-3 .. +3.5 dBm)
RIN_DB_HZ = -145.0              # a typical device; the SR1 limit is RIN12OMA -131 dB/Hz
# This link is RIN-limited: RIN noise scales with the received power, so the
# OMA buys little once the TIA floor is cleared, and the electrical loss the
# receiver must undo (its FFE enhances that noise) decides the reach.
LOSSES_DB = [4.0, 8.0, 12.0, 16.0]
FIBRE_M = [30, 60, 100, 140, 180, 220, 260, 300]
QUICK = "--quick" in sys.argv


def segment_for_loss(loss_db: float) -> ChannelConfig:
    """Analytic trace whose insertion loss at Nyquist is ``loss_db`` (bisection on length)."""
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


def make_cfg(seg: ChannelConfig, fibre_m: float, oma_dbm: float,
             n_sym: int = 200_000) -> LinkConfig:
    return LinkConfig(
        modulation="pam4", symbol_rate=BAUD, osr=16,
        tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=3.0),
                    adc=AdcConfig(n_bits=10, n_lanes=16, enob=None, fullscale=0.3),
                    ffe=FfeConfig(n_pre=4, n_post=12, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=1e-4),
        sim=SimConfig(n_symbols=n_sym, seed=7, pattern="prbs13q"),
        topology=TopologyConfig(
            seg_a=seg,
            optical=OpticalConfig(kind="vcsel_mmf", f_r_hz=22e9, damping_hz=30e9,
                                  er_db=4.0, oma_dbm=oma_dbm, rin_db_hz=RIN_DB_HZ,
                                  length_m=fibre_m, modal_bw_mhz_km=4700.0,
                                  responsivity_a_w=0.7, tia_bw_hz=40e9,
                                  tia_noise_pa_sqrthz=12.0, tz_ohm=2000.0),
            seg_b=seg))


def time_point(cfg: LinkConfig) -> dict:
    cm = ChannelModel.from_config(cfg)
    res = run_time_link(cfg, channel=cm)
    pre = max(res.ber.ber, 0.5 / max(res.ber.n_checked, 1))  # MC floor: half an error
    return {"loss": -cm.loss_at(NYQ), "pre": pre, "post": pre_to_post_fec_ber(pre, "kp4"),
            "snr": res.slicer_snr_db, "n_err": res.ber.n_errors, "ffe": res.ffe_taps}


def stat_post_fec(cfg: LinkConfig, ffe_taps) -> float:
    cm = ChannelModel.from_config(cfg)
    st = run_statistical(cfg, channel=cm, ffe_taps=ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    return pre_to_post_fec_ber(max(st.ber, 1e-30), "kp4")


def reach_from_ladder(fibre_m, post):
    """Longest fibre with post-FEC below target, interpolated in log BER."""
    fibre_m = np.asarray(fibre_m, float)
    lp = np.log10(np.maximum(post, 1e-300))
    target = np.log10(POST_FEC_TARGET)
    if lp[0] > target:
        return 0.0
    for i in range(1, lp.size):
        if lp[i] > target:
            return float(np.interp(target, [lp[i - 1], lp[i]], [fibre_m[i - 1], fibre_m[i]]))
    return float(fibre_m[-1])


# ------------------------------------------------------------ reach ladder ---
losses = LOSSES_DB[::3] if QUICK else LOSSES_DB
fibres = FIBRE_M[::3] if QUICK else FIBRE_M
n_sym = 60_000 if QUICK else 200_000
ladder = {}
print(f"VCSEL + OM4, {BAUD / 1e9:.3f} GBd PAM4, OMA {OMA_NOMINAL_DBM:+.0f} dBm, ER 4 dB, "
      f"RIN {RIN_DB_HZ:.0f} dB/Hz, ADC RX + KP4; segments A = B")
print(f"{'seg loss':>8} {'fibre':>6} {'pre-FEC':>9} {'post-KP4':>9} {'SNR':>6}  errs")
for loss_db in losses:
    seg = segment_for_loss(loss_db)
    rows = []
    for L in fibres:
        t0 = time.time()
        r = time_point(make_cfg(seg, L, OMA_NOMINAL_DBM, n_sym))
        rows.append(r)
        print(f"{loss_db:7.0f}dB {L:5.0f}m {r['pre']:9.2e} {r['post']:9.1e} {r['snr']:5.1f}dB  "
              f"{r['n_err']:5d}  [{time.time() - t0:.0f}s]")
        if r["post"] > 1e-3:          # the ladder is over; the rest only costs time
            break
    ladder[loss_db] = rows

print()
print("Reach ladder (post-KP4 < 1e-15):")
print(f"{'seg A/B loss':>12} {'total elec.':>11} {'reach':>7} {'SNR @ reach':>11}")
summary = []
for loss_db, rows in ladder.items():
    L = [fibres[i] for i in range(len(rows))]
    post = [r["post"] for r in rows]
    reach = reach_from_ladder(L, post)
    snr = float(np.interp(reach, L, [r["snr"] for r in rows])) if reach > 0 else float("nan")
    summary.append((loss_db, 2 * loss_db, reach, snr))
    print(f"{loss_db:11.0f}dB {2 * loss_db:10.0f}dB {reach:6.0f}m {snr:10.1f}dB")

reaches = [s[2] for s in summary]
monotone = all(a >= b for a, b in zip(reaches, reaches[1:]))
print(f"reach decreases monotonically with electrical loss: {monotone}")

# ------------------------------------------------------- optical margin ---
# At 100 m: lowest OMA that still meets the target, per loss class, found by
# bisection on the statistical engine with the FFE the time engine converged
# to at nominal OMA. Margin = nominal OMA - minimum OMA.
print()
print("Optical margin at 100 m fibre (statistical engine, KP4 projection):")
margins = {}
OMA_MAX_DBM = 3.5                 # SR1 OMA_outer maximum
for loss_db in losses:
    seg = segment_for_loss(loss_db)
    base = time_point(make_cfg(seg, 100.0, OMA_NOMINAL_DBM, n_sym))
    if stat_post_fec(make_cfg(seg, 100.0, OMA_MAX_DBM), base["ffe"]) > POST_FEC_TARGET:
        margins[loss_db] = float("nan")
        print(f"  seg {loss_db:.0f} dB x2: target not met even at OMA {OMA_MAX_DBM:+.1f} dBm "
              f"(RIN-limited: more light brings more RIN noise; SNR {base['snr']:.1f} dB)")
        continue
    lo, hi = OMA_NOMINAL_DBM - 20.0, OMA_MAX_DBM
    for _ in range(16):
        mid = 0.5 * (lo + hi)
        ok = stat_post_fec(make_cfg(seg, 100.0, mid), base["ffe"]) < POST_FEC_TARGET
        lo, hi = (lo, mid) if ok else (mid, hi)
    oma_min = hi
    margins[loss_db] = OMA_NOMINAL_DBM - oma_min
    print(f"  seg {loss_db:.0f} dB x2: minimum OMA {oma_min:+.1f} dBm -> margin "
          f"{margins[loss_db]:.1f} dB (time-engine SNR at nominal {base['snr']:.1f} dB)")
cpo = margins[losses[0]]
closing = [d for d in losses[1:] if np.isfinite(margins[d])]
lpo_loss = max(closing) if closing else None
if lpo_loss is not None:
    print(f"CPO ({losses[0]:.0f} dB segments) keeps {cpo - margins[lpo_loss]:.1f} dB more optical "
          f"margin than LPO ({lpo_loss:.0f} dB segments) at 100 m")
for d in losses[1:]:
    if not np.isfinite(margins[d]):
        print(f"LPO at {d:.0f} dB segments has no optical margin at 100 m: no OMA in the SR1 "
              f"window closes it with this receiver (CTLE + FFE, no DFE/MLSD)")

# --------------------------------------------- module and host equalisation ---
# The photodiode noise is the dividing line. EQ ahead of it (the host Tx FFE,
# the module's driver CTLE) undoes segment A and the laser's roll-off before
# the noise is added; EQ behind it (the TIA CTLE) lifts noise and signal
# together, which the host receiver's FFE already does. Pre-emphasis also
# overshoots the steady levels, and a laser has finite room for that, so each
# margin is given three ways: linear (the overshoot free); through a laser
# with an L-I curve (li_compression 0.2) and its zero-power floor, which is
# what actually limits the overshoot in this model; and with no headroom at
# all -- the OMA cut until the TP2 waveform fits between the outer levels,
# 10 log10(peak-to-peak / OMA) when that exceeds 1, a bound the curve and
# floor turn out to be far from.
LASER_C = 0.2
FIR9 = (-0.06, 0.68, -0.26)       # 3-tap host Tx FFE, ~9 dB more at Nyquist than at DC
EQ_CASES = [(4.0, None, 0.0, 0.0), (4.0, FIR9, 0.0, 0.0),
            (12.0, None, 0.0, 0.0), (12.0, None, 0.0, 6.0), (12.0, None, 6.0, 0.0),
            (12.0, FIR9, 0.0, 0.0), (12.0, FIR9, 6.0, 0.0),
            (16.0, FIR9, 0.0, 0.0), (16.0, FIR9, 0.0, 6.0), (16.0, FIR9, 6.0, 0.0),
            (16.0, FIR9, 9.0, 0.0)]
if QUICK:
    EQ_CASES = [(4.0, None, 0.0, 0.0), (16.0, FIR9, 0.0, 0.0), (16.0, FIR9, 0.0, 6.0),
                (16.0, FIR9, 6.0, 0.0)]


def with_eq(cfg: LinkConfig, fir, drv_db: float, tia_db: float, c: float = 0.0) -> LinkConfig:
    o = dataclasses.replace(cfg.topology.optical, drv_ctle_db=drv_db, tia_ctle_db=tia_db,
                            li_compression=c)
    cfg = dataclasses.replace(cfg, topology=dataclasses.replace(cfg.topology, optical=o))
    if fir is not None:
        cfg = dataclasses.replace(cfg, tx=dataclasses.replace(cfg.tx, fir_taps=fir, fir_n_pre=1))
    return cfg


def oma_margin(cfg_at, ffe) -> float:
    """Nominal minus the lowest OMA meeting the target (bisection, statistical engine)."""
    if stat_post_fec(cfg_at(OMA_MAX_DBM), ffe) > POST_FEC_TARGET:
        return float("nan")
    lo, hi = OMA_NOMINAL_DBM - 20.0, OMA_MAX_DBM
    for _ in range(10 if QUICK else 16):
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if stat_post_fec(cfg_at(mid), ffe) < POST_FEC_TARGET else (mid, hi)
    return OMA_NOMINAL_DBM - hi


print()
print("Equalisation at 100 m fibre (OMA margin, statistical engine; host FFE "
      f"{FIR9} where set):")
print(f"{'segments':>8} {'host FFE':>8} {'driver':>6} {'TIA':>5} {'SNR':>6} {'margin':>7} "
      f"{'laser c=' + str(LASER_C):>11} {'p-p/OMA':>7} {'no headroom':>11}")
eq_rows = {}
for loss_db, fir, drv_db, tia_db in EQ_CASES:
    seg = segment_for_loss(loss_db)

    def cfg_at(oma, n=n_sym, c=0.0):
        return with_eq(make_cfg(seg, 100.0, oma, n), fir, drv_db, tia_db, c)

    def curved_at(oma, n=n_sym):
        return cfg_at(oma, n, LASER_C)

    base = time_point(cfg_at(OMA_NOMINAL_DBM))
    margin = oma_margin(cfg_at, base["ffe"])
    curved = oma_margin(curved_at, time_point(curved_at(OMA_NOMINAL_DBM))["ffe"])
    power, _ = transmitter_power(cfg_at(OMA_NOMINAL_DBM, 20_000), include_rin=False)
    power = power[2000:-2000]
    pp = float(power.max() - power.min()) / cfg_at(OMA_NOMINAL_DBM).topology.optical.oma_w
    strict = margin - 10.0 * np.log10(max(pp, 1.0))
    eq_rows[(loss_db, fir is not None, drv_db, tia_db)] = (margin, strict, curved)

    def fmt(x, w):
        return f"{x:{w - 2}.2f}dB" if np.isfinite(x) else f"{'fails':>{w}}"   # not even at SR1 max OMA

    print(f"{loss_db:7.0f}dB {'on' if fir else '-':>8} {drv_db:5.0f}dB {tia_db:4.0f}dB "
          f"{base['snr']:5.1f}dB {fmt(margin, 8)} {fmt(curved, 11)} {pp:7.2f} {fmt(strict, 12)}")

# direction, as measured (cairn/光互联建模.md §6): EQ ahead of the photodiode
# buys margin, the TIA's costs a little
base16 = eq_rows[(16.0, True, 0.0, 0.0)][0]
assert eq_rows[(16.0, True, 6.0, 0.0)][0] > base16 + 1.0, eq_rows
assert eq_rows[(16.0, True, 0.0, 6.0)][0] < base16 + 0.1, eq_rows
# and through a compressing laser the driver CTLE still pays
assert eq_rows[(16.0, True, 6.0, 0.0)][2] > eq_rows[(16.0, True, 0.0, 0.0)][2] + 1.0, eq_rows

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
ax = axes[0]
for loss_db, rows in ladder.items():
    L = [fibres[i] for i in range(len(rows))]
    ax.semilogy(L, [max(r["post"], 1e-30) for r in rows], "o-",
                label=f"seg A = B = {loss_db:.0f} dB")
ax.axhline(POST_FEC_TARGET, color="green", ls=":", lw=1, label="target 1e-15")
ax.set(xlabel="OM4 fibre length [m]", ylabel="post-KP4 BER",
       title="Reach vs electrical segment loss\n(VCSEL + OM4, 53 GBd PAM4, ADC RX)")
ax.set_ylim(1e-30, 1)
ax.grid(True, which="both", alpha=0.3)
ax.legend(fontsize=8)
ax = axes[1]
ax.plot([s[1] for s in summary], [s[2] for s in summary], "s-", color="C3")
ax.set(xlabel="total electrical loss A + B @ Nyquist [dB]", ylabel="reach [m]",
       title="The same optics, cut at different places:\nCPO (short segments) vs LPO (long host trace)")
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "32_lpo_vs_cpo.png", dpi=130)
print(f"wrote {OUT / '32_lpo_vs_cpo.png'}")
