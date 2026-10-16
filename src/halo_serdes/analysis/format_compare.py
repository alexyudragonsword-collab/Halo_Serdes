"""Comparing modulation formats at one net bit rate (examples/46).

The pure arithmetic of a format comparison: the baud a format needs for a
net rate under a FEC's overhead, the half-gap penalty of more levels, and
the three one-line rules the comparison's tables rest on -- which receiver
cell wins, how many DAC bits a format needs, how much transmitter noise it
tolerates. Everything measured (reach, power) comes from the engines; this
module only reads the numbers off.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

import numpy as np

from ..core.mapping import bits_per_symbol_of, n_levels_of

#: line rate over net rate: the RS(544, 514) KP4 code alone, and with the
#: BCH(255, 215) inner code of examples/20 in front of it
FEC_OVERHEAD = {"kp4": 544.0 / 514.0, "kp4+bch": 544.0 / 514.0 * 255.0 / 215.0}

T = TypeVar("T")


def baud_for(bit_rate: float, modulation: str, fec: str = "kp4") -> float:
    """Symbols per second that carry ``bit_rate`` net bits under ``fec``."""
    return float(bit_rate) * FEC_OVERHEAD[fec] / bits_per_symbol_of(modulation)


def half_gap_penalty_db(modulation: str) -> float:
    """How much closer the levels sit than PAM4's at the same swing, in dB:
    20 log10((N - 1) / 3) -- 4.4 dB for PAM6, 7.4 dB for PAM8, -9.5 for NRZ."""
    return float(20.0 * np.log10((n_levels_of(modulation) - 1) / 3.0))


def pick_receiver(cells: Sequence[T], reach: Callable[[T], float | None],
                  pj: Callable[[T], float], tol_db: float = 0.1) -> T | None:
    """The cell with the longest reach; within ``tol_db`` of it, the one
    that burns the least. Cells whose reach could not be placed (None) do
    not compete; None when none could."""
    placed = [c for c in cells if reach(c) is not None]
    if not placed:
        return None
    best = max(reach(c) for c in placed)
    tied = [c for c in placed if reach(c) >= best - tol_db]
    return min(tied, key=pj)


def dac_bits_needed(reach_by_bits: Mapping[int | None, float | None],
                    tol_db: float = 0.1) -> int | None:
    """The fewest DAC bits whose reach is within ``tol_db`` of the ideal
    DAC's (the ``None`` key); None when the ideal reach is unplaced or no
    bit count qualifies."""
    ideal = reach_by_bits.get(None)
    if ideal is None:
        return None
    ok = [b for b, r in reach_by_bits.items()
          if b is not None and r is not None and ideal - r <= tol_db]
    return min(ok) if ok else None


def noise_tolerance(reach_by_noise: Mapping[float, float | None],
                    drop_db: float = 0.5) -> float | None:
    """The largest transmitter noise whose reach is within ``drop_db`` of
    the noiseless one (key 0.0); None when the noiseless reach is unplaced
    or no noise level qualifies."""
    clean = reach_by_noise.get(0.0)
    if clean is None:
        return None
    ok = [n for n, r in reach_by_noise.items() if r is not None and clean - r <= drop_db]
    return max(ok) if ok else None
