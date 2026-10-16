"""Symbol/bit mapping: Gray coding, the level sets, 1+D precoding hook.

Gray map (802.3 convention, matching serdespy):
    bits (b_msb, b_lsb) -> level index: 00->0, 01->1, 11->2, 10->3
    level indices map to voltages [-1, -1/3, +1/3, +1] * (swing/2), with RLM
    compression applied to the inner levels when rlm < 1.

PAM8 is 3-bit reflected Gray per symbol on levels (2k - 7) / 7. PAM6 maps
5 bits onto a *pair* of symbols: 32 of the 36 pairs, the four corner pairs
(both symbols outer) left out (``PAM6_PAIR_WORDS``, the "corner32" map), so
a symbol is not a whole number of bits (2.5) and bit errors are counted per
pair (``count_bit_errors``). The outer symbols are sent less often
(``level_probabilities``). This module is the only place that knows the
alphabets; everything else asks it by ``modulation`` name.
"""

from __future__ import annotations

import numpy as np

# level_index = _GRAY_ENC[b1*2 + b0] with b1 = MSB (first bit), b0 = LSB.
_GRAY_ENC = np.array([0, 1, 3, 2], dtype=np.int8)
# inverse: level index -> (b_msb, b_lsb)
_GRAY_DEC_MSB = np.array([0, 0, 1, 1], dtype=np.int8)
_GRAY_DEC_LSB = np.array([0, 1, 1, 0], dtype=np.int8)
# bits that differ between two PAM4 level indices: the Hamming distance of
# their Gray codes. Not min(|difference|, 2): 0 (00) and 3 (10) differ in one
# bit, and that is the error 1/(1+D) mod 4 precoding makes when it wraps.
GRAY_BIT_DISTANCE = (_GRAY_DEC_MSB[:, None] != _GRAY_DEC_MSB[None, :]).astype(np.int64) \
    + (_GRAY_DEC_LSB[:, None] != _GRAY_DEC_LSB[None, :])


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


# ---------------------------------------------------------------- formats

FORMATS = ("nrz", "pam4", "pam6", "pam8")
_N_LEVELS = {"nrz": 2, "pam4": 4, "pam6": 6, "pam8": 8}
_BITS_PER_SYMBOL = {"nrz": 1, "pam4": 2, "pam6": 2.5, "pam8": 3}


def n_levels_of(modulation: str) -> int:
    return _N_LEVELS[modulation]


def bits_per_symbol_of(modulation: str) -> int | float:
    """Bits per symbol; an int except PAM6's 2.5 (five bits per pair)."""
    return _BITS_PER_SYMBOL[modulation]


def pam8_levels(swing: float = 2.0) -> np.ndarray:
    """PAM8 voltage levels for level indices 0..7: (2k - 7) / 7 * swing / 2."""
    a = swing / 2.0
    return a * (2.0 * np.arange(8) - 7.0) / 7.0


def pam6_levels(swing: float = 2.0) -> np.ndarray:
    """PAM6 voltage levels for level indices 0..5: (2k - 5) / 5 * swing / 2."""
    a = swing / 2.0
    return a * (2.0 * np.arange(6) - 5.0) / 5.0


def levels_for(modulation: str, swing: float = 2.0, rlm: float = 1.0) -> np.ndarray:
    """The driver's levels [V] for a modulation; ``rlm`` compresses PAM4's inner pair."""
    if modulation == "pam4":
        return pam4_levels(swing, rlm)
    if modulation == "pam6":
        return pam6_levels(swing)
    if modulation == "pam8":
        return pam8_levels(swing)
    return nrz_levels(swing)


# PAM8: 3-bit reflected Gray. _GRAY8_DEC[k] is the code of level index k
# (k ^ (k >> 1)); _GRAY8_ENC[code] the index it stands for.
_GRAY8_DEC = np.array([k ^ (k >> 1) for k in range(8)], dtype=np.int64)
_GRAY8_ENC = np.argsort(_GRAY8_DEC).astype(np.int64)

# PAM6 corner32: the four corner pairs (both symbols outer) are not sent.
_PAM6_CORNERS = ((0, 0), (0, 5), (5, 0), (5, 5))


def _derive_pam6_corner32() -> tuple[tuple[int, int], ...]:
    """The 32 pairs in word order: scanning the 6 x 6 grid row by row and
    skipping the corners, each pair takes the unused 5-bit word closest in
    summed Hamming distance to the grid neighbours already assigned (ties
    to the smallest word). ``PAM6_PAIR_WORDS`` is this, fixed; the test
    re-derives it."""
    pairs = [(u, v) for u in range(6) for v in range(6) if (u, v) not in _PAM6_CORNERS]
    code: dict[tuple[int, int], int] = {}
    free = list(range(32))
    for u, v in pairs:
        nbs = [code[p] for p in ((u - 1, v), (u, v - 1)) if p in code]
        best = min(free, key=lambda w: (sum(bin(w ^ c).count("1") for c in nbs), w))
        code[(u, v)] = best
        free.remove(best)
    out = [None] * 32
    for p, w in code.items():
        out[w] = p
    return tuple(out)


PAM6_PAIR_WORDS: tuple[tuple[int, int], ...] = _derive_pam6_corner32()


def _pam6_decode_table() -> np.ndarray:
    t = np.full((6, 6), -1, dtype=np.int64)
    for w, (u, v) in enumerate(PAM6_PAIR_WORDS):
        t[u, v] = w
    # a corner pair is never sent; decided, its second symbol is moved one
    # level inward and the pair looked up
    for u, v in _PAM6_CORNERS:
        t[u, v] = t[u, 1 if v == 0 else 4]
    return t


#: word of a received pair (u, v), the corners decoded inward
PAM6_DECODE: np.ndarray = _pam6_decode_table()
_PAM6_WORD_U = np.array([u for u, _ in PAM6_PAIR_WORDS], dtype=np.int64)
_PAM6_WORD_V = np.array([v for _, v in PAM6_PAIR_WORDS], dtype=np.int64)


def level_probabilities(modulation: str) -> np.ndarray:
    """How often each level index is sent on uniform data: equiprobable,
    except PAM6's outer symbols (1/8 each against 3/16 for the inner four)."""
    n = n_levels_of(modulation)
    if modulation != "pam6":
        return np.full(n, 1.0 / n)
    counts = np.bincount(_PAM6_WORD_U, minlength=6) + np.bincount(_PAM6_WORD_V, minlength=6)
    return counts / counts.sum()


def neighbour_conditional(modulation: str) -> np.ndarray | None:
    """``C[k, j]``: how often the other symbol of a PAM6 pair is level ``j``
    when this one is ``k`` -- None for the formats whose symbols are
    independent. The corner32 map never pairs an outer level with an outer
    one, so an outer symbol's partner is uniform over the inner four and an
    inner symbol's over all six; averaged over ``level_probabilities`` the
    rows give back the occupancy. Each symbol's partner is its right
    neighbour in the pair's first position and its left one in the second,
    equally often. The statistical engine conditions the +-1 cursors' ISI
    on it: taken as independent, the time engine's SER on the mixed-signal
    receiver read 1.6-2.8x below it (ROADMAP P2 #35)."""
    if modulation != "pam6":
        return None
    cond = np.zeros((6, 6))
    for k in range(6):
        cond[k] += np.bincount(_PAM6_WORD_V[_PAM6_WORD_U == k], minlength=6)
        cond[k] += np.bincount(_PAM6_WORD_U[_PAM6_WORD_V == k], minlength=6)
    return cond / cond.sum(axis=1, keepdims=True)


def bits_needed(n_symbols: int, modulation: str) -> int:
    """Bits that ``bits_to_symbols`` consumes to make at least ``n_symbols``."""
    if modulation == "pam6":
        return 5 * ((int(n_symbols) + 1) // 2)
    return int(n_symbols) * int(bits_per_symbol_of(modulation))


def _bits_to_words(bits: np.ndarray, width: int) -> np.ndarray:
    b = np.asarray(bits, dtype=np.int64)
    b = b[: b.size - b.size % width].reshape(-1, width)
    return b @ (1 << np.arange(width - 1, -1, -1))


def _words_to_bits(words: np.ndarray, width: int) -> np.ndarray:
    w = np.asarray(words, dtype=np.int64)[:, None]
    return ((w >> np.arange(width - 1, -1, -1)) & 1).astype(np.int8).ravel()


def bits_to_symbols(bits: np.ndarray, modulation: str) -> np.ndarray:
    """Bit stream -> level indices, MSB first within a word; a trailing
    partial word is dropped. PAM6 yields two symbols per 5 bits."""
    if modulation == "nrz":
        return bits_to_nrz_symbols(bits).astype(np.int64)
    if modulation == "pam4":
        return bits_to_pam4_symbols(bits).astype(np.int64)
    if modulation == "pam8":
        return _GRAY8_ENC[_bits_to_words(bits, 3)]
    w = _bits_to_words(bits, 5)
    return np.stack([_PAM6_WORD_U[w], _PAM6_WORD_V[w]], axis=1).ravel()


def symbols_to_bits(symbols: np.ndarray, modulation: str) -> np.ndarray:
    """Level indices -> bit stream (the inverse of ``bits_to_symbols``);
    PAM6 reads pairs from index 0, a trailing single symbol is dropped, a
    corner pair decodes inward."""
    s = np.asarray(symbols, dtype=np.int64)
    if modulation == "nrz":
        return s.astype(np.int8)
    if modulation == "pam4":
        return gray_decode_symbols(s)
    if modulation == "pam8":
        return _words_to_bits(_GRAY8_DEC[s], 3)
    s = s[: s.size - s.size % 2].reshape(-1, 2)
    return _words_to_bits(PAM6_DECODE[s[:, 0], s[:, 1]], 5)


def _popcount(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.int64)
    out = np.zeros(x.shape, dtype=np.int64)
    while np.any(x):
        out += x & 1
        x = x >> 1
    return out


def bit_distance_matrix(modulation: str) -> np.ndarray:
    """``D[i, j]``: bits in error when level index ``i`` is sent and ``j``
    decided. Exact for NRZ, PAM4 (``GRAY_BIT_DISTANCE``) and PAM8; for PAM6
    the mean over the pair's other symbol and over which of the two it is,
    the corners decoded inward -- a per-symbol stand-in for a per-pair count."""
    if modulation == "nrz":
        return np.array([[0, 1], [1, 0]], dtype=np.float64)
    if modulation == "pam4":
        return GRAY_BIT_DISTANCE.astype(np.float64)
    if modulation == "pam8":
        return _popcount(_GRAY8_DEC[:, None] ^ _GRAY8_DEC[None, :]).astype(np.float64)
    d = np.zeros((6, 6))
    for i in range(6):
        for j in range(6):
            acc = []
            for first in (True, False):
                for k in range(6):
                    sent = (i, k) if first else (k, i)
                    if sent in _PAM6_CORNERS:
                        continue
                    got = (j, k) if first else (k, j)
                    acc.append(bin(int(PAM6_DECODE[sent]) ^ int(PAM6_DECODE[got])).count("1"))
            d[i, j] = float(np.mean(acc))
    return d


def count_bit_errors(ref_symbols: np.ndarray, rx_symbols: np.ndarray, modulation: str,
                     ref_pos: np.ndarray | None = None):
    """Bit errors between a reference and a decided symbol stream, as the
    format maps bits: ``BerResult(n_checked, n_errors, error_idx)`` with the
    symbol indices in error. A reference of -1 is unscored. PAM6 scores by
    pair: the pairs start at even transmitted indices (``ref_pos``, the
    stream's own positions when None), a pair with an unscored or missing
    partner is left out, so ``n_checked`` is five per scored pair. PAM4
    is ``symbol_checker(gray=True)`` to the bit."""
    from .prbs import BerResult, symbol_checker

    ref = np.asarray(ref_symbols, dtype=np.int64)
    rx = np.asarray(rx_symbols, dtype=np.int64)
    n = min(ref.size, rx.size)
    ref, rx = ref[:n], rx[:n]
    if modulation == "pam4":
        return symbol_checker(ref, rx, gray=True)
    if modulation != "pam6":
        ok = ref >= 0
        errs = np.flatnonzero(ok & (ref != rx))
        if modulation == "nrz":
            return BerResult(n_checked=int(ok.sum()), n_errors=int(errs.size), error_idx=errs)
        d = _popcount(_GRAY8_DEC[np.where(ok, ref, 0)] ^ _GRAY8_DEC[np.clip(rx, 0, 7)])
        return BerResult(n_checked=3 * int(ok.sum()), n_errors=int(d[ok].sum()), error_idx=errs)
    pos = np.arange(n) if ref_pos is None else np.asarray(ref_pos, dtype=np.int64)[:n]
    first = np.flatnonzero((pos % 2 == 0) & (np.arange(n) + 1 < n))
    first = first[pos[np.minimum(first + 1, n - 1)] == pos[first] + 1]
    ok = (ref[first] >= 0) & (ref[first + 1] >= 0)
    first = first[ok]
    sent = PAM6_DECODE[ref[first], ref[first + 1]]
    got = PAM6_DECODE[np.clip(rx[first], 0, 5), np.clip(rx[first + 1], 0, 5)]
    d = _popcount(sent ^ got)
    wrong = np.concatenate([first[ref[first] != rx[first]], first[ref[first + 1] != rx[first + 1]] + 1])
    return BerResult(n_checked=5 * int(first.size), n_errors=int(d.sum()),
                     error_idx=np.sort(wrong))
