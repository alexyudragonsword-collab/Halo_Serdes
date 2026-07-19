"""CDR locking dynamics, NRZ 16G and PAM4 32G (product mixed-signal RX).

Three views per modulation, driving the ms_rx kernel directly so the initial
condition is controllable (the engine normally starts at the pulse peak —
already locked):

1. phase pull-in: start the sampler at -0.4..+0.4 UI away from the lock
   point, watch the bang-bang loop walk in;
2. frequency acquisition: +/-200 ppm symbol-rate offset — the proportional
   branch alone cannot hold a ramp, the integral branch must absorb it
   (instantaneous period estimate converging to osr*(1 -/+ eps));
3. lock detection: |moving mean of the PD| under threshold (PyBERT-style
   hysteresis criterion), lock instant marked.
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
from halo_serdes.cdr import ms_rx  # noqa: E402
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.channel.response import pulse_from_impulse  # noqa: E402
from halo_serdes.config import load_config  # noqa: E402
from halo_serdes.core.waveform import Waveform  # noqa: E402
from halo_serdes.dsp import channel_cursors  # noqa: E402
from halo_serdes.engine.static_link import _levels, make_pattern  # noqa: E402
from halo_serdes.tx.builder import symbols_to_voltages  # noqa: E402
from halo_serdes.tx.jitter import jittered_zoh  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

CASES = {
    "NRZ 16 Gb/s": (REPO / "configs" / "nrz_16g_ms.yaml",
                    {"channel.file": str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p")}),
    "PAM4 32 Gb/s": (REPO / "configs" / "pam4_32g_ms.yaml", {}),
}

N_SYM = 30_000


def setup(yaml_path, over, ppm=0.0):
    cfg = load_config(yaml_path, overrides={**over, "sim.n_symbols": N_SYM + 2000})
    channel = ChannelModel.from_config(cfg)
    rng = np.random.default_rng(3)
    symbols = make_pattern(cfg)
    v = symbols_to_voltages(symbols, cfg)
    jit = -ppm * 1e-6 * cfg.ui * np.arange(v.size + 1)  # rate offset as ramp
    y_tx = jittered_zoh(v, cfg.osr, jit, cfg.ui)
    h = channel.response_set(cfg.dt).h.y
    ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
    nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
    f = np.fft.rfftfreq(nfft, d=cfg.dt)
    h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    n = y_tx.size
    rx = np.fft.irfft(np.fft.rfft(y_tx) * np.fft.rfft(h, n), n)
    rx += rng.normal(scale=cfg.rx.noise_rms, size=n)
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), cfg.osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    cur = channel_cursors(pulse, cfg.osr, 0, cfg.rx.dfe.n_taps, peak_idx=peak)
    w_dfe = cur[1:] / cur[0]
    levels = _levels(cfg) * abs(cur[0])
    return cfg, rx, peak, w_dfe, levels


def run_cdr(cfg, rx, pos0, w_dfe, levels):
    osr = cfg.osr
    kp = osr * 2.0 ** (-cfg.rx.cdr.kp_shift)
    ki = osr * 2.0 ** (-cfg.rx.cdr.ki_shift)
    ref = np.full(N_SYM, -1, dtype=np.int64)
    return ms_rx(rx, osr, float(pos0), N_SYM, levels.astype(np.float64),
                 np.asarray(w_dfe, float), 0.0, 100, kp, ki, 0.0, 1.0,
                 ref, 0, 0,
                 1 if cfg.rx.dfe.tap1_mode == "unrolled" else 0,
                 np.zeros(levels.size))


def lock_metric(ph, osr, peak, win=200, thresh_ui=0.2):
    """Lock = phase stationarity: peak-to-peak of the phase deviation over a
    trailing window below thresh (dither pp ~0.1 UI), sustained one window.
    (PD-mean and period-mean criteria are both fooled during pull-in: garbage
    decisions randomize the PD, and a slow walk averages to ~nominal.)"""
    dev = (ph - peak - np.arange(ph.size) * osr) / osr
    n = dev.size
    ptp = np.array([np.ptp(dev[max(0, i - win): i + 1]) for i in range(n)])
    locked = ptp < thresh_ui
    idx = None
    run = 0
    for i in range(win, n):
        run = run + 1 if locked[i] else 0
        if run >= win:
            idx = i - win + 1
            break
    return ptp, idx


fig, axes = plt.subplots(2, 3, figsize=(15.5, 8))

for row, (name, (yaml_path, over)) in enumerate(CASES.items()):
    # --- (a) phase pull-in from several initial offsets ---
    cfg, rx, peak, w_dfe, levels = setup(yaml_path, over)
    osr = cfg.osr
    ax = axes[row, 0]
    for off_ui in (-0.4, -0.2, 0.2, 0.4):
        dec, ys, ph, w, pd, wh = run_cdr(cfg, rx, peak + off_ui * osr, w_dfe, levels)
        dev = (ph - peak - np.arange(ph.size) * osr) / osr
        ax.plot(np.arange(min(ph.size, 600)), dev[:600], lw=1.0,
                label=f"初始 {off_ui:+.1f} UI")
        inside = np.nonzero(np.abs(dev) < 0.05)[0]
        if inside.size and abs(off_ui) > 0.05:
            print(f"{name}: 初始 {off_ui:+.1f} UI -> 牵引进 0.05 UI 用时 "
                  f"{inside[0]} 符号")
    ax.axhline(0, color="gray", lw=0.6)
    ax.set(xlabel="symbol", ylabel="相位偏差 [UI]",
           title=f"{name}: 相位牵引(bang-bang 斜率 ~ Kp x 跳变密度)")
    ax.legend(fontsize=7)

    # --- (b) frequency tracking: +/-200 ppm — phase-ramp view (the period
    # view drowns in bang-bang dither: MA-500 still leaves ~1000 ppm noise) ---
    ax = axes[row, 1]
    for ppm, c in ((+200, "C0"), (-200, "C3")):
        cfg2, rx2, peak2, w2, lv2 = setup(yaml_path, over, ppm=ppm)
        dec, ys, ph, w, pd, wh = run_cdr(cfg2, rx2, peak2, w2, lv2)
        k = np.arange(ph.size)
        dev = (ph - peak2 - k * osr) / osr
        ideal = -ppm * 1e-6 * k
        ax.plot(k / 1e3, dev, color=c, lw=1.0, label=f"{ppm:+d} ppm: 恢复相位")
        ax.plot(k / 1e3, ideal, color=c, ls="--", lw=0.8, alpha=0.7)
        track_err = dev - ideal
        te = track_err[2000:]
        # an early cycle slip leaves a harmless static integer-UI offset:
        # report the tracking jitter around the settled offset
        print(f"{name}: {ppm:+d} ppm 静态偏移 {np.mean(te):+.2f} UI, "
              f"去趋势跟踪抖动 RMS = {np.std(te):.4f} UI")
    ax.set(xlabel="symbol [k]", ylabel="累计相位偏差 [UI]",
           title=f"{name}: 频偏跟踪(虚线=输入斜坡,重合=积分支路锁住频差)")
    ax.legend(fontsize=7)

    # --- (c) lock detection on the pull-in case ---
    ax = axes[row, 2]
    dec, ys, ph, w, pd, wh = run_cdr(cfg, rx, peak + 0.4 * osr, w_dfe, levels)
    ma, lock_idx = lock_metric(ph, osr, peak)
    ax.plot(np.arange(min(ma.size, 3000)), ma[:3000], lw=0.8,
            label="相位滑窗峰峰值 [UI] (win=200)")
    ax.axhspan(0, 0.2, color="green", alpha=0.12, label="锁定判据 pp<0.2 UI")
    if lock_idx is not None:
        ax.axvline(lock_idx, color="green", ls="--",
                   label=f"判定锁定 @ {lock_idx} 符号")
        ref_sym = make_pattern(cfg)
        if lock_idx > 0:
            err_pre = float(np.mean(dec[:lock_idx] != ref_sym[:lock_idx]))
        else:
            err_pre = float("nan")
        err_post = float(np.mean(dec[lock_idx:] != ref_sym[lock_idx: dec.size]))
        print(f"{name}: lock @ {lock_idx} symbols; SER before/after lock = "
              f"{err_pre:.2e} / {err_post:.2e}")
    ax.set(xlabel="symbol", ylabel="相位峰峰值 [UI]",
           title=f"{name}: 锁定检测(相位平稳性判据,从 +0.4 UI 启动)")
    ax.legend(fontsize=7)

for ax in axes.flat:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "14_cdr_locking.png", dpi=130)
print(f"wrote {OUT / '14_cdr_locking.png'}")
