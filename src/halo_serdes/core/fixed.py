"""Fixed-point framework: int64 + shift scaling with RTL-exact semantics.

Not "float snapped to a grid": values live as int64 integers scaled by
2^-fl, multiplies are integer multiplies, and rescaling is an arithmetic
right shift whose rounding matches RTL:

- ``rounding="floor"``: plain arithmetic shift (Verilog ``>>>``);
- ``rounding="round"``: round-half-up via +2^(s-1) before the shift
  (the standard RTL rounding adder).

Saturation clips to the two's-complement range of ``wl`` bits.
"""

from __future__ import annotations

import numpy as np

from ..config.schema import QFormat


def q_scale(q: QFormat) -> float:
    return float(2.0 ** (-q.fl))


def q_limits(q: QFormat) -> tuple[int, int]:
    if q.signed:
        return -(1 << (q.wl - 1)), (1 << (q.wl - 1)) - 1
    return 0, (1 << q.wl) - 1


def to_int(x: np.ndarray | float, q: QFormat) -> np.ndarray:
    """Quantize real values to int64 representation (round-to-nearest,
    saturating)."""
    v = np.round(np.asarray(x, dtype=np.float64) * 2.0 ** q.fl).astype(np.int64)
    lo, hi = q_limits(q)
    return np.clip(v, lo, hi)


def from_int(i: np.ndarray | int, q: QFormat) -> np.ndarray:
    return np.asarray(i, dtype=np.float64) * q_scale(q)


def rshift_round(v: np.ndarray | int, shift: int, rounding: str = "round") -> np.ndarray:
    """Arithmetic right shift with RTL-matching rounding (vector int64).

    floor: v >> shift (arithmetic; numpy int64 >> is arithmetic).
    round: (v + 2^(shift-1)) >> shift (round-half-up, the RTL rounding adder).
    """
    v = np.asarray(v, dtype=np.int64)
    if shift <= 0:
        return v << (-shift)
    if rounding == "round":
        v = v + (np.int64(1) << (shift - 1))
    return v >> shift


def saturate(v: np.ndarray | int, wl: int) -> np.ndarray:
    lo = -(1 << (wl - 1))
    hi = (1 << (wl - 1)) - 1
    return np.clip(np.asarray(v, dtype=np.int64), lo, hi)
