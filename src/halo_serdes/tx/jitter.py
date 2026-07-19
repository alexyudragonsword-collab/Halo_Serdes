"""Tx jitter injection: RJ / SJ / DCD via sub-sample edge placement.

Each symbol boundary k (between symbol k-1 and k) is moved by

    dt_k = rj[k] + A_sj * sin(2*pi*f_sj * k*UI + phi) + dcd_shift(edge polarity)

and the oversampled waveform is rebuilt with *fractional* (area-conserving)
edges: samples fully inside a symbol take that symbol's value, and the one
sample straddling a boundary takes the length-weighted average of the two
adjacent values. This preserves jitter to sub-sample accuracy (replacing
serdespy's whole-sample edge shifting, which quantizes jitter to the grid).
"""

from __future__ import annotations

import numpy as np

from ..config.schema import LinkConfig
from ..core.waveform import Waveform


def edge_jitter_seq(n_symbols: int, cfg: LinkConfig,
                    rng: np.random.Generator,
                    v_baud: np.ndarray | None = None) -> np.ndarray:
    """Per-boundary time offsets [s], length n_symbols+1 (boundary k starts
    symbol k). DCD sign follows edge polarity (rising +, falling -)."""
    ui = cfg.ui
    tx = cfg.tx
    jit = np.zeros(n_symbols + 1)
    if tx.rj_ui > 0:
        jit += rng.normal(scale=tx.rj_ui * ui, size=n_symbols + 1)
    if tx.sj_ui > 0 and tx.sj_freq > 0:
        k = np.arange(n_symbols + 1)
        jit += tx.sj_ui * ui * np.sin(2 * np.pi * tx.sj_freq * k * ui)
    if tx.dcd_ui > 0 and v_baud is not None:
        prev = np.concatenate([[v_baud[0]], v_baud[:-1]])
        rising = v_baud > prev
        falling = v_baud < prev
        shift = np.zeros(n_symbols)
        shift[rising[:n_symbols]] = +tx.dcd_ui * ui / 2
        shift[falling[:n_symbols]] = -tx.dcd_ui * ui / 2
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
    y = np.repeat(v_baud, osr).astype(np.float64)  # base fill (tail/edges)
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
    """Voltages -> jittered oversampled Tx waveform. Returns (wave, jitter_s)."""
    n_sym = v_baud.size
    jit = edge_jitter_seq(n_sym, cfg, rng, v_baud)
    y = jittered_zoh(v_baud, cfg.osr, jit, cfg.ui)
    wave = Waveform(y, cfg.dt)
    if cfg.tx.bw is not None:
        from .builder import apply_single_pole

        wave = apply_single_pole(wave, cfg.tx.bw)
    return wave, jit
