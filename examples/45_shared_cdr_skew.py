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
starts from, re-solved at each offset or held at the lock point's. Re-solved
it reads 1.4-4.5x above a time-domain lane held at the offset; which FFE a
lane converges to is ROADMAP P2 #21 (cairn/多lane建模.md §8, where the
time-domain cross-check is).

Pass ``--quick`` for a smoke run (fewer phases).
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
    print(f"  own loop: {float(main.at(0.0)):.2e} at its lock point; lowest {float(main.at(b)):.2e} "
          f"at {b:+.3f} UI")
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
    print("  four lanes, skews " + " ".join(f"{s:+.2f}" for s in SKEWS) + " UI:")
    for pol in ("master", "average", "minmax"):
        r = shared_cdr(main, SKEWS, pol)
        print(f"    {pol:8s} clock at {r.phase_ui:+.3f} UI   lanes "
              + " ".join(f"{x:8.1e}" for x in r.ber)
              + f"   worst {r.worst:.1e} (lane {int(np.argmax(r.ber))}), mean {r.mean:.1e}")
    mm = shared_cdr(main, SKEWS, "minmax")
    print(f"  minmax worst lane / one lane on its own loop: {mm.worst / float(main.at(0.0)):.2f}")
    sweep = {pol: [shared_cdr(main, s * np.array([0, 1, 2, 3]) / 3, pol).worst for s in SPREADS]
             for pol in ("master", "average", "minmax")}
    results[name] = (cs, target, tname, sweep)
    print(f"  [{time.time() - t0:.0f}s]")

if not QUICK:
    # the master's lane reads its own loop's BER; the minimax never does worse
    # than the master on its worst lane
    for name, (cs, target, tname, sweep) in results.items():
        main = next(iter(cs.values()))
        m = shared_cdr(main, SKEWS, "master")
        assert m.ber[0] == float(main.at(0.0))
        assert shared_cdr(main, SKEWS, "minmax").worst <= m.worst * (1 + 1e-9)
    adc = results["PAM4 224G ADC (106 GBd)"][0]
    moved, held = adc["FFE re-solved"], adc["FFE held at lock"]
    assert float(held.at(0.25)) > 5 * float(moved.at(0.25))

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
