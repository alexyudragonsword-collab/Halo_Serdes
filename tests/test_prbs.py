"""PRBS/PRQS generation and checker tests (closed-form properties)."""

import numpy as np
import pytest

from halo_serdes.core import prbs_bits, prbs_checker, prbs_q_symbols, prqs10, symbol_checker
from halo_serdes.core.mapping import gray_decode_symbols, gray_encode_bits


@pytest.mark.parametrize("order", [7, 9, 13, 15, 23])
def test_prbs_period_and_balance(order):
    period = (1 << order) - 1
    bits = prbs_bits(order, 2 * period)
    # exact periodicity
    assert np.array_equal(bits[:period], bits[period:2 * period])
    # ML-sequence balance: exactly 2^(n-1) ones per period
    assert int(bits[:period].sum()) == 1 << (order - 1)
    # no shorter period (check a few divisors)
    for div in (3, 7):
        sub = period // div
        if sub > 1 and period % div == 0:
            assert not np.array_equal(bits[:sub], bits[sub:2 * sub])


def test_prbs31_generates():
    bits = prbs_bits(31, 100_000)
    assert bits.size == 100_000
    assert 0.45 < bits.mean() < 0.55


def test_checker_clean_and_injected_errors():
    bits = prbs_bits(31, 50_000)
    res = prbs_checker(31, bits)
    assert res.n_errors == 0
    corrupted = bits.copy()
    err_pos = [1000, 20_000, 49_999]
    for p in err_pos:
        corrupted[p] ^= 1
    res = prbs_checker(31, corrupted)
    assert res.n_errors == len(err_pos)
    assert set(res.error_idx.tolist()) == set(err_pos)


def test_gray_roundtrip():
    rng = np.random.default_rng(0)
    b = rng.integers(0, 2, size=1000).astype(np.int8)
    syms = gray_encode_bits(b[0::2], b[1::2])
    bits_back = gray_decode_symbols(syms)
    assert np.array_equal(bits_back, b)
    # adjacent gray levels differ by exactly one bit
    volts_order = [0, 1, 2, 3]
    codes = [tuple(gray_decode_symbols(np.array([s]))) for s in volts_order]
    for a, c in zip(codes, codes[1:]):
        assert sum(x != y for x, y in zip(a, c)) == 1


def test_prbs13q_period():
    syms = prbs_q_symbols(13)
    assert syms.size == (1 << 13) - 1
    assert set(np.unique(syms)) <= {0, 1, 2, 3}
    # roughly uniform level occupancy
    counts = np.bincount(syms, minlength=4)
    assert counts.min() > 0.8 * counts.mean()


def test_prqs10_levels_and_length():
    syms = prqs10(100_000)
    assert syms.size == 100_000
    counts = np.bincount(syms, minlength=4)
    assert counts.min() > 0.8 * counts.mean()


def test_symbol_checker_gray_bit_weighting():
    ref = np.array([0, 1, 2, 3, 0])
    rx = np.array([0, 2, 2, 1, 0])  # one adjacent error (1 bit), one 2-jump (2 bits)
    res = symbol_checker(ref, rx, gray=True)
    assert res.n_errors == 1 + 2
    assert res.n_checked == 10
