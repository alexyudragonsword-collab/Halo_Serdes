"""Lanes on one sampling clock: the static skew budget.

When one CDR's clock samples N lanes, a lane whose data arrives ``s`` UI late
is sampled ``s`` UI early -- ``-s`` UI from where its own loop would have
locked. Its DFE taps, slicer levels and (ADC) FFE still adapt where it
samples, so its BER is the lane's curve against sampling offset with the
equalisers re-solved at each offset (``analysis.skew.lane_curve``, one
statistical run per offset). The budget is the width of the offsets that
keep it under a target; ``shared_cdr`` places the clock:

- master: one lane's loop drives all of them, at its own lock point;
- average: one loop listening to every lane settles at their mean skew;
- minmax: the phase that minimises the worst lane's BER (a calibration
  that eye-scans every lane).

Three links (the presets' receivers, receiver noise set so the curves stay
above 1e-300). The ADC lane's FFE is the MMSE solution the time engine
starts from (and its LMS barely leaves), re-solved at each offset or held at
the lock point's: re-solved it reads 0.9-1.5x of a time-domain lane held at
the offset (cairn/多lane建模.md §8, where the time-domain cross-check is).
Until 2026-10-14 the stand-in was a copy of that solve that left the Tx FFE
out of the pulse and read 14-25x high, so this curve never reached the KP4
threshold (ROADMAP P2 #23); before 2026-10-13 (P1 #21) the time-domain
lane's DFE never engaged.

The curves are read on a clock the lane's own data does not vote; the
master lane reads its own loop (``LaneCurve.own_ber``): on NRZ with a DFE
the data that votes a bang-bang loop late is the data it then errs on
(the pattern-phase factor), which a lane on that clock does not share. The
last part runs lanes together on one clock in the time domain
(``engine.shared_clock``, ROADMAP P3 #9c / #9d, the dynamic part) and reads
that difference, and on the ADC receiver an averaging loop that stays where
it starts: its detector, on the equalised samples, does not see the lanes'
offsets.

Pass ``--quick`` for a smoke run (fewer phases, shorter runs).
"""

import dataclasses
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

from halo_serdes.analysis import lane_curve, shared_cdr  # noqa: E402
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.engine.cascade import initial_ffe_taps  # noqa: E402
from halo_serdes.engine.shared_clock import run_shared_clock  # noqa: E402
from halo_serdes.engine.statistical import run_statistical  # noqa: E402
from halo_serdes.fec import fec_threshold  # noqa: E402
from halo_serdes_app import config_bridge as cb  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

QUICK = "--quick" in sys.argv
N_PHASES = 8 if QUICK else 32
KP4 = fec_threshold("kp4")

# preset, receiver noise [V rms], BER target (and its name)
LINKS = {"PAM4 32G mixed-signal": (0.007, KP4, "KP4 threshold"),
         "NRZ 28G analytic (COM/xtalk)": (0.012, 1e-12, "1e-12"),
         "PAM4 224G ADC (106 GBd)": (0.0015, KP4, "KP4 threshold")}
SKEWS = [0.0, 0.06, -0.09, 0.13]          # four lanes' arrival skew [UI]
SPREADS = np.linspace(0.0, 0.5, 26)       # the same four, spread over [0, s]
TABLE = [-0.375, -0.25, -0.125, 0.0, 0.125, 0.25, 0.375]


def link(name):
    noise = LINKS[name][0]
    cfg = cb.load_preset(name)
    return dataclasses.replace(cfg, rx=dataclasses.replace(cfg.rx, noise_rms=noise))


def curves(cfg):
    """The lane's curve; an ADC lane's both ways (FFE re-solved, held)."""
    ch = ChannelModel.from_config(cfg)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")          # the presets' envelope notes
        if cfg.rx.arch != "adc_dsp":
            return {"": lane_curve(cfg, ch, n_phases=N_PHASES)}
        return {"FFE re-solved": lane_curve(cfg, ch, n_phases=N_PHASES,
                                            ffe_taps=lambda at: initial_ffe_taps(cfg, ch, at_ui=at)),
                "FFE held at lock": lane_curve(cfg, ch, n_phases=N_PHASES,
                                                           ffe_taps=initial_ffe_taps(cfg, ch))}


def fmt_window(w):
    if w is None:
        return "none (never that low)"
    if not np.isfinite(w[0]):
        return "every offset"
    return f"{w[0]:+.3f} ... {w[1]:+.3f} UI"


results = {}
print("A lane sampled off its own lock point, its equalisers adapted there; "
      f"{N_PHASES} phases per curve")
for name, (noise, target, tname) in LINKS.items():
    t0 = time.time()
    cs = curves(link(name))
    print(f"\n{name}  (noise {noise * 1e3:.1f} mV, target {target:.2e} = {tname})")
    print("  offset from lock [UI] " + " ".join(f"{d:+8.3f}" for d in TABLE))
    for lab, c in cs.items():
        print(f"  {lab or 'BER':21s} " + " ".join(f"{float(c.at(d)):8.1e}" for d in TABLE))
    main = next(iter(cs.values()))
    b = main.best_offset_ui
    own = main.own_ber if main.own_ber is not None else float(main.at(0.0))
    print(f"  own loop: {own:.2e} at its lock point (on another lane's clock "
          f"{float(main.at(0.0)):.2e}); lowest {float(main.at(b)):.2e} at {b:+.3f} UI")
    for lab, c in cs.items():
        w = c.window(target)
        t10 = 10 * float(c.at(0.0))
        print(f"  window{(' (' + lab + ')') if lab else ''}: at the target {fmt_window(w)} "
              f"-> {c.budget_ui(target):.3f} UI; at 10x the lock BER {fmt_window(c.window(t10))} "
              f"-> {c.budget_ui(t10):.3f} UI")
    if len(cs) == 2:
        a, b = cs.values()
        print("  held / re-solved: " + ", ".join(f"{float(b.at(d)) / float(a.at(d)):.1f}x at {d:+.2f} UI"
                                                  for d in (-0.25, 0.25)))
    # the average's clock is one loop over the four lanes' detectors: quieter
    # hunting on a bang-bang loop (the ADC's Mueller-Muller loop is modelled
    # without its own noise on white clocks, so its curve is the same)
    if link(name).rx.arch == "mixed_signal":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            avg = lane_curve(link(name), n_phases=N_PHASES, voters=len(SKEWS))
    else:
        avg = dataclasses.replace(main, voters=len(SKEWS))
    by_pol = {"master": main, "average": avg, "minmax": main}
    print(f"  on one loop over the four lanes' detectors: {float(avg.at(0.0)):.2e} at the lock point")
    print("  four lanes, skews " + " ".join(f"{s:+.2f}" for s in SKEWS) + " UI:")
    for pol in ("master", "average", "minmax"):
        r = shared_cdr(by_pol[pol], SKEWS, pol)
        print(f"    {pol:8s} clock at {r.phase_ui:+.3f} UI   lanes "
              + " ".join(f"{x:8.1e}" for x in r.ber)
              + f"   worst {r.worst:.1e} (lane {int(np.argmax(r.ber))}), mean {r.mean:.1e}")
    mm = shared_cdr(main, SKEWS, "minmax")
    print(f"  minmax worst lane / one lane on its own loop: {mm.worst / own:.2f}")
    sweep = {pol: [shared_cdr(by_pol[pol], s * np.array([0, 1, 2, 3]) / 3, pol).worst
                   for s in SPREADS]
             for pol in ("master", "average", "minmax")}
    results[name] = (cs, target, tname, sweep)
    print(f"  [{time.time() - t0:.0f}s]")

if not QUICK:
    # the master's lane reads its own loop's BER; the minimax never does worse
    # than the master on its worst lane
    for name, (cs, target, tname, sweep) in results.items():
        main = next(iter(cs.values()))
        m = shared_cdr(main, SKEWS, "master")
        assert m.ber[0] == (main.own_ber if main.own_ber is not None else float(main.at(0.0)))
        assert shared_cdr(main, SKEWS, "minmax").worst <= m.worst * (1 + 1e-9)
    adc = results["PAM4 224G ADC (106 GBd)"][0]
    moved, held = adc["FFE re-solved"], adc["FFE held at lock"]
    assert float(held.at(0.25)) > 3 * float(moved.at(0.25))

# ---------------------------------------- lanes run together on one clock ---
# NRZ 16G at 15 mV: four lanes at skew 0, lane 0's loop driving. The master
# lane errs on the data that votes its loop late; the others sample on the
# same phase without that correlation.
cfg16 = dataclasses.replace(cb.load_preset("NRZ 16G mixed-signal"), sim=dataclasses.replace(
    cb.load_preset("NRZ 16G mixed-signal").sim, n_symbols=100_000 if QUICK else 1_500_000))
cfg16 = dataclasses.replace(cfg16, rx=dataclasses.replace(cfg16.rx, noise_rms=0.015))
t0 = time.time()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    run = run_shared_clock(cfg16, [0.0] * 4, "master")
    s_own = run_statistical(cfg16).ber
    s_other = run_statistical(cfg16, phase_ui=0.0, own_clock=False).ber
e, n = run.n_errors, np.array([ln.ber.n_checked for ln in run.lanes])
print("\nNRZ 16G mixed-signal (noise 15.0 mV), four lanes at skew 0 on lane 0's loop, time domain:")
print("  lanes " + " ".join(f"{x:8.1e}" for x in run.ber) + "   errors " + " ".join(map(str, e)))
t_m, t_o = e[0] / n[0], e[1:].sum() / n[1:].sum()
print(f"  master lane {t_m:.2e} (statistical, own loop {s_own:.2e}: {s_own / max(t_m, 1e-300):.2f}x); "
      f"the others {t_o:.2e} (statistical, on its clock {s_other:.2e}: {s_other / max(t_o, 1e-300):.2f}x)")
print(f"  master / others: time {t_m / max(t_o, 1e-300):.2f}, statistical {s_own / s_other:.2f}  "
      f"[{time.time() - t0:.0f}s]")

# PAM4 224G ADC at 2.5 mV, the four lanes at SKEWS on one loop over all of
# them. Its Mueller-Muller detector reads the equalised samples and every
# lane's FFE re-adapts where it samples, so the averaged detector does not see
# the offsets: the clock stays where it starts (lane 0's lock), not at the
# mean skew (ROADMAP P3 #9d).
adc_name = "PAM4 224G ADC (106 GBd)"
cfg_a = cb.load_preset(adc_name)
cfg_a = dataclasses.replace(cfg_a, rx=dataclasses.replace(cfg_a.rx, noise_rms=0.0025),
                            sim=dataclasses.replace(cfg_a.sim, n_symbols=30_000 if QUICK else 200_000))
ch_a = ChannelModel.from_config(cfg_a)
t0 = time.time()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    run_a = run_shared_clock(cfg_a, SKEWS, "average", channel=ch_a)
    mean_skew = float(np.angle(np.mean(np.exp(2j * np.pi * np.array(SKEWS)))) / (2 * np.pi))

    def st_a(d):
        return run_statistical(cfg_a, channel=ch_a, phase_ui=d, ffe_pre=cfg_a.rx.ffe.n_pre,
                               ffe_taps=initial_ffe_taps(cfg_a, ch_a, at_ui=d)).ber

    at_start = [st_a(-s) for s in SKEWS]
    at_mean = [st_a(mean_skew - s) for s in SKEWS]
print(f"\n{adc_name} (noise 2.5 mV), four lanes, skews " + " ".join(f"{s:+.2f}" for s in SKEWS)
      + " UI, one loop over all of them, time domain:")
print("  lanes                " + " ".join(f"{x:8.1e}" for x in run_a.ber)
      + "   errors " + " ".join(map(str, run_a.n_errors)))
print("  statistical, clock where it starts  " + " ".join(f"{x:8.1e}" for x in at_start)
      + "   ratio " + " ".join(f"{a / max(t, 1e-300):.2f}" for a, t in zip(at_start, run_a.ber)))
print(f"  statistical, clock at the mean skew ({mean_skew:+.3f} UI) " + " ".join(f"{x:8.1e}" for x in at_mean)
      + "   ratio " + " ".join(f"{a / max(t, 1e-300):.2f}" for a, t in zip(at_mean, run_a.ber))
      + f"  [{time.time() - t0:.0f}s]")

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(2, len(LINKS), figsize=(15, 8))
x = np.linspace(-0.5, 0.5, 401)
for j, (name, (cs, target, tname, sweep)) in enumerate(results.items()):
    ax = axes[0, j]
    for (lab, c), st in zip(cs.items(), ("-", "--")):
        ax.semilogy(x, c.at(x), st, label=lab or "lane")
    main = next(iter(cs.values()))
    w = main.window(target)
    if w is not None and np.isfinite(w[0]):
        ax.axvspan(max(w[0], -0.5), min(w[1], 0.5), color="C2", alpha=0.12,
                   label=f"window at {tname}")
    ax.axhline(target, color="k", ls=":", lw=1)
    r = shared_cdr(main, SKEWS, "minmax")
    ax.plot(r.offsets_ui, r.ber, "kv", label="4 lanes, minmax clock")
    ax.set(xlabel="sampling offset from the lane's own lock point [UI]", ylabel="BER", title=name)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7)
    ax = axes[1, j]
    for pol, ys in sweep.items():
        ax.semilogy(SPREADS, ys, "o-", ms=3, label=pol)
    ax.axhline(target, color="k", ls=":", lw=1)
    ax.set(xlabel="four lanes spread over [0, s] UI: s", ylabel="worst lane's BER")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7)
fig.tight_layout()
out = OUT / "45_shared_cdr_skew.png"
fig.savefig(out, dpi=130)
print(f"wrote {out}")
