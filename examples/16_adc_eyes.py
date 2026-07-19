"""Observable eyes in the ADC-based architecture, 112G and 224G PAM4.

The subtlety: after the ADC the signal is purely digital baud-rate samples —
there is no continuous "eye" in the DSP domain, only a slicer sample cloud.
Two eyes ARE observable/meaningful:

1. AFE eye (CTLE output = ADC input): the real analog waveform a scope would
   see at the ADC front end. At 224G it is largely closed — that is the whole
   point of ADC/DSP: the eye is closed in the analog domain and reopened
   digitally;
2. reconstructed post-FFE eye: the baud-rate FFE taps upsampled onto the
   oversampled grid and convolved with the AFE waveform — the continuous-time
   *equivalent* of the digital FFE output (the DSP itself only evaluates it at
   baud instants; this shows what that equalized signal looks like between
   samples).

Third column: the actual post-DSP reality — the slicer sample cloud at the
sampling instant (a 1-UI histogram strip), which is all the DSP truly sees.
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
from halo_serdes.analysis.eye import plot_eye  # noqa: E402
from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.channel.response import pulse_from_impulse  # noqa: E402
from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.waveform import Waveform  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.engine.static_link import fold_eye, make_pattern  # noqa: E402
from halo_serdes.tx.builder import symbols_to_voltages, tx_fir  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

TRACE = ChannelConfig(kind="analytic", length_m=0.12, rdc=5.0,
                      r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192)


def make_cfg(rate_gbps: float, n_sym: int = 200_000) -> LinkConfig:
    return LinkConfig(
        modulation="pam4", symbol_rate=rate_gbps * 1e9 / 2, osr=32,
        channel=TRACE,
        tx=TxConfig(swing=1.0, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=4.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                    dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13q"),
    )


fig, axes = plt.subplots(2, 3, figsize=(15.5, 8))
for row, rate in enumerate((112.0, 224.0)):
    cfg = make_cfg(rate)
    channel = ChannelModel.from_config(cfg)
    res = run_time_link(cfg, channel=channel)
    osr = cfg.osr

    # rebuild the AFE (CTLE-output) oversampled waveform
    rng = np.random.default_rng(cfg.sim.seed)
    symbols = make_pattern(cfg)
    v = symbols_to_voltages(symbols, cfg)
    if len(cfg.tx.fir_taps) > 1:
        v = tx_fir(v, cfg.tx.fir_taps, cfg.tx.fir_n_pre)
    y_tx = np.repeat(v, osr)
    h = channel.response_set(cfg.dt).h.y
    ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
    nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
    f = np.fft.rfftfreq(nfft, d=cfg.dt)
    h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    n = y_tx.size
    afe = np.fft.irfft(np.fft.rfft(y_tx) * np.fft.rfft(h, n), n)
    afe += rng.normal(scale=cfg.rx.noise_rms, size=n)

    pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    phase = peak % osr

    # reconstructed post-FFE waveform (upsampled baud taps)
    w = res.ffe_taps
    tap_pre = cfg.rx.ffe.n_pre
    w_up = np.zeros((w.size - 1) * osr + 1)
    w_up[::osr] = w
    post_ffe = np.convolve(afe, w_up)[tap_pre * osr: tap_pre * osr + afe.size]

    loss = channel.loss_at(cfg.f_nyquist)
    print(f"{rate:.0f} Gb/s ({cfg.f_nyquist / 1e9:.0f} GHz Nyq, {loss:.1f} dB): "
          f"{res.summary()}")

    # panel 1: AFE eye (analog, ADC input)
    plot_eye(axes[row, 0], fold_eye(afe, osr, phase, n_traces=3000),
             cfg.ui * 1e12, title=f"{rate:.0f} Gb/s: AFE eye (ADC input, analog)")
    # panel 2: reconstructed post-FFE eye (digital-EQ equivalent)
    plot_eye(axes[row, 1], fold_eye(post_ffe, osr, phase, n_traces=3000),
             cfg.ui * 1e12, title=f"{rate:.0f} Gb/s: reconstructed post-FFE eye")
    # panel 3: the real post-DSP view — slicer sample cloud (1-UI strip)
    ax = axes[row, 2]
    y_sl = res.y_slicer
    ax.plot(np.zeros(y_sl.size) + np.random.uniform(-0.05, 0.05, y_sl.size),
            y_sl, ".", ms=0.6, alpha=0.15, color="C0")
    for lv in res.extras["levels"]:
        ax.axhline(lv, ls="--", c="r", alpha=0.5)
    ax.set(xlim=(-0.5, 0.5), xlabel="sampling instant [UI]", ylabel="V",
           title=f"{rate:.0f} Gb/s: slicer samples (what the DSP truly sees)")

for ax in axes.flat:
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "16_adc_eyes.png", dpi=130)
print(f"wrote {OUT / '16_adc_eyes.png'}")
