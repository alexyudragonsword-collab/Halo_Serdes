"""The receiver's own sampling clock, as per-symbol offsets the kernels add.

A receiver samples with a clock of its own -- a second PLL, with its own
phase-noise profile -- and the CDR tracks the *difference* between it and the
transmit clock. Phase 1 put a profile on the transmit edges (``tx/jitter.py``);
this module puts one on the sampling instants, in the same units the kernels
already use (samples), and the two kernels add it to the loop phase before
every sample they take (``cdr/kernels.py``, ``cdr/adc_kernel.py``).

Draw order matters for the engine's reproducibility: a non-trivial receiver
clock draws its random numbers *after* every draw the engine made before this
module existed (transmit jitter, AWGN, comparator offsets, ADC noise), so a
configuration with the default ideal clock produces the same waveform, the
same decisions and the same phase track, bit for bit. An ideal clock draws
nothing at all and returns zeros.
"""

from __future__ import annotations

import numpy as np

from ..config.schema import ClockConfig, LinkConfig


def is_ideal(clock: ClockConfig) -> bool:
    """True when the clock adds nothing (the default: white, all zero)."""
    return clock.kind == "white" and clock.rj_ui == 0.0 and not (clock.sj_ui > 0 and clock.sj_freq > 0)


def rx_clock_offsets_s(n_symbols: int, cfg: LinkConfig,
                       rng: np.random.Generator) -> np.ndarray:
    """Sampling-instant error [s] per symbol: profile (if any) + white RJ + SJ.

    The same synthesis as the transmit side minus duty-cycle distortion,
    which is a property of edges, not of sampling instants.
    """
    clk = cfg.rx.clock
    ui = cfg.ui
    out = np.zeros(n_symbols)
    if is_ideal(clk):
        return out
    if clk.kind == "profile":
        from ..tx.clock import ClockProfile

        out += ClockProfile.load(clk.file, f0_hz=clk.f0_hz).edge_offsets_s(n_symbols, ui, rng)
    if clk.rj_ui > 0:
        out += rng.normal(scale=clk.rj_ui * ui, size=n_symbols)
    if clk.sj_ui > 0 and clk.sj_freq > 0:
        k = np.arange(n_symbols)
        out += clk.sj_ui * ui * np.sin(2 * np.pi * clk.sj_freq * k * ui)
    return out


def rx_clock_offsets_samples(n_symbols: int, cfg: LinkConfig,
                             rng: np.random.Generator) -> np.ndarray:
    """What the kernels take: :func:`rx_clock_offsets_s` in samples (float64)."""
    return np.ascontiguousarray(rx_clock_offsets_s(n_symbols, cfg, rng) / cfg.dt, dtype=np.float64)
