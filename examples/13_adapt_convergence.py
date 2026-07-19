"""Adaptation-loop convergence, NRZ and PAM4 (product mixed-signal RX).

Both canonical links run with the DFE cold-started from ZERO taps
(dfe.init = "zero") so the full sign-sign LMS transient is visible:

    settle (CDR only) -> data-aided training -> decision-directed tracking

Plots per modulation: DFE tap trajectories (dashed lines = channel-cursor
targets) and the slicer-error RMS learning curve.
"""

import dataclasses
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.config import apply_overrides, load_config  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.static_link import make_pattern  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

CASES = {
    "NRZ 16 Gb/s": (REPO / "configs" / "nrz_16g_ms.yaml",
                    {"channel.file": str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p")}),
    "PAM4 32 Gb/s": (REPO / "configs" / "pam4_32g_ms.yaml", {}),
}

fig, axes = plt.subplots(2, 2, figsize=(13, 8))

for row, (name, (yaml_path, over)) in enumerate(CASES.items()):
    cfg = load_config(yaml_path, overrides={**over, "sim.n_symbols": 120_000})
    # cold start: zero DFE taps, slightly hotter mu to make the walk visible
    cfg = apply_overrides(cfg, {"rx.dfe.init": "zero", "rx.dfe.mu": 1.0e-3})
    res = run_time_link(cfg)

    # channel-cursor targets = what the taps should converge to
    cfg_ref = apply_overrides(cfg, {"rx.dfe.init": "cursor"})
    res_ref = run_time_link(cfg_ref)
    targets = res_ref.extras["w_dfe0"]

    wh = res.extras["w_dfe_hist"]
    n_ave = res.extras["n_ave"]
    x = np.arange(wh.shape[0]) * n_ave / 1e3
    settle = res.extras["settle"] / 1e3
    train_end = res.extras["train_end"] / 1e3

    ax = axes[row, 0]
    for t in range(wh.shape[1]):
        ax.plot(x, wh[:, t], label=f"tap {t + 1}", lw=1.2)
        ax.axhline(targets[t], color=f"C{t}", ls="--", lw=0.8, alpha=0.6)
    ax.axvspan(0, settle, color="gray", alpha=0.15)
    ax.axvspan(settle, train_end, color="green", alpha=0.10)
    ax.text(settle / 2, ax.get_ylim()[1] * 0.9, "settle", fontsize=7, ha="center")
    ax.text((settle + train_end) / 2, ax.get_ylim()[1] * 0.9, "train", fontsize=7,
            ha="center", color="green")
    ax.set(xlabel="symbol [k]", ylabel="DFE tap value",
           title=f"{name}: tap trajectories (cold start, dashed = cursor targets)")
    ax.legend(fontsize=7, loc="center right")
    ax.grid(True, alpha=0.3)

    # slicer-error learning curve (the stored slicer window starts at warmup,
    # i.e. right where decision-directed tracking begins)
    symbols = make_pattern(cfg)
    levels = res.extras["levels"]
    ref = symbols[: res.extras["phase_track"].size]
    ax = axes[row, 1]
    y_sl = res.y_slicer
    warm = res.extras["warmup"]
    ideal = levels[ref[warm: warm + y_sl.size]]
    e = y_sl - ideal
    win = 500
    rms = np.sqrt(np.convolve(e ** 2, np.ones(win) / win, mode="valid"))
    ax.semilogy(warm / 1e3 + np.arange(rms.size) / 1e3, rms * 1e3, lw=0.9,
                label="slicer error RMS (post-warmup)")
    ax.axvline(train_end, color="green", ls=":", lw=1, label="train end (DD start)")
    ax.set(xlabel="symbol [k]", ylabel="error RMS [mV]",
           title=f"{name}: slicer-error learning curve  ({res.summary()})")
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)
    print(f"{name}: taps zero-init -> {np.round(res.dfe_taps, 4).tolist()}")
    print(f"          cursor targets      {np.round(targets, 4).tolist()}")
    print(f"          {res.summary()}")

fig.tight_layout()
fig.savefig(OUT / "13_adapt_convergence.png", dpi=130)
print(f"wrote {OUT / '13_adapt_convergence.png'}")
