"""How much reach would a fourth partial-response cursor buy? (ROADMAP #8)

Example 38 measures, on its 224 Gb/s PAM4 long-reach link, the reach to the
KP4 threshold of the delta target (31.3 dB), 1 + aD (36.3 dB) and
1 + aD + bD^2 (38.6 dB). The engine stops at three cursors. A fourth grows
the trellis by N again: PAM4 with memory 2 behind the target goes from 256
to 1024 states. The kernel's per-symbol decision, the bit-true datapath and
the statistical engine's decision model would also each need a third
controlled cursor. This script estimates the gain before anyone pays for it.

Method: a baud-rate Monte Carlo on example 38's slicer-referred cursors,
i.e. channel + Tx FIR + CTLE as the ADC receiver sees them. Each run uses
the jointly MMSE (FFE, monic target) pair for K target cursors, and the
library's Viterbi over the FFE's actual output cursors: the K target ones
plus ``mem`` residual ones (example 38 runs memory 2).

There is no CDR, no time-interleaved ADC lanes and no adaptation. What they
cost is folded into one noise scale, fitted so the delta target lands on
example 38's measured 31.3 dB. The model's 2- and 3-cursor gains are then
held against the measured +5.0 / +7.3 dB before its fourth-cursor gain is
read off, because a model that over-predicts the first two will
over-predict the third too.

    python tools/pr_target_length.py            # 400k symbols, ~8 min on 4 cores
    python tools/pr_target_length.py --quick    # 100k symbols

The numbers and the decision are in cairn/DSP发端与PR.md §12.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.channel import ChannelModel  # noqa: E402
from halo_serdes.channel.response import pulse_from_impulse  # noqa: E402
from halo_serdes.config import LinkConfig, PrConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig,
    MlsdConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.waveform import Waveform  # noqa: E402
from halo_serdes.dsp import channel_cursors  # noqa: E402
from halo_serdes.dsp.ffe import _conv_matrix, mmse_ffe  # noqa: E402
from halo_serdes.dsp.mlsd import viterbi_mlsd  # noqa: E402
from halo_serdes.engine.optical_stage import apply_front_end  # noqa: E402
from halo_serdes.engine.statistical import adc_noise_sigma  # noqa: E402
from halo_serdes.fec import pre_to_post_fec_ber  # noqa: E402

LEVELS = np.array([-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0])
SYMBOL_POWER = float(np.mean(LEVELS ** 2))         # 5/9
N_PRE, N_POST = 6, 14                               # example 38's FFE
LENGTHS = np.linspace(0.16, 0.36, 21)               # 24 .. 55 dB at 56 GHz
#: example 38's measured reach [dB] by target length (memory-2 Viterbi)
MEASURED = {1: 31.33, 2: 36.33, 3: 38.60}
#: (target cursors, residual memory): example 38's three, then a fourth cursor
#: at the same trellis size as three (memory 1) and at four times it (memory 2)
CONFIGS = ((1, 2), (2, 2), (3, 2), (4, 1), (4, 2))


def make_cfg(length_m: float) -> LinkConfig:
    """Example 38's link (examples/38_pr_three_cursor.py ``make_cfg``)."""
    return LinkConfig(
        modulation="pam4", symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0,
                              r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1,
                    clock=ClockConfig(rj_ui=0.004)),
        rx=RxConfig(arch="adc_dsp",
                    ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=N_PRE, n_post=N_POST, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    mlsd=MlsdConfig(kind="viterbi", memory=2),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=1000, seed=3, pattern="prbs13q"),
        pr=PrConfig(),
    )


def kp4_threshold(target: float = 1e-15) -> float:
    lo, hi = -8.0, -2.0                             # log10 pre-FEC BER
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if pre_to_post_fec_ber(10 ** mid, "kp4") > target:
            hi = mid
        else:
            lo = mid
    return 10 ** lo


def slicer_cursors(length_m: float):
    """(cursors normalised to a unit main, precursor count, noise sd in the
    same units, insertion loss at Nyquist [dB]) for one channel length."""
    cfg = make_cfg(length_m)
    cm = ChannelModel.from_config(cfg)
    h = apply_front_end(cfg, cm.response_set(cfg.dt).h.y)
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), cfg.osr)
    c_pre = N_PRE + 4
    c = channel_cursors(pulse, cfg.osr, c_pre, N_POST + 12,
                        peak_idx=int(np.argmax(np.abs(pulse.y))))
    peak = float(np.max(np.abs(c)))
    noise = float(np.hypot(cfg.rx.noise_rms, adc_noise_sigma(cfg)))
    return c / peak, c_pre, noise / peak, float(-cm.loss_at(56e9))


def monic_target(cursors, c_pre: int, noise_var: float, k: int) -> np.ndarray:
    """The monic k-cursor target with the least MMSE-FFE residual.

    The same quadratic form as ``dsp.ffe.mmse_pr_target`` (Q_tt t = -Q_t0),
    without its clip: the clip ranges there are only defined for up to three
    cursors."""
    n_taps = N_PRE + 1 + N_POST
    M = _conv_matrix(np.asarray(cursors, float), n_taps)
    A = SYMBOL_POWER * (M.T @ M) + noise_var * np.eye(n_taps)
    m0 = c_pre + N_PRE
    rows = M[m0:m0 + k, :]
    q = SYMBOL_POWER * np.eye(k) - SYMBOL_POWER ** 2 * (rows @ np.linalg.solve(A, rows.T))
    if k == 1:
        return np.ones(1)
    return np.concatenate([[1.0], np.linalg.solve(q[1:, 1:], -q[1:, 0])])


def viterbi_chunked(z: np.ndarray, taps: np.ndarray, chunk: int = 40_000,
                    pad: int = 150) -> np.ndarray:
    """``viterbi_mlsd`` in overlapping windows: its traceback table is
    n x states int64, 3 GB for 400k symbols at 1024 states. A window starts
    with every state equally likely, which costs nothing once the paths
    merge, well inside ``pad`` symbols on these targets."""
    out = np.empty(z.size, dtype=np.int64)
    for s in range(0, z.size, chunk):
        e = min(z.size, s + chunk)
        a, b = max(0, s - pad), min(z.size, e + pad)
        out[s:e] = viterbi_mlsd(z[a:b], LEVELS, taps)[s - a: e - a]
    return out


def link_ber(link, k: int, mem: int, scale: float, n_sym: int, seed: int) -> float:
    """Pre-FEC BER of one (target, memory) on one channel; Gray coding, one
    bit per symbol error. The symbols and the noise depend on the seed alone,
    so every configuration sees the same ones."""
    c, c_pre, noise, _ = link
    s2 = (noise * scale) ** 2
    t = monic_target(c, c_pre, s2, k)
    n_taps = N_PRE + 1 + N_POST
    w = mmse_ffe(c, c_pre, n_taps, N_PRE, noise_var=s2 / SYMBOL_POWER,
                 normalize=False, target=t)
    m0 = c_pre + N_PRE
    # the trellis takes the cursors the FFE actually made, as the engine does
    taps = np.convolve(c, w)[m0:m0 + k + mem]
    rng = np.random.default_rng(seed)
    sym = rng.integers(0, LEVELS.size, size=n_sym)
    y = np.convolve(LEVELS[sym], c)[:n_sym] + np.sqrt(s2) * rng.normal(size=n_sym)
    z = np.convolve(y, w)[m0:m0 + n_sym]
    dec = viterbi_chunked(z, taps)
    edge = 200
    ser = float(np.mean(dec[edge:-edge] != sym[edge:-edge]))
    return max(ser / 2.0, 0.5 / n_sym)


def reach(links, k: int, mem: int, scale: float, n_sym: int, seed: int,
          threshold: float) -> float:
    """Loss [dB] at which the BER crosses ``threshold`` (log-linear between
    grid points); walks up the grid and stops at the crossing."""
    lt = np.log10(threshold)
    prev = None
    for i, link in enumerate(links):
        lb = np.log10(link_ber(link, k, mem, scale, n_sym, seed))
        if prev is not None and prev <= lt < lb:
            l0, l1 = links[i - 1][3], link[3]
            return l0 + (lt - prev) / (lb - prev) * (l1 - l0)
        prev = lb
    return float("nan")


def fit_scale(links, n_sym: int, seed: int, threshold: float) -> float:
    """The noise scale that puts the delta target's reach on the measured one."""
    from scipy.optimize import brentq

    return brentq(lambda s: reach(links, 1, 2, s, n_sym, seed, threshold) - MEASURED[1],
                  1.0, 3.0, xtol=1e-3)


def _reach_job(args):
    return reach(*args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="100k symbols instead of 400k")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    n_sym = 100_000 if a.quick else 400_000
    t0 = time.time()
    threshold = kp4_threshold()
    links = [slicer_cursors(L) for L in LENGTHS]
    scale = fit_scale(links, n_sym, a.seed, threshold)
    print(f"example 38's link, {n_sym} symbols, seed {a.seed}: noise scale {scale:.3f} "
          f"puts the delta target on {MEASURED[1]} dB")
    jobs = [(links, k, mem, scale, n_sym, a.seed, threshold) for k, mem in CONFIGS]
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        r = dict(zip(CONFIGS, ex.map(_reach_job, jobs)))
    base = r[(1, 2)]
    print(f"{'target cursors':>15} {'memory':>7} {'states':>7} {'reach':>8} {'gain':>7}   measured")
    for (k, mem), v in r.items():
        meas = MEASURED.get(k) if mem == 2 else None
        print(f"{k:>15} {mem:>7} {4 ** (k - 1 + mem):>7} {v:7.2f}  {v - base:+6.2f}"
              + (f"   {meas} ({meas - MEASURED[1]:+.1f})" if meas else ""))
    print(f"[{time.time() - t0:.0f}s]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
