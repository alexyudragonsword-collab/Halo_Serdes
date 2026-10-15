"""448 Gb/s per lane: PAM4, PAM6 or PAM8? Same net rate, same FEC, each with
its best receiver, on one channel family -- who reaches further and at what
cost in pJ/bit.

每 lane 448 Gb/s 的调制格式比较:同一净吞吐、同一套 FEC(KP4,以及
KP4 + BCH(255,215) 级联),PAM4 / PAM6 / PAM8 各配自己最优的收端,在示例
19–21 的解析信道族上比 reach(dB)与 pJ/bit。802.3dj 的 448G 规范值一律
未核实,本例只用库内模型。

The baud each format needs follows from the net rate and the FEC overhead
alone (``analysis/format_compare.baud_for``): under KP4 PAM4 runs at
237 GBd, PAM6 (2.5 bit/symbol, the corner32 pair map) at 190, PAM8 at 158;
the concatenated code adds 255/215. More levels buy baud with level spacing:
PAM6's gaps are 4.4 dB narrower than PAM4's at the same swing, PAM8's 7.4 dB.

Per format the receiver is scanned on the statistical engine (ADC receiver,
example 38's shape without its transmit jitter: LMS FFE, MM-CDR, ENOB 6.5; the engine takes the time
engine's MMSE starting FFE, ``engine.cascade.initial_ffe_taps``): FFE 11 or
21 taps x target delta / 1 + aD / 1 + aD + bD^2 (MMSE) x DFSE memory K.
Reach is where the pre-FEC BER crosses the FEC's threshold, in dB of loss
at 56 GHz (one length family; each format's own-Nyquist loss is quoted
beside it, extrapolated by the analytic model). The winner: longest reach,
within 0.1 dB the cheaper receiver. The chosen cell of each format is then
run on the time engine (invariant 3: stat / time per point) and the winner
against the runner-up on three seeds at the winner's reach.

Table 3 asks what the transmitter must do for each: the fewest DAC bits
whose reach stays within 0.1 dB of an ideal DAC's, and the transmitter
noise that costs 0.5 dB of reach -- both on the fixed delta receiver.

Pass ``--quick`` for a smoke run (fewer symbols, half the lengths, fewer
receiver cells, one seed).
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

from halo_serdes.analysis.format_compare import (  # noqa: E402
    baud_for, dac_bits_needed, half_gap_penalty_db, noise_tolerance, pick_receiver,
)
from halo_serdes.analysis.power import power_report, trellis_states  # noqa: E402
from halo_serdes.analysis.tx_metrics import tx_report  # noqa: E402
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.config import LinkConfig, PrConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig, MlsdConfig,
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.cascade import initial_ffe_taps  # noqa: E402
from halo_serdes.engine.statistical import run_statistical  # noqa: E402
from halo_serdes.fec import fec_threshold, refine  # noqa: E402

warnings.filterwarnings("ignore")   # the PAM6 / PAM8 envelope notes, the PR loop-load notes

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

QUICK = "--quick" in sys.argv
N_SYM = 100_000 if QUICK else 300_000
BIT_RATE = 448e9
FORMATS = ("pam4", "pam6", "pam8")
FECS = {"KP4": ("kp4", fec_threshold("kp4")),
        "KP4+BCH": ("kp4+bch", fec_threshold("kp4", inner=(255, 5)))}
LENGTHS = [0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.24, 0.28]
if QUICK:
    LENGTHS = LENGTHS[::2]
SEEDS = (3,) if QUICK else (3, 4, 5)

DELTA = PrConfig()
AD = PrConfig(target=(1.0, 0.5), adapt="mmse")
ADBD = PrConfig(target=(1.0, 0.5, 0.0), adapt="mmse")
# receiver cells: (FFE pre/post, target, DFSE state memory K; None = full trellis)
CELLS = {"21-tap, delta": ((6, 14), DELTA, None),
         "21-tap, 1 + aD, K = 1": ((6, 14), AD, 1),
         "21-tap, 1 + aD + bD^2, K = 2": ((6, 14), ADBD, 2),
         "11-tap, delta": ((3, 7), DELTA, None)}
if QUICK:
    CELLS = {k: CELLS[k] for k in ("21-tap, delta", "21-tap, 1 + aD, K = 1")}
FIXED = "21-tap, delta"           # table 3's receiver


def make_cfg(fmt: str, length_m: float, fec: str = "kp4", cell: str = FIXED,
             n_sym: int = N_SYM, seed: int = 3, dac_bits: int | None = None,
             tx_noise: float = 0.0) -> LinkConfig:
    ffe, pr, k = CELLS.get(cell, {**CELLS, "21-tap, delta": ((6, 14), DELTA, None)}[cell])
    return LinkConfig(
        modulation=fmt, symbol_rate=baud_for(BIT_RATE, fmt, fec), osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        # no transmit edge jitter: example 38 carries 0.004 UI, but a jitter
        # budget is per baud and the statistical engine's per-pattern bins
        # grow as N^3 (PAM8: 8x PAM4's cost per run)
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1,
                    dac_bits=dac_bits, noise_rms=tx_noise),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=ffe[0], n_post=ffe[1], adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    mlsd=MlsdConfig(kind="viterbi", memory=2, state_memory=k),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=seed, pattern="prbs13q" if fmt == "pam4" else "prbs13"),
        pr=pr,
    )


_channels: dict[float, ChannelModel] = {}


def channel(length_m: float) -> ChannelModel:
    if length_m not in _channels:
        _channels[length_m] = ChannelModel.from_config(make_cfg("pam4", length_m, n_sym=1000))
    return _channels[length_m]


def loss_db(length_m: float, f_hz: float = 56e9) -> float:
    return -channel(length_m).loss_at(f_hz)


def stat_ber(cfg: LinkConfig) -> float:
    ch = channel(cfg.channel.length_m)
    taps = initial_ffe_taps(cfg, ch)
    return float(run_statistical(cfg, channel=ch, ffe_taps=taps, ffe_pre=cfg.rx.ffe.n_pre).ber)


def time_ber(cfg: LinkConfig) -> tuple[float, int]:
    r = run_time_link(cfg, channel=channel(cfg.channel.length_m))
    return float(r.ber.ber), int(r.ber.n_errors)


def reach_of(measure, threshold: float, lengths=LENGTHS, tol: float = 0.4,
             near_db: float | None = None):
    """Walk the lengths up until the BER crosses ``threshold``, then bisect
    (``fec.refine``): the reach in dB of loss at 56 GHz, with the points.
    ``near_db``: where the crossing is expected (another cell's reach) --
    the walk starts a step below it and steps down first if that is
    already past the crossing."""
    points = {}
    k0 = 0
    if near_db is not None:
        k0 = max(0, int(np.searchsorted([loss_db(L) for L in lengths], near_db)) - 1)
    k = k0
    while k > 0:
        points[lengths[k]] = measure(lengths[k])
        if points[lengths[k]][1] <= threshold:
            break
        k -= 1
    for L in lengths[k if k < k0 else k0:]:
        if L not in points:
            points[L] = measure(L)
        if points[L][1] > threshold:
            break
    r = refine(points, measure, threshold, tol=tol)
    value = r.value if r.value is not None else (r.hi if r.note == "below sweep" else r.lo)
    return value, r.note, points


def fmt_name(fmt: str) -> str:
    return fmt.upper()


t_all = time.time()
print("448 Gb/s per lane, one length family (examples 19-21's analytic channel), loss at 56 GHz")
print("format   bit/sym   KP4 GBd   KP4+BCH GBd   Nyquist GHz (KP4)   half-gap vs PAM4")
for fmt in FORMATS:
    b4 = baud_for(BIT_RATE, fmt, "kp4")
    print(f"  {fmt_name(fmt):<6}   {make_cfg(fmt, 0.1).bits_per_symbol:5.1f}   {b4 / 1e9:7.2f}"
          f"   {baud_for(BIT_RATE, fmt, 'kp4+bch') / 1e9:9.2f}   {b4 / 2e9:12.1f}"
          f"        {half_gap_penalty_db(fmt):+5.1f} dB")
print("thresholds: " + ", ".join(f"{name} pre-FEC BER <= {thr:.2e}" for name, (_, thr) in FECS.items()))
print("loss ladder @ 56 GHz: " + " ".join(f"{L * 100:.0f}cm:{loss_db(L):.1f}" for L in LENGTHS) + " dB")

# ---- receiver scan on the statistical engine (KP4) ------------------------
print("\nreceiver scan (statistical engine, KP4): reach [dB @ 56 GHz], trellis states, receiver pJ/b")
scan: dict[str, dict[str, dict]] = {}
for fmt in FORMATS:
    scan[fmt] = {}
    for cell in CELLS:
        t0 = time.time()

        def measure(L, fmt=fmt, cell=cell):
            return loss_db(L), stat_ber(make_cfg(fmt, L, "kp4", cell))

        near = scan[fmt].get("21-tap, delta", {}).get("reach")
        value, note, points = reach_of(measure, FECS["KP4"][1], near_db=near)
        cfg = make_cfg(fmt, 0.1, "kp4", cell, n_sym=1000)
        rep = power_report(cfg)
        scan[fmt][cell] = {"reach": value, "note": note, "points": points,
                           "states": trellis_states(cfg), "pj": rep.pj("host.rx"),
                           "pj_mlsd": rep.pj("host.rx.mlsd")}
        print(f"  {fmt_name(fmt):<5} {cell:<30} {value:6.2f} {note:<14} {trellis_states(cfg):5d}"
              f"  {rep.pj('host.rx'):6.2f} pJ/b  [{time.time() - t0:.0f}s]", flush=True)

best = {fmt: pick_receiver(list(CELLS), reach=lambda c, f=fmt: scan[f][c]["reach"]
                           if not scan[f][c]["note"] else None,
                           pj=lambda c, f=fmt: scan[f][c]["pj"]) or FIXED
        for fmt in FORMATS}

# ---- the chosen cells on the time engine (KP4) and under the concatenated code
print("\nchosen receivers on the time engine (KP4): per point loss, stat BER, time BER, stat / time")
time_reach, stat_reach, ratios = {}, {}, {}
for fmt in FORMATS:
    cell = best[fmt]
    t0 = time.time()
    st_pts = scan[fmt][cell]["points"]
    lines = []

    def measure(L, fmt=fmt, cell=cell, st_pts=st_pts, lines=lines):
        ber, n_err = time_ber(make_cfg(fmt, L, "kp4", cell))
        if L not in st_pts:
            st_pts[L] = (loss_db(L), stat_ber(make_cfg(fmt, L, "kp4", cell)))
        s = st_pts[L][1]
        lines.append((loss_db(L), s, ber, n_err))
        return loss_db(L), ber

    value, note, _ = reach_of(measure, FECS["KP4"][1], near_db=scan[fmt][cell]["reach"])
    time_reach[fmt] = (value, note)
    stat_reach[fmt] = scan[fmt][cell]["reach"]
    ratios[fmt] = [s / b for _, s, b, n in lines if n >= 30 and b > 0]
    print(f"  {fmt_name(fmt)} / {cell}: time reach {value:.2f} dB {note}  "
          f"(stat {stat_reach[fmt]:.2f})  [{time.time() - t0:.0f}s]")
    for loss, s, b, n in sorted(lines):
        tag = "" if n >= 30 else f"  ({n} errors)"
        print(f"      {loss:5.1f} dB   stat {s:9.2e}   time {b:9.2e}   stat/time {s / b if b > 0 else float('nan'):5.2f}{tag}")

print("\nconcatenated code (KP4+BCH, higher baud): statistical reach of the chosen cell, one time-engine point")
bch_reach, bch_ok = {}, {}
for fmt in FORMATS:
    cell = best[fmt]
    t0 = time.time()

    def measure(L, fmt=fmt, cell=cell):
        return loss_db(L), stat_ber(make_cfg(fmt, L, "kp4+bch", cell))

    value, note, points = reach_of(measure, FECS["KP4+BCH"][1], near_db=scan[fmt][cell]["reach"])
    bch_reach[fmt] = (value, note)
    # the sweep point nearest the reach, on the time engine
    L_chk = min(points, key=lambda L: abs(loss_db(L) - value))
    tb, n_err = time_ber(make_cfg(fmt, L_chk, "kp4+bch", cell))
    ratio = points[L_chk][1] / tb if tb > 0 else float("inf")
    bch_ok[fmt] = 0.5 < ratio < 2.0
    print(f"  {fmt_name(fmt):<5} {baud_for(BIT_RATE, fmt, 'kp4+bch') / 1e9:6.1f} GBd  reach {value:6.2f} dB {note:<12}"
          f"  check @ {loss_db(L_chk):.1f} dB: stat {points[L_chk][1]:.2e}  time {tb:.2e} ({n_err} errors)"
          f"  stat/time {ratio:.2f}" + ("" if bch_ok[fmt] else "  -- NOT confirmed")
          + f"  [{time.time() - t0:.0f}s]", flush=True)
    if not bch_ok[fmt]:
        # the concatenated code's operating point is a pre-FEC BER of 7e-3: the
        # receiver's per-symbol decisions (what its LMS, CDR and PR adaptation
        # run on) are far worse there, and the time engine's loops can let go
        print("        the time engine's decision-directed loops do not hold at this operating point"
              " (the statistical engine's pr_loop_load warning); the statistical reach stands unconfirmed")

# ---- table 1 ------------------------------------------------------------
print("\ntable 1: 448 Gb/s per lane, best receiver per format")
print("format  receiver                        reach KP4 [dB]  KP4+BCH  own-Nyquist loss*  stat/time    pJ/b  states")
rows = {}
for fmt in FORMATS:
    cell = best[fmt]
    s = scan[fmt][cell]
    r_t = time_reach[fmt][0]
    # the chosen cell's loss at its own Nyquist, at the time-engine reach length
    L_r = float(np.interp(r_t, [loss_db(L) for L in LENGTHS], LENGTHS))
    own = loss_db(L_r, baud_for(BIT_RATE, fmt, "kp4") / 2.0)
    rat = ratios[fmt]
    rows[fmt] = (r_t, s["pj"])
    print(f"  {fmt_name(fmt):<5} {cell:<30} {r_t:9.2f}      {bch_reach[fmt][0]:6.2f}{' ' if bch_ok[fmt] else '?'}  {own:9.1f} dB"
          f"      {min(rat):4.2f}-{max(rat):4.2f}  {s['pj']:6.2f}  {s['states']:5d}")
print("  * at the format's own Nyquist (118 / 95 / 79 GHz), extrapolated by the analytic model -- 未核实")
if not all(bch_ok.values()):
    print("  ? statistical reach the time engine did not confirm (its loops let go at that operating point)")

order = sorted(FORMATS, key=lambda f: rows[f][0], reverse=True)
win, second = order[0], order[1]
gap = rows[win][0] - rows[second][0]
print(f"\nwinner (KP4, time engine): {fmt_name(win)} with {best[win]} -- "
      f"{gap:+.2f} dB over {fmt_name(second)}, {rows[win][1] - rows[second][1]:+.2f} pJ/b"
      + ("  (inside the 0.4 dB refinement step: a tie on reach, read the pJ/b)" if gap < 0.4 else ""))

# the same question on the other seeds, at the winner's reach length
L_win = float(np.interp(rows[win][0], [loss_db(L) for L in LENGTHS], LENGTHS))
L_win = min(LENGTHS + [L_win], key=lambda L: abs(L - L_win))
print(f"seeds at the winner's reach ({loss_db(L_win):.1f} dB): BER {fmt_name(win)} vs {fmt_name(second)}")
same = 0
for seed in SEEDS:
    b_w, n_w = time_ber(make_cfg(win, L_win, "kp4", best[win], seed=seed))
    b_s, n_s = time_ber(make_cfg(second, L_win, "kp4", best[second], seed=seed))
    same += b_w <= b_s
    print(f"  seed {seed}: {b_w:.2e} ({n_w})  vs  {b_s:.2e} ({n_s})  -> {'same' if b_w <= b_s else 'reversed'}")
print(f"direction consistent on {same} of {len(SEEDS)} seeds")

# PAM4's receiver-side PR + DFSE against its delta receiver: does it change who wins?
delta_win = max(FORMATS, key=lambda f: scan[f]["21-tap, delta"]["reach"])
pr_cells = [c for c in CELLS if "1 + aD" in c and not scan["pam4"][c]["note"]]
if pr_cells:
    c_pr = max(pr_cells, key=lambda c: scan["pam4"][c]["reach"])
    gain = scan["pam4"][c_pr]["reach"] - scan["pam4"]["21-tap, delta"]["reach"]
    print(f"PAM4 with receiver PR + DFSE ({c_pr}): {gain:+.2f} dB over its delta receiver (statistical); "
          f"on delta receivers alone {fmt_name(delta_win)} leads "
          f"({scan[delta_win]['21-tap, delta']['reach']:.2f} dB) -- PR + DFSE "
          f"{'turns the table' if delta_win != win else 'does not change the winner'}")
print("transmitter-side PR (example 37) is not in the scan: it trades Tx swing for the same target")

# ---- table 2: the three bathtubs on one loss grid (statistical, chosen cells)
print("\ntable 2: BER against loss @ 56 GHz, chosen receivers (statistical engine, KP4 baud)")
curves = {}
for fmt in FORMATS:
    pts = scan[fmt][best[fmt]]["points"]
    for L in LENGTHS:
        if L not in pts:
            pts[L] = (loss_db(L), stat_ber(make_cfg(fmt, L, "kp4", best[fmt])))
    curves[fmt] = sorted(pts.values())
print("  loss [dB]  " + "  ".join(f"{fmt_name(f):>9}" for f in FORMATS))
for L in LENGTHS:
    print(f"  {loss_db(L):7.1f}    " + "  ".join(f"{dict(curves[f])[loss_db(L)]:9.2e}" for f in FORMATS))


def crossing(a, b, floor: float = 1e-9):
    """Loss where two log-BER curves cross, None without one; points where
    either curve is below ``floor`` (the engine's resolution) do not count."""
    da, db = dict(a), dict(b)
    x = np.array([v for v in sorted(set(da) & set(db)) if da[v] > floor and db[v] > floor])
    if x.size < 2:
        return None
    d = np.log10([da[v] for v in x]) - np.log10([db[v] for v in x])
    s = np.flatnonzero(np.diff(np.sign(d)) != 0)
    if s.size == 0:
        return None
    k = int(s[0])
    return float(x[k] - d[k] * (x[k + 1] - x[k]) / (d[k + 1] - d[k]))


for a, b in (("pam4", "pam6"), ("pam4", "pam8"), ("pam6", "pam8")):
    x = crossing(curves[a], curves[b])
    print(f"  {fmt_name(a)} / {fmt_name(b)}: " + ("no crossing on the grid" if x is None else f"curves cross at {x:.1f} dB"))

# ---- table 3: what the transmitter needs (fixed delta receiver, statistical)
print("\ntable 3: DAC bits and transmitter noise (21-tap delta receiver, statistical reach, KP4)")
BITS = (None, 7, 6, 5) if not QUICK else (None, 6)
NOISES = (0.0, 0.01, 0.02) if not QUICK else (0.0, 0.02)
print("format   " + "  ".join(f"{'ideal' if b is None else str(b) + ' bit':>8}" for b in BITS)
      + "   bits needed (0.1 dB)   SNDR @ needed   " + "  ".join(f"{1e3 * n:4.0f} mV" for n in NOISES)
      + "   noise for -0.5 dB")
table3 = {}
for fmt in FORMATS:
    t0 = time.time()
    by_bits = {}
    for bits in BITS:
        def measure(L, fmt=fmt, bits=bits):
            return loss_db(L), stat_ber(make_cfg(fmt, L, "kp4", FIXED, dac_bits=bits))
        value, note, _ = reach_of(measure, FECS["KP4"][1], near_db=scan[fmt][FIXED]["reach"])
        by_bits[bits] = None if note else value
    need = dac_bits_needed(by_bits, tol_db=0.1)
    sndr = tx_report(make_cfg(fmt, 0.1, "kp4", FIXED, n_sym=4000, dac_bits=need)).sndr_db
    by_noise = {}
    for nz in NOISES:
        def measure(L, fmt=fmt, nz=nz):
            return loss_db(L), stat_ber(make_cfg(fmt, L, "kp4", FIXED, tx_noise=nz))
        value, note, _ = reach_of(measure, FECS["KP4"][1], near_db=scan[fmt][FIXED]["reach"])
        by_noise[nz] = None if note else value
    tol = noise_tolerance(by_noise, drop_db=0.5)
    table3[fmt] = (need, tol)
    print(f"  {fmt_name(fmt):<5}  " + "  ".join(f"{by_bits[b]:8.2f}" if by_bits[b] is not None else f"{'--':>8}" for b in BITS)
          + f"   {need if need is not None else '--':>10}            {sndr:5.1f} dB   "
          + "  ".join(f"{by_noise[n]:7.2f}" if by_noise[n] is not None else f"{'--':>7}" for n in NOISES)
          + f"   {'--' if tol is None else f'{1e3 * tol:.0f} mV'}  [{time.time() - t0:.0f}s]", flush=True)

# ---- appendix and figure --------------------------------------------------
print("\nappendix: every receiver cell (statistical engine, KP4)")
print("format  receiver                        reach [dB]  states   Viterbi pJ/b   receiver pJ/b")
for fmt in FORMATS:
    for cell, s in scan[fmt].items():
        print(f"  {fmt_name(fmt):<5} {cell:<30} {s['reach']:7.2f} {s['note']:<12} {s['states']:5d}"
              f"   {s['pj_mlsd']:8.2f}        {s['pj']:8.2f}")

fig, ax = plt.subplots(figsize=(7, 4.5))
for fmt, mk in zip(FORMATS, ("o", "s", "^")):
    x, y = zip(*curves[fmt])
    ax.semilogy(x, np.maximum(y, 1e-12), marker=mk, label=f"{fmt_name(fmt)} / {best[fmt]} (stat)")
for name, (_, thr) in FECS.items():
    ax.axhline(thr, ls="--", lw=0.8, color="gray")
    ax.text(LENGTHS and loss_db(LENGTHS[0]), thr * 1.3, name, fontsize=8, color="gray")
ax.set_xlabel("loss @ 56 GHz [dB]")
ax.set_ylabel("pre-FEC BER")
ax.set_ylim(1e-9, 1)
ax.set_title("448 Gb/s per lane: PAM4 / PAM6 / PAM8, best receiver each")
ax.grid(True, which="both", alpha=0.3)
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "46_448g_formats.png", dpi=120)
print(f"\nsaved {OUT / '46_448g_formats.png'}  [{time.time() - t_all:.0f}s total]")
