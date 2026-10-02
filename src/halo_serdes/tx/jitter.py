"""Tx jitter injection: RJ / SJ / DCD (and a PLL profile) via sub-sample edge placement.

Each symbol boundary k (between symbol k-1 and k) is moved by

    dt_k = profile[k] + rj[k] + A_sj * sin(2*pi*f_sj * k*UI + phi) + dcd_shift(edge polarity)

where ``profile[k]`` is present only for ``tx.clock.kind == "profile"``: the
coloured timing a PLL phase-noise profile synthesises (``tx/clock.py``). The
three spec-sheet terms are then *added on top*, so a profile clock can still
carry an extra SJ tone for a JTOL sweep.

The oversampled waveform is then rebuilt with *fractional* (area-conserving)
edges: samples fully inside a symbol take that symbol's value, and the one
sample straddling a boundary takes the length-weighted average of the two
adjacent values. This preserves jitter to sub-sample accuracy (replacing
serdespy's whole-sample edge shifting, which quantizes jitter to the grid).
"""

from __future__ import annotations

import numpy as np

from ..config.schema import LinkConfig
from ..core.sampler import hold
from ..core.waveform import Waveform
from .clock import ClockProfile


def edge_jitter_seq(n_symbols: int, cfg: LinkConfig,
                    rng: np.random.Generator,
                    v_baud: np.ndarray | None = None) -> np.ndarray:
    """Per-boundary time offsets [s], length n_symbols+1 (boundary k starts
    symbol k). DCD sign follows edge polarity (rising +, falling -)."""
    ui = cfg.ui
    clk = cfg.tx.clock
    jit = np.zeros(n_symbols + 1)
    # The profile draws its random numbers first and only when asked for, so
    # the white-kind draws below happen in the same order, from the same
    # generator state, as before profiles existed: kind="white" stays
    # byte-identical to the pre-profile engine, which tests/golden/ pins.
    if clk.kind == "profile":
        profile = ClockProfile.load(clk.file, f0_hz=clk.f0_hz)
        jit += profile.edge_offsets_s(n_symbols + 1, ui, rng)
    if clk.rj_ui > 0:
        jit += rng.normal(scale=clk.rj_ui * ui, size=n_symbols + 1)
    if clk.sj_ui > 0 and clk.sj_freq > 0:
        k = np.arange(n_symbols + 1)
        jit += clk.sj_ui * ui * np.sin(2 * np.pi * clk.sj_freq * k * ui)
    if clk.dcd_ui > 0 and v_baud is not None:
        prev = np.concatenate([[v_baud[0]], v_baud[:-1]])
        rising = v_baud > prev
        falling = v_baud < prev
        shift = np.zeros(n_symbols)
        shift[rising[:n_symbols]] = +clk.dcd_ui * ui / 2
        shift[falling[:n_symbols]] = -clk.dcd_ui * ui / 2
        jit[:n_symbols] += shift
    return jit


def jittered_zoh(v_baud: np.ndarray, osr: int, jitter_s: np.ndarray,
                 ui: float) -> np.ndarray:
    """Build the oversampled waveform with jittered, fractional edges.

    Boundary k sits at t = k*UI + jitter_s[k]; symbol k occupies
    [boundary_k, boundary_{k+1}). Samples fully inside a symbol take its
    value; the sample straddling a boundary takes the length-weighted mix.

    Implemented as a sequential interval fill between *actual* consecutive
    boundary positions, so cumulative timing offsets larger than one UI
    (e.g. ppm frequency-offset ramps) are handled exactly. (A previous
    implementation refilled regions from the nominal grid position, which
    silently clipped accumulated shifts beyond 1 UI in the slow direction.)
    """
    n_sym = v_baud.size
    dt = ui / osr
    n = n_sym * osr
    y = hold(v_baud, osr).astype(np.float64)  # base fill (tail/edges)
    b = (np.arange(n_sym + 1) * ui + jitter_s) / dt  # boundaries [samples]
    cur_start = b[0]
    for k in range(1, n_sym + 1):
        cur_end = b[k]
        va = v_baud[k - 1]
        # samples fully inside symbol k-1: [ceil(start), floor(end))
        i0 = max(int(np.ceil(cur_start)), 0)
        i1 = min(int(np.floor(cur_end)), n)
        if i1 > i0:
            y[i0:i1] = va
        # boundary-straddling sample: left portion va, right portion next
        if k < n_sym:
            j = int(np.floor(cur_end))
            if 0 <= j < n:
                frac = cur_end - j
                y[j] = va * frac + v_baud[k] * (1.0 - frac)
        cur_start = cur_end
    return y


def build_jittered_tx(v_baud: np.ndarray, cfg: LinkConfig,
                      rng: np.random.Generator) -> tuple[Waveform, np.ndarray]:
    """Voltages -> jittered oversampled Tx waveform. Returns (wave, jitter_s).
    Kept for callers of the old API; it is ``TxPipeline.waveform_and_edges``."""
    from .pipeline import TxPipeline

    return TxPipeline.from_config(cfg).waveform_and_edges(v_baud, rng)
