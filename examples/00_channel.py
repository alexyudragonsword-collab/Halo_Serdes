"""Phase 0 deliverable: load an s4p channel, plot loss Bode + impulse + pulse.

Usage:
    python examples/00_channel.py [path/to/channel.s4p]

Defaults to the classic IEEE 802.3 backplane channel in data/channels/.
Outputs PNG figures to examples/output/.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.channel import ChannelModel  # noqa: E402

# Default: TEC Whisper 42.8" backplane (802.3ck COM reference, DC-40 GHz) —
# valid past the 16 GHz Nyquist of 32G NRZ. The peters_01_0605 channels in
# data/channels/ are only measured to 15 GHz; use them for <=16G rates.
S4P = sys.argv[1] if len(sys.argv) > 1 else str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p")
OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

# --- 32G NRZ setup: UI = 31.25 ps, OSR 32 ---
SYMBOL_RATE = 32e9
UI = 1 / SYMBOL_RATE
OSR = 32
DT = UI / OSR

cm = ChannelModel.from_touchstone(S4P, f_max=4 * SYMBOL_RATE, n_freq=4096)
print(f"channel: {Path(S4P).name}")
print(f"  insertion loss @ Nyquist ({SYMBOL_RATE / 2e9:.0f} GHz): {cm.loss_at(SYMBOL_RATE / 2):.1f} dB")

rs = cm.response_set(DT)
h = rs.h
p = cm.pulse(DT, OSR)

fig, axes = plt.subplots(1, 3, figsize=(15, 4))

axes[0].plot(cm.f / 1e9, cm.insertion_loss_db())
axes[0].axvline(SYMBOL_RATE / 2e9, ls="--", c="gray", label="Nyquist")
axes[0].set(xlabel="Frequency [GHz]", ylabel="Insertion loss [dB]",
            title="Differential channel |H|", ylim=(-60, 5))
axes[0].legend()
axes[0].grid(True, alpha=0.3)

axes[1].plot(h.t * 1e9, h.y)
axes[1].set(xlabel="Time [ns]", ylabel="h [per-sample]", title=f"Impulse response (dt={h.dt * 1e12:.2f} ps)")
axes[1].grid(True, alpha=0.3)

peak = int(np.argmax(p.y))
win = slice(max(0, peak - 5 * OSR), peak + 15 * OSR)
axes[2].plot((p.t[win] - p.t[peak]) / UI, p.y[win])
# mark UI-spaced cursors
for k in range(-3, 12):
    idx = peak + k * OSR
    if 0 <= idx < p.y.size:
        axes[2].plot(k, p.y[idx], "ro", ms=4)
axes[2].set(xlabel="Time [UI, centered on main cursor]", ylabel="Pulse response [V]",
            title="1-UI pulse response + ISI cursors")
axes[2].grid(True, alpha=0.3)

fig.tight_layout()
out_png = OUT / f"00_channel_{Path(S4P).stem}.png"
fig.savefig(out_png, dpi=130)
print(f"  wrote {out_png}")

# ISI summary
cursors = [float(p.y[peak + k * OSR]) for k in range(-3, 12) if 0 <= peak + k * OSR < p.y.size]
main = max(cursors)
isi = sum(abs(c) for c in cursors) - main
print(f"  main cursor: {main:.4f} V, sum|ISI| (±3/+11 UI): {isi:.4f} V, ISI/main = {isi / main:.2f}")
