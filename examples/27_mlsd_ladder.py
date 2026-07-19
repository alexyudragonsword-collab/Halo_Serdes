"""MLSD gain ladder: DFE vs sliding-detector vs Viterbi-MLSE.

On a residual channel (main cursor + one dominant postcursor, the shape left
after a finite FFE), three detectors are compared over a noise sweep:

* ideal DFE (decision feedback of the postcursor) — the baseline;
* sliding detector (DragonPHY error-event post-corrector, low cost);
* Viterbi MLSE (full sequence detector, the high-end option).

The measured Viterbi gain is checked against the analytic asymptotic MLSE
coding gain over an ideal DFE, ``10·log10(d_min² / main²)`` — a matched-filter
bound the framework computes in closed form (mlse_gain_over_dfe_db).
"""

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

from halo_serdes.dsp.mlsd import (  # noqa: E402
    mlse_gain_over_dfe_db, post_detect,
)

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

levels = np.array([-1.0, 1.0])
cursors = np.array([1.0, 0.55])            # residual: main + one postcursor
main = cursors[0]
n = 200_000
rng = np.random.default_rng(2)
sym = rng.integers(0, 2, size=n)
v = levels[sym]
y_clean = np.convolve(v, cursors)[:n]

analytic_gain = mlse_gain_over_dfe_db(cursors)
print(f"residual channel {cursors.tolist()}: analytic MLSE gain over DFE "
      f"= {analytic_gain:.2f} dB")

sigmas = np.linspace(0.20, 0.48, 9)
ber_dfe, ber_sld, ber_vit = [], [], []
for sigma in sigmas:
    y = y_clean + rng.normal(scale=sigma, size=n)
    # ideal DFE: subtract the postcursor of the *decided* previous symbol
    dec_dfe = np.zeros(n, dtype=np.int64)
    prev = 0.0
    for k in range(n):
        dec_dfe[k] = 1 if (y[k] - cursors[1] * prev) > 0 else 0
        prev = levels[dec_dfe[k]]
    ber_dfe.append(max(np.mean(dec_dfe != sym), 1e-7))
    ber_sld.append(max(np.mean(post_detect(y, cursors, levels, "sliding") != sym), 1e-7))
    ber_vit.append(max(np.mean(post_detect(y, cursors, levels, "viterbi") != sym), 1e-7))
    print(f"  σ={sigma:.3f}: DFE={ber_dfe[-1]:.2e}  sliding={ber_sld[-1]:.2e}  "
          f"Viterbi={ber_vit[-1]:.2e}")

# SNR axis: 20 log10(main / sigma)
snr_db = 20 * np.log10(main / sigmas)

fig, ax = plt.subplots(figsize=(8.2, 5.2))
ax.semilogy(snr_db, ber_dfe, "o-", label="ideal DFE", color="C7")
ax.semilogy(snr_db, ber_sld, "s-", label="sliding detector", color="C1")
ax.semilogy(snr_db, ber_vit, "^-", label="Viterbi MLSE", color="C0")
# analytic MLSE curve: DFE curve shifted left by the coding gain
ax.semilogy(snr_db + analytic_gain, ber_dfe, "k--", alpha=0.6,
            label=f"DFE shifted +{analytic_gain:.1f} dB (analytic MLSE)")
ax.set(xlabel="SNR = 20·log₁₀(main/σ) [dB]", ylabel="BER",
       title="MLSD gain ladder on a residual channel (main + 0.55 postcursor)")
ax.grid(True, which="both", alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig(OUT / "27_mlsd_ladder.png", dpi=130)
print(f"wrote {OUT / '27_mlsd_ladder.png'}")
