"""Crosstalk from the config: synchronous lanes at a fixed skew against
quasi-synchronous lanes whose phase drifts.

``LinkConfig.crosstalk`` describes the victim lane's aggressors -- here a bank
of two FEXT and two NEXT lanes (``aggressor_bank``: couplings drawn within
+-2 dB of a common level, independent data) -- and both engines build them
from it. Where those lanes sit in time decides what the victim sees:

- synchronous (the victim's own clock, ``ppm`` 0): every aggressor launches
  ``skew_ui`` UI after the victim, so the victim samples each coupling pulse
  at one fixed phase. Example 24 and ``cairn/多lane建模.md`` §3 measured how
  much that phase matters;
- quasi-synchronous (their own reference clocks, ``ppm`` off the victim's):
  the phase walks ``ppm`` 1e-6 UI per UI and the victim sees every one in
  turn. The time engine holds each aggressor's symbols at its own rate; the
  statistical engine averages the crosstalk PDF over the phase.

Three links (the presets' receivers, receiver noise set so the crosstalk
shows): each at eight skews, synchronous, then at +-100 ppm. Time-domain BER,
statistical BER and their ratio (invariant 3: within 2x).

Pass ``--quick`` for a smoke run (fewer symbols and skews).
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

from halo_serdes.config import CrosstalkConfig  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.statistical import run_statistical  # noqa: E402
from halo_serdes_app import config_bridge as cb  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

QUICK = "--quick" in sys.argv
N_SYM = 60_000 if QUICK else 300_000
SKEWS = [0.0, 0.25, 0.5, 0.75] if QUICK else [k / 8 for k in range(8)]
PPMS = [100.0, -100.0]

# preset, receiver noise [V rms], bank coupling level [dB]
LINKS = {"PAM4 224G ADC (106 GBd)": (0.0015, -46.0),
         "PAM4 32G mixed-signal": (0.006, -34.0),
         "NRZ 28G analytic (COM/xtalk)": (0.010, -27.0)}


def link(name: str, **xt):
    noise, cdb = LINKS[name]
    cfg = cb.load_preset(name)
    return dataclasses.replace(
        cfg, rx=dataclasses.replace(cfg.rx, noise_rms=noise),
        sim=dataclasses.replace(cfg.sim, n_symbols=N_SYM),
        crosstalk=CrosstalkConfig(n_fext=2, n_next=2, coupling_db=cdb, seed=1, **xt))


def both(cfg) -> tuple[float, int, float]:
    """Time-domain BER and error count, statistical BER (the ADC receiver's
    with the FFE the time run converged to)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")          # the presets' envelope notes
        t = run_time_link(cfg)
        kw = dict(ffe_taps=t.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre) if cfg.rx.arch == "adc_dsp" else {}
        s = run_statistical(cfg, **kw)
    return t.ber.ber, t.ber.n_errors, s.ber


results = {}
print("Crosstalk from LinkConfig.crosstalk: 2 FEXT + 2 NEXT lanes, synchronous at a skew or")
print(f"quasi-synchronous at +-100 ppm; {N_SYM} symbols per run")
for name in LINKS:
    t0 = time.time()
    noise, cdb = LINKS[name]
    print(f"\n{name}  (noise {noise * 1e3:.1f} mV, couplings around {cdb:.0f} dB)")
    print("  lanes                time BER (errors)     stat BER   stat / time")
    rows = []
    for s in SKEWS:
        t, n, st = both(link(name, skew_ui=s))
        rows.append(("sync", s, t, n, st))
        print(f"  sync, skew {s:5.3f} UI   {t:9.2e} ({n:6d})   {st:9.2e}   {st / t:5.2f}")
    for p in PPMS:
        t, n, st = both(link(name, ppm=p))
        rows.append(("ppm", p, t, n, st))
        print(f"  quasi-sync {p:+5.0f} ppm  {t:9.2e} ({n:6d})   {st:9.2e}   {st / t:5.2f}")
    sync_t = [r[2] for r in rows if r[0] == "sync"]
    sync_s = [r[4] for r in rows if r[0] == "sync"]
    q_t = [r[2] for r in rows if r[0] == "ppm"]
    print(f"  synchronous: time BER {min(sync_t):.2e} ... {max(sync_t):.2e} over the skews "
          f"({max(sync_t) / min(sync_t):.1f}x), mean {np.mean(sync_t):.2e}; statistical mean "
          f"{np.mean(sync_s):.2e}")
    print("  quasi-synchronous / mean over the skews (time): "
          + " ".join(f"{q / np.mean(sync_t):.2f}" for q in q_t) + f"  [{time.time() - t0:.0f}s]")
    results[name] = rows

sync_r = [r[4] / r[2] for rows in results.values() for r in rows if r[0] == "sync"]
quasi_r = [r[4] / r[2] for rows in results.values() for r in rows if r[0] == "ppm"]
print(f"\nstat / time: synchronous {min(sync_r):.2f} ... {max(sync_r):.2f} ({len(sync_r)} runs), "
      f"quasi-synchronous {min(quasi_r):.2f} ... {max(quasi_r):.2f} ({len(quasi_r)} runs)")
if not QUICK:
    # invariant 3. The NRZ preset at 7/8 UI synchronous reads 0.41: its bathtub
    # climbs 17x within 1/16 UI of the lock point (cairn/多lane建模.md §3)
    assert all(0.5 < x < 2.0 for x in quasi_r), quasi_r
    assert sum(not 0.5 < x < 2.0 for x in sync_r) <= 1, sync_r

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, len(LINKS), figsize=(15, 4.4))
for ax, (name, rows) in zip(axes, results.items()):
    sk = [r[1] for r in rows if r[0] == "sync"]
    ax.semilogy(sk, [r[2] for r in rows if r[0] == "sync"], "o-", label="time, synchronous")
    ax.semilogy(sk, [r[4] for r in rows if r[0] == "sync"], "s--", label="stat, synchronous")
    for r, c in zip([r for r in rows if r[0] == "ppm"], ("C2", "C3")):
        ax.axhline(r[2], color=c, lw=1.2, label=f"time, {r[1]:+.0f} ppm")
    ax.axhline([r[4] for r in rows if r[0] == "ppm"][0], color="k", ls=":", label="stat, quasi-sync")
    ax.set(xlabel="aggressor skew [UI]", ylabel="BER", title=name)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7)
fig.tight_layout()
out = OUT / "44_quasi_sync_xtalk.png"
fig.savefig(out, dpi=130)
print(f"wrote {out}")
