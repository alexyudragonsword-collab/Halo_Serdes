"""Symbol/bit mapping: Gray coding, PAM4/NRZ levels, 1+D precoding hook.

Gray map (802.3 convention, matching serdespy):
    bits (b_msb, b_lsb) -> level index: 00->0, 01->1, 11->2, 10->3
    level indices map to voltages [-1, -1/3, +1/3, +1] * (swing/2), with RLM
    compression applied to the inner levels when rlm < 1.
"""

from __future__ import annotations

import numpy as np

# level_index = _GRAY_ENC[b1*2 + b0] with b1 = MSB (first bit), b0 = LSB.
_GRAY_ENC = np.array([0, 1, 3, 2], dtype=np.int8)
# inverse: level index -> (b_msb, b_lsb)
_GRAY_DEC_MSB = np.array([0, 0, 1, 1], dtype=np.int8)
_GRAY_DEC_LSB = np.array([0, 1, 1, 0], dtype=np.int8)


def gray_encode_bits(b_msb: np.ndarray, b_lsb: np.ndarray) -> np.ndarray:
    """Pairs of bits -> PAM4 level indices (0..3)."""
    idx = (np.asarray(b_msb, dtype=np.int8) << 1) | np.asarray(b_lsb, dtype=np.int8)
    return _GRAY_ENC[idx]


def gray_decode_symbols(symbols: np.ndarray) -> np.ndarray:
    """PAM4 level indices -> interleaved bit stream (msb, lsb, msb, lsb, ...)."""
    s = np.asarray(symbols, dtype=np.int64)
    bits = np.empty(2 * s.size, dtype=np.int8)
    bits[0::2] = _GRAY_DEC_MSB[s]
    bits[1::2] = _GRAY_DEC_LSB[s]
    return bits


def pam4_levels(swing: float = 2.0, rlm: float = 1.0) -> np.ndarray:
    """PAM4 voltage levels for level indices 0..3.

    ``swing`` is outer peak-to-peak; ``rlm`` (Ratio of Level Mismatch)
    compresses the inner levels: inner = +/- rlm * swing/6.
    """
    a = swing / 2.0
    return np.array([-a, -rlm * a / 3.0, rlm * a / 3.0, a])


def nrz_levels(swing: float = 2.0) -> np.ndarray:
    a = swing / 2.0
    return np.array([-a, a])


def bits_to_nrz_symbols(bits: np.ndarray) -> np.ndarray:
    """Bit stream -> NRZ level indices (identical, int8)."""
    return np.asarray(bits, dtype=np.int8)


def bits_to_pam4_symbols(bits: np.ndarray) -> np.ndarray:
    """Bit stream (even length) -> Gray-mapped PAM4 level indices."""
    b = np.asarray(bits, dtype=np.int8)
    if b.size % 2:
        b = b[:-1]
    return gray_encode_bits(b[0::2], b[1::2])


def precode_1plusd(symbols: np.ndarray, n_levels: int) -> np.ndarray:
    """1/(1+D) mod-N precoding (burst-error mitigation with DFE/MLSD).

    Placeholder for Phase 5: p[k] = (s[k] - p[k-1]) mod N.
    """
    s = np.asarray(symbols, dtype=np.int64)
    out = np.empty_like(s)
    prev = 0
    for k in range(s.size):
        prev = (s[k] - prev) % n_levels
        out[k] = prev
    return out


def unprecode_1plusd(symbols: np.ndarray, n_levels: int) -> np.ndarray:
    """Inverse of ``precode_1plusd``: s[k] = (p[k] + p[k-1]) mod N."""
    p = np.asarray(symbols, dtype=np.int64)
    prev = np.concatenate([[0], p[:-1]])
    return (p + prev) % n_levels
