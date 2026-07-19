"""Probe the mixed-signal envelope: sweep data rate until the eye closes.

NRZ from 16 Gb/s in 2 Gb/s steps; PAM4 from 32 Gb/s in 4 Gb/s steps (both
are 1 GBd steps — the architecture's real axis is symbol rate). Product RX
partition throughout: CTLE (best of 4 peaking gears per point) + 4-tap DFE
(tap-1 unrolled) + no RX FFE. Two reference channels:

- Whisper 42.8" backplane (802.3ck, harsh);
- the canonical analytic trace from pam4_32g_ms.yaml (moderate).

"Eye open" criterion: worst-case post-DFE inner eye (min PAM4 sub-eye) > 0
at the sampling instant over 3000 noisy traces. The sweep stops after the
eye stays closed for two consecutive steps.
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
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.channel.response import pulse_from_impulse  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.waveform import Waveform  # noqa: E402
from halo_serdes.dsp import channel_cursors, dfe_static  # noqa: E402
from halo_serdes.engine.static_link import _levels, fold_eye, make_pattern  # noqa: E402
from halo_serdes.tx.builder import symbols_to_voltages  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

N_SYM = 40_000
NOISE = 0.002
CTLE_GEARS = (3.0, 6.0, 9.0, 12.0)

CHANNELS = {
    "Whisper backplane": ChannelConfig(
        kind="touchstone",
        file=str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p"),
        n_freq=4096),
    "Analytic trace 0.2m": ChannelConfig(kind="analytic", length_m=0.2, rdc=3.0,
                                    r_skin=2.5e-3, loss_tangent=0.015),
}


def measure_inner_eye(modulation: str, symbol_rate: float, ch_cfg: ChannelConfig,
                      peak_db: float) -> tuple[float, float]:
    """Reconstruct the post-DFE eye; return (worst inner eye [V], SER)."""
    cfg = LinkConfig(
        modulation=modulation, symbol_rate=symbol_rate, osr=16,
        channel=ch_cfg,
        tx=TxConfig(swing=1.0),
        rx=RxConfig(ctle=CtleConfig(enable=True, peak_db=peak_db),
                    dfe=DfeConfig(n_taps=4, tap1_mode="unrolled"),
                    noise_rms=NOISE),
        sim=SimConfig(n_symbols=N_SYM, seed=17,
                      pattern="prbs13q" if modulation == "pam4" else "prbs31"),
    )
    channel = ChannelModel.from_config(cfg)
    rng = np.random.default_rng(cfg.sim.seed)
    symbols = make_pattern(cfg)
    v = symbols_to_voltages(symbols, cfg)
    y_tx = np.repeat(v, cfg.osr)
    h = channel.response_set(cfg.dt).h.y
    ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
    nfft = int(2 ** np.ceil(np.log2(max(h.size * 4, 8))))
    f = np.fft.rfftfreq(nfft, d=cfg.dt)
    h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    n = y_tx.size
    rx = np.fft.irfft(np.fft.rfft(y_tx) * np.fft.rfft(h, n), n)
    rx += rng.normal(scale=NOISE, size=n)

    osr = cfg.osr
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    cur = channel_cursors(pulse, osr, 0, 4, peak_idx=peak)
    main = cur[0]
    if abs(main) < 1e-6:
        return float("-inf"), 1.0
    w_dfe = cur[1:] / main
    levels = _levels(cfg) * abs(main)
    phase = peak % osr

    y_baud = rx[phase::osr]
    dec, y_eq = dfe_static(y_baud.astype(np.float64), w_dfe, levels)

    # decision k corresponds to transmitted symbol k - delay
    delay = peak // osr
    n_cmp = min(dec.size, symbols.size + delay)
    d = dec[delay + 8: n_cmp]
    r = symbols[8: n_cmp - delay]
    ser = float(np.mean(d != r))

    # worst-case inner eye at the sampler, grouped by the TRUE symbol
    # (grouping by measured value against the thresholds is always >= 0 by
    # construction and cannot detect a closed eye)
    v = y_eq[delay + 8: n_cmp]
    sub = []
    for i in range(len(levels) - 1):
        lo_g = v[r == i]
        hi_g = v[r == i + 1]
        if lo_g.size == 0 or hi_g.size == 0:
            return float("-inf"), ser
        sub.append(float(hi_g.min() - lo_g.max()))
    return float(min(sub)), ser


def sweep(modulation: str, start_gbps: float, step_gbps: float, ch_name: str,
          ch_cfg: ChannelConfig, max_points: int = 26):
    bits = 2 if modulation == "pam4" else 1
    rows = []
    closed_streak = 0
    rate = start_gbps
    for _ in range(max_points):
        fs = rate * 1e9 / bits
        best_eye, best_ser, best_peak = -np.inf, 1.0, None
        for pk in CTLE_GEARS:
            try:
                e, s = measure_inner_eye(modulation, fs, ch_cfg, pk)
            except Exception:
                e, s = float("-inf"), 1.0
            if e > best_eye:
                best_eye, best_ser, best_peak = e, s, pk
        cm = ChannelModel.from_config(LinkConfig(
            modulation=modulation, symbol_rate=fs, channel=ch_cfg))
        loss = cm.loss_at(fs / 2)
        rows.append((rate, best_eye, best_ser, best_peak, loss))
        state = "OPEN " if best_eye > 0 else "closed"
        print(f"  {modulation.upper():4s} {rate:5.0f} Gb/s ({fs / 1e9:4.0f} GBd, "
              f"{loss:6.1f} dB@Nyq): eye={best_eye * 1e3:7.1f} mV [{state}] "
              f"SER={best_ser:.1e} (CTLE {best_peak:.0f} dB)")
        closed_streak = closed_streak + 1 if best_eye <= 0 else 0
        if closed_streak >= 2:
            break
        rate += step_gbps
    return rows


results = {}
for ch_name, ch_cfg in CHANNELS.items():
    print(f"== {ch_name} ==")
    results[("nrz", ch_name)] = sweep("nrz", 16, 2, ch_name, ch_cfg)
    results[("pam4", ch_name)] = sweep("pam4", 32, 4, ch_name, ch_cfg)

# ------------------------------------------------------------------ plot ---
fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.4))
styles = {"nrz": "o-", "pam4": "s--"}
colors = {"Whisper backplane": "C0", "Analytic trace 0.2m": "C2"}
for (mod, ch_name), rows in results.items():
    r = np.array([[x[0], x[1]] for x in rows])
    axes[0].plot(r[:, 0], np.maximum(r[:, 1], -0.005) * 1e3, styles[mod],
                 color=colors[ch_name], label=f"{mod.upper()} @ {ch_name}")
axes[0].axhline(0, color="r", lw=1)
axes[0].set(xlabel="Data rate [Gb/s]", ylabel="Worst inner eye post-DFE [mV]",
            title="Mixed-signal eye height vs data rate (closed = hits red)")
axes[0].legend(fontsize=8)

for (mod, ch_name), rows in results.items():
    r = np.array([[x[0] / (2 if mod == "pam4" else 1), x[1]] for x in rows])
    axes[1].plot(r[:, 0], np.maximum(r[:, 1], -0.005) * 1e3, styles[mod],
                 color=colors[ch_name], label=f"{mod.upper()} @ {ch_name}")
axes[1].axhline(0, color="r", lw=1)
axes[1].set(xlabel="Symbol rate [GBd]", ylabel="Worst inner eye post-DFE [mV]",
            title="Same data, symbol-rate axis (architecture's real axis)")
axes[1].legend(fontsize=8)
for ax in axes:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "12_ms_limit_sweep.png", dpi=130)

print("== Limit (last point with an open eye) ==")
for (mod, ch_name), rows in results.items():
    open_rows = [r for r in rows if r[1] > 0]
    if open_rows:
        last = open_rows[-1]
        fs = last[0] / (2 if mod == "pam4" else 1)
        print(f"  {mod.upper():4s} @ {ch_name}: {last[0]:.0f} Gb/s "
              f"({fs:.0f} GBd, {last[4]:.1f} dB@Nyq, eye {last[1] * 1e3:.1f} mV)")
    else:
        print(f"  {mod.upper():4s} @ {ch_name}: closed at start")
print(f"wrote {OUT / '12_ms_limit_sweep.png'}")
