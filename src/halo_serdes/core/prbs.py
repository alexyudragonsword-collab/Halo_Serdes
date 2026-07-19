"""PRBS / PRQS pattern generation and self-synchronizing error checking.

Polynomials follow industry conventions (matching serdespy ``prs.py`` and
IEEE 802.3):

    PRBS7:  x^7 + x^6 + 1          PRBS15: x^15 + x^14 + 1
    PRBS9:  x^9 + x^5 + 1          PRBS20: x^20 + x^3 + 1
    PRBS13: x^13 + x^12 + x^2 + x + 1 (802.3 PRBS13Q base)
    PRBS23: x^23 + x^18 + 1        PRBS31: x^31 + x^28 + 1

PAM4 "Q" patterns (PRBS13Q/PRBS31Q) are formed by Gray-mapping consecutive
bit pairs of the underlying PRBS, per IEEE 802.3 test-pattern practice.
PRQS10 follows serdespy: PRBS20 and its (2^20-1)/3 cyclic shift, Gray-combined.

The bit-generation inner loop is JIT-compiled with numba when available;
set ``HALO_NO_JIT=1`` to force the pure-Python fallback (CI runs both).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from .mapping import gray_encode_bits

_TAPS: dict[int, tuple[int, ...]] = {
    7: (7, 6),
    9: (9, 5),
    13: (13, 12, 2, 1),
    15: (15, 14),
    20: (20, 3),
    23: (23, 18),
    31: (31, 28),
}


def _fb_positions(order: int) -> np.ndarray:
    """Feedback bit positions for the right-shift Fibonacci LFSR.

    With state bit j holding output x[k+j], inserting feedback at the MSB
    realizes x[k+n] = XOR_j x[k+j] over positions j. For polynomial
    x^n + x^a + ... + 1 the positions are {0} plus the non-leading exponents.
    """
    return np.asarray([0] + [t for t in _TAPS[order] if t != order], dtype=np.int64)


def _lfsr_py(order: int, fb_pos: np.ndarray, seed: int, length: int) -> np.ndarray:
    out = np.empty(length, dtype=np.int8)
    state = seed
    mask = (1 << order) - 1
    for i in range(length):
        fb = 0
        for p in fb_pos:
            fb ^= (state >> p) & 1
        out[i] = state & 1
        state = ((state >> 1) | (fb << (order - 1))) & mask
    return out


_lfsr = _lfsr_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _lfsr = numba.njit(cache=True)(_lfsr_py)
    except ImportError:
        pass


def prbs_bits(order: int, length: int | None = None, seed: int | None = None) -> np.ndarray:
    """Generate a PRBS bit sequence (int8 array of 0/1).

    Default ``length`` is one full period (2^order - 1); the sequence repeats
    cyclically beyond that. ``seed`` must be a nonzero ``order``-bit state.
    """
    if order not in _TAPS:
        raise ValueError(f"unsupported PRBS order {order}; supported: {sorted(_TAPS)}")
    if length is None:
        length = (1 << order) - 1
    if seed is None:
        seed = (1 << order) - 1
    if not (0 < seed < (1 << order)):
        raise ValueError(f"seed must be a nonzero {order}-bit value")
    return _lfsr(order, _fb_positions(order), seed, length)


def prbs_q_symbols(order: int, n_symbols: int | None = None, seed: int | None = None) -> np.ndarray:
    """PAM4 symbol indices (0..3) from Gray-mapped PRBS bit pairs.

    ``prbs_q_symbols(13)`` yields the PRBS13Q-style pattern (period 2^13 - 1
    symbols, since the odd-length bit period is walked twice); likewise for
    PRBS31Q.
    """
    if n_symbols is None:
        n_symbols = (1 << order) - 1
    bits = prbs_bits(order, 2 * n_symbols, seed)
    return gray_encode_bits(bits[0::2], bits[1::2])


def prqs10(n_symbols: int | None = None) -> np.ndarray:
    """PRQS10 PAM4 symbol sequence (serdespy construction).

    PRBS20 stream A and its (2^20-1)/3 cyclic shift as stream B, Gray-combined
    bitwise into PAM4 symbols. Period: 2^20 - 1 symbols.
    """
    period = (1 << 20) - 1
    if n_symbols is None:
        n_symbols = period
    a = prbs_bits(20, period)
    shift = period // 3
    b = np.roll(a, -shift)
    syms = gray_encode_bits(a, b)
    if n_symbols <= period:
        return syms[:n_symbols]
    reps = int(np.ceil(n_symbols / period))
    return np.tile(syms, reps)[:n_symbols]


@dataclass
class BerResult:
    n_checked: int
    n_errors: int
    error_idx: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))

    @property
    def ber(self) -> float:
        return self.n_errors / self.n_checked if self.n_checked else float("nan")


def prbs_checker(order: int, rx_bits: np.ndarray, sync_bits: int | None = None) -> BerResult:
    """Self-synchronizing PRBS bit checker.

    Seeds a reference LFSR from the first ``order`` received bits and
    regenerates the expected remainder (O(N), vs serdespy's O(N*n) sliding
    search). Bits used for seeding are not checked, so errors within the first
    ``order`` bits shift the reference; callers should discard link start-up
    before checking (standard BERT practice).
    """
    rx = np.asarray(rx_bits, dtype=np.int8)
    if sync_bits is None:
        sync_bits = order
    if rx.size <= sync_bits:
        raise ValueError("received sequence shorter than sync length")
    # Reconstruct LFSR state from the first `order` output bits.
    # Output bit i is state bit 0 at step i; state shifts right with feedback
    # entering at MSB, so bits [b0..b_{n-1}] ARE the initial state LSB-first.
    state = 0
    for i in range(order):
        state |= int(rx[i]) << i
    if state == 0:
        raise ValueError("cannot sync: first bits reconstruct all-zero LFSR state")
    ref = _lfsr(order, _fb_positions(order), state, rx.size)
    errs = np.nonzero(ref[order:] != rx[order:])[0] + order
    return BerResult(n_checked=rx.size - order, n_errors=errs.size, error_idx=errs)


def symbol_checker(ref_symbols: np.ndarray, rx_symbols: np.ndarray,
                   gray: bool = True) -> BerResult:
    """Compare PAM4 symbol streams; counts bit errors (Gray: adjacent-level
    error = 1 bit, 2-level jump = 2 bits, matching serdespy prqs_checker)."""
    ref = np.asarray(ref_symbols, dtype=np.int64)
    rx = np.asarray(rx_symbols, dtype=np.int64)
    n = min(ref.size, rx.size)
    ref, rx = ref[:n], rx[:n]
    if gray:
        # Gray-coded level index: bit errors = hamming distance of gray codes,
        # equivalently |level difference| capped at 2 for the standard map.
        diff = np.abs(ref - rx)
        n_bit_errs = int(np.sum(np.minimum(diff, 2)))
    else:
        n_bit_errs = int(np.sum(ref != rx) * 2)
    errs = np.nonzero(ref != rx)[0]
    return BerResult(n_checked=2 * n, n_errors=n_bit_errs, error_idx=errs)
