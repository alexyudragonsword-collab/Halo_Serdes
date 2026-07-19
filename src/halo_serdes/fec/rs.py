"""Reed-Solomon FEC: IEEE 802.3 KP4 / KR4 over GF(2^10).

- KP4 = RS(544, 514), t = 15 (100G/200G/400G-KP lanes);
- KR4 = RS(528, 514), t = 7.

Implemented as shortened codes of the GF(2^10) mother code RS(1023, .) via
the ``galois`` library (vectorized), with the serdespy bit<->10-bit-symbol
packing pipeline. Also provides pre/post-FEC BER conversion from symbol-error
statistics (random-error assumption) so post-FEC projections don't require
running the codec.
"""

from __future__ import annotations

import numpy as np

try:
    import galois

    _GF = galois.GF(2 ** 10)
except ImportError:  # pragma: no cover
    galois = None

from scipy.stats import binom

M_BITS = 10  # GF(2^10) symbol width


class RsCode:
    def __init__(self, n: int, k: int) -> None:
        if galois is None:
            raise ImportError("RS codec requires the 'galois' package (pip install galois)")
        self.n, self.k = n, k
        self.t = (n - k) // 2
        # shortened code: use the full-length mother code and zero-pad
        self._rs = galois.ReedSolomon(1023, 1023 - (n - k))

    def encode(self, msg_symbols: np.ndarray) -> np.ndarray:
        """k 10-bit symbols -> n coded symbols (systematic)."""
        msg = np.asarray(msg_symbols, dtype=np.int64)
        if msg.size != self.k:
            raise ValueError(f"message must be {self.k} symbols")
        full_k = 1023 - 2 * self.t
        buf = np.zeros(full_k, dtype=np.int64)
        buf[full_k - self.k:] = msg
        cw = self._rs.encode(_GF(buf))
        return np.asarray(cw[full_k - self.k:], dtype=np.int64)  # drop pad

    def decode(self, rx_symbols: np.ndarray) -> tuple[np.ndarray, int]:
        """n received symbols -> (k decoded symbols, n_corrected|-1)."""
        rx = np.asarray(rx_symbols, dtype=np.int64)
        if rx.size != self.n:
            raise ValueError(f"codeword must be {self.n} symbols")
        full_k = 1023 - 2 * self.t
        buf = np.zeros(1023, dtype=np.int64)
        buf[1023 - self.n:] = rx
        dec, n_err = self._rs.decode(_GF(buf), errors=True)
        msg = np.asarray(dec[full_k - self.k: full_k], dtype=np.int64)
        return msg, int(n_err)


def rs_kp4() -> RsCode:
    return RsCode(544, 514)


def rs_kr4() -> RsCode:
    return RsCode(528, 514)


# ---------------------------------------------------------------------------
# bit <-> symbol packing (serdespy rs_code.py pipeline)
# ---------------------------------------------------------------------------


def bits_to_gf_symbols(bits: np.ndarray) -> np.ndarray:
    """Pack a bit stream (multiple of 10) into 10-bit symbols, MSB first."""
    b = np.asarray(bits, dtype=np.int64)
    b = b[: (b.size // M_BITS) * M_BITS].reshape(-1, M_BITS)
    weights = 1 << np.arange(M_BITS - 1, -1, -1)
    return (b * weights).sum(axis=1)


def gf_symbols_to_bits(symbols: np.ndarray) -> np.ndarray:
    s = np.asarray(symbols, dtype=np.int64)
    out = np.zeros((s.size, M_BITS), dtype=np.int8)
    for i in range(M_BITS):
        out[:, i] = (s >> (M_BITS - 1 - i)) & 1
    return out.reshape(-1)


# ---------------------------------------------------------------------------
# pre/post-FEC BER conversion (random symbol errors)
# ---------------------------------------------------------------------------


def post_fec_frame_error_rate(ser_10b: float, n: int, t: int) -> float:
    """P(frame uncorrectable) = P(#symbol errors in n > t), binomial model."""
    return float(binom.sf(t, n, ser_10b))


def pre_to_post_fec_ber(pre_ber: float, code: str = "kp4",
                        bits_per_fec_symbol_error: float = 1.5) -> float:
    """Project post-FEC BER from pre-FEC BER (random-error assumption).

    pre-FEC bit errors -> 10-bit-symbol error rate (1 - (1-p)^10), binomial
    over the codeword; an uncorrectable frame is assumed to leave the
    erroneous symbols' bits wrong. Bursty error statistics (DFE error
    propagation!) violate the random assumption — pair with 1/(1+D)
    precoding, and validate with the real codec when it matters.
    """
    n, k, t = (544, 514, 15) if code == "kp4" else (528, 514, 7)
    ser = 1.0 - (1.0 - pre_ber) ** M_BITS
    p_frame = post_fec_frame_error_rate(ser, n, t)
    # expected wrong bits per failed frame ~ (t+1) symbols * avg wrong bits
    return float(p_frame * (t + 1) * bits_per_fec_symbol_error / (k * M_BITS))


def inner_decoded_ber(pre_ber: float, n: int, t: int) -> float:
    """Post-decode BER of a t-error-correcting (n, k) hard-decision block
    code on a BSC(pre_ber) — bounded-distance decoder, errors pass through
    only when more than t occur in a block (miscorrection ignored).

    ``p_out = sum_{j>t} (j/n) * C(n,j) p^j (1-p)^(n-j)``
    """
    from scipy.stats import binom

    if pre_ber <= 0:
        return 0.0
    j = np.arange(t + 1, n + 1)
    return float(np.sum(j / n * binom.pmf(j, n, pre_ber)))


def concatenated_post_fec_ber(pre_ber: float, inner_n: int, inner_t: int,
                              outer: str = "kp4") -> float:
    """Post-FEC BER of an inner (n, t) hard-decision block code concatenated
    with an RS outer (KP4/KR4).

    The inner code corrects most raw errors, presenting a much lower BER to
    the RS outer, which raises the tolerable pre-FEC BER (the standard deep-LR
    / 800G-1.6T concatenated-FEC approach). Total overhead = RS overhead +
    inner parity/data (report separately; a BCH(n,k) over GF(2^m) has
    ~m*t parity bits).
    """
    return pre_to_post_fec_ber(inner_decoded_ber(pre_ber, inner_n, inner_t), outer)
