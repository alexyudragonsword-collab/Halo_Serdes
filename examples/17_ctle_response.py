"""CTLE frequency response across rates and architectures.

The parameterized Ctle (one zero, two poles) derives its poles from the link
Nyquist frequency, so the same peak_db config yields a Nyquist-scaled shape.
Two comparisons:

1. same config across rates (112G vs 224G ADC links): identical shape scaled
   2x in frequency — and a note that the *realized* peaking is below the
   nominal peak_db because the 2x-Nyquist second pole rolls the zero boost
   back down;
2. ADC-arch light CTLE (~1.5 dB, only pre-conditions the ADC input) vs
   mixed-signal aggressive CTLE (9-12 dB, must open the eye before the
   slicer) — the analog-EQ burden that distinguishes the architectures.
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

from halo_serdes.afe import Ctle  # noqa: E402
from halo_serdes.config.schema import CtleConfig  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

f = np.linspace(1e8, 130e9, 5000)


def mag_db(ctle):
    return 20 * np.log10(np.abs(ctle.transfer(f)))


def gain_at(ctle, freq):
    return 20 * np.log10(abs(ctle.transfer(np.array([freq]))[0]))


fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))

# --- panel 1: same peak_db=4 config, 112G vs 224G (Nyquist-scaled) ---
ax = axes[0]
print("== Same config (peak_db=4) across rates: poles/zero derived from Nyquist ==")
for fnyq, rate, c in ((28e9, "112 Gb/s (28 GHz Nyq)", "C0"),
                      (56e9, "224 Gb/s (56 GHz Nyq)", "C3")):
    ctle = Ctle.from_config(CtleConfig(enable=True, peak_db=4.0), fnyq)
    ax.plot(f / 1e9, mag_db(ctle), c, label=rate)
    ax.axvline(fnyq / 1e9, color=c, ls=":", alpha=0.5)
    print(f"  {rate}: fz={ctle.fz/1e9:.1f} GHz, fp1={ctle.fp1/1e9:.0f} GHz, "
          f"fp2={ctle.fp2/1e9:.0f} GHz | realized peaking {ctle.peaking_db():.2f} dB "
          f"(nominal 4 dB), @Nyq {gain_at(ctle, fnyq):+.1f} dB")
ax.set(xlabel="Frequency [GHz]", ylabel="|H| [dB]", xlim=(0, 130),
       title="Same config across rates: same shape, 2x frequency scaling\n(dotted = respective Nyquist)")
ax.legend(fontsize=8)

# --- panel 2: nominal vs realized peaking sweep (224G Nyquist) ---
ax = axes[1]
print("== Nominal peak_db vs realized peaking (224G, fnyq=56 GHz) ==")
nominal = np.arange(2, 21, 2)
realized = []
for pk in nominal:
    ctle = Ctle.from_config(CtleConfig(enable=True, peak_db=float(pk)), 56e9)
    realized.append(ctle.peaking_db())
ax.plot(nominal, realized, "o-", color="C2")
ax.plot(nominal, nominal, "k--", lw=0.8, alpha=0.5, label="ideal y=x")
for pk, rz in zip(nominal, realized):
    print(f"  nominal {pk:2d} dB -> realized {rz:.2f} dB")
ax.set(xlabel="Config peak_db [dB]", ylabel="Realized peaking [dB]",
       title="Nominal vs realized peaking\n(2x-Nyquist second pole lowers boost)")
ax.legend(fontsize=8)

# --- panel 3: ADC light CTLE vs mixed-signal aggressive CTLE ---
ax = axes[2]
print("== Architecture comparison (28 GHz Nyquist) ==")
for pk, label, c in ((4.0, "ADC arch (peak_db=4)", "C0"),
                     (12.0, "mixed-signal (peak_db=12)", "C3")):
    ctle = Ctle.from_config(CtleConfig(enable=True, peak_db=pk), 28e9)
    ax.plot(f / 1e9, mag_db(ctle), c, label=f"{label}: peaking {ctle.peaking_db():.1f} dB")
    print(f"  {label}: realized peaking {ctle.peaking_db():.1f} dB, "
          f"@Nyq {gain_at(ctle, 28e9):+.1f} dB")
ax.axvline(28, color="gray", ls=":", alpha=0.6)
ax.set(xlabel="Frequency [GHz]", ylabel="|H| [dB]", xlim=(0, 130),
       title="Architecture comparison: ADC light EQ vs mixed-signal aggressive EQ\n(digital-domain vs analog-domain EQ burden)")
ax.legend(fontsize=8)

for a in axes:
    a.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "17_ctle_response.png", dpi=130)
print(f"wrote {OUT / '17_ctle_response.png'}")
