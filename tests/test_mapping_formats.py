"""PAM6 and PAM8 join NRZ and PAM4: alphabets, bit mappings, bit-error counting.

448G/lane format comparison (``examples/46_448g_formats.py``). The level
count and bits per symbol come from ``modulation`` alone (invariant 1); PAM8
is 3-bit Gray per symbol, PAM6 maps 5 bits onto a pair of symbols (32 of the
36 pairs, the four corner pairs left out). NRZ and PAM4 paths are untouched:
the fingerprint and every expected example output stay bit-identical.
"""

import dataclasses

import numpy as np
import pytest

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import RxConfig, SimConfig, TxConfig, ms_envelope
from halo_serdes.core import mapping as mp
from halo_serdes.core.prbs import symbol_checker
from halo_serdes.engine.static_link import _levels, make_pattern

FORMATS = ("nrz", "pam4", "pam6", "pam8")


# ------------------------------------------------------------------ config

def test_n_levels_and_bits_per_symbol():
    expect = {"nrz": (2, 1), "pam4": (4, 2), "pam6": (6, 2.5), "pam8": (8, 3)}
    for m, (n, b) in expect.items():
        cfg = LinkConfig(modulation=m)
        assert cfg.n_levels == n and cfg.bits_per_symbol == b
        assert isinstance(cfg.n_levels, int)
        if m != "pam6":
            assert isinstance(cfg.bits_per_symbol, int)
        assert cfg.data_rate == pytest.approx(cfg.symbol_rate * b)
        assert mp.n_levels_of(m) == n and mp.bits_per_symbol_of(m) == b


def test_pam5_rejected_pam6_pam8_accepted():
    with pytest.raises(ValueError, match="modulation"):
        LinkConfig(modulation="pam5")
    for m in ("pam6", "pam8"):
        assert LinkConfig(modulation=m).modulation == m


@pytest.mark.parametrize("mod, bad, good", [("pam6", 2, 3), ("pam8", 2, 3), ("pam4", 1, 2)])
def test_dac_bits_must_hold_the_alphabet(mod, bad, good):
    with pytest.raises(ValueError, match="dac_bits"):
        LinkConfig(modulation=mod, tx=TxConfig(dac_bits=bad))
    LinkConfig(modulation=mod, tx=TxConfig(dac_bits=good))


def test_pam6_map_is_validated_and_defaults_to_corner32():
    assert TxConfig().pam6_map == "corner32"
    with pytest.raises(ValueError, match="pam6_map"):
        TxConfig(pam6_map="x")


def test_mm_pd_input_auto_is_the_equalised_sample_above_two_levels():
    for m, exp in (("nrz", "adc"), ("pam4", "ffe"), ("pam6", "ffe"), ("pam8", "ffe")):
        assert LinkConfig(modulation=m, rx=RxConfig(arch="adc_dsp")).mm_pd_input == exp


def test_ms_envelope_is_known_for_all_four_formats():
    """PAM6 / PAM8 calibrated 2026-10-17 (ROADMAP P2 #34, examples/12); an
    unknown format is the only None."""
    assert ms_envelope("nrz") == (24e9, 30e9) and ms_envelope("pam4") == (32e9, 36e9)
    assert ms_envelope("pam6") == (30e9, 32.5e9) and ms_envelope("pam8") == (24e9, 33e9)
    assert ms_envelope("pam5") is None


# ------------------------------------------------------------------ levels

def test_levels_are_uniform_and_symmetric():
    assert mp.pam6_levels(2.0) == pytest.approx([-1, -0.6, -0.2, 0.2, 0.6, 1])
    assert mp.pam8_levels(2.0) == pytest.approx(np.arange(-7, 8, 2) / 7.0)
    for m in FORMATS:
        lv = mp.levels_for(m, 1.0)
        assert lv.size == mp.n_levels_of(m)
        assert np.all(np.diff(lv) > 0) and lv[0] == -0.5 and lv[-1] == 0.5
        assert np.allclose(np.diff(lv), np.diff(lv)[0])
        assert np.all(lv != 0.0)                       # the sign detectors need no level at 0
        assert np.array_equal(_levels(LinkConfig(modulation=m)), lv)
    assert np.array_equal(mp.levels_for("pam4", 1.0, 0.9), mp.pam4_levels(1.0, 0.9))
    assert np.array_equal(mp.levels_for("nrz", 1.0), mp.nrz_levels(1.0))


def test_level_probabilities():
    for m in ("nrz", "pam4", "pam8"):
        p = mp.level_probabilities(m)
        assert p.size == mp.n_levels_of(m) and np.allclose(p, 1.0 / p.size)
    p6 = mp.level_probabilities("pam6")
    assert p6 == pytest.approx([1 / 8, 3 / 16, 3 / 16, 3 / 16, 3 / 16, 1 / 8])


# ------------------------------------------------------------------ PAM8 Gray

def test_pam8_gray_adjacent_levels_differ_in_one_bit():
    d = mp.bit_distance_matrix("pam8")
    assert d.shape == (8, 8) and np.all(np.diag(d) == 0) and np.array_equal(d, d.T)
    assert np.all(np.diag(d, 1) == 1)
    bits = np.random.default_rng(1).integers(0, 2, 3 * 1000)
    sym = mp.bits_to_symbols(bits, "pam8")
    assert sym.min() >= 0 and sym.max() <= 7 and sym.size == 1000
    assert np.array_equal(mp.symbols_to_bits(sym, "pam8"), bits)
    assert np.array_equal(mp.bit_distance_matrix("pam4"), mp.GRAY_BIT_DISTANCE)
    assert np.array_equal(mp.bit_distance_matrix("nrz"), [[0, 1], [1, 0]])


# ------------------------------------------------------------------ PAM6 corner32

CORNERS = {(0, 0), (0, 5), (5, 0), (5, 5)}


def test_pam6_corner32_table():
    words = mp.PAM6_PAIR_WORDS
    assert len(words) == 32 and len(set(words)) == 32
    assert not (set(words) & CORNERS)
    assert all(0 <= u < 6 and 0 <= v < 6 for u, v in words)
    assert tuple(mp._derive_pam6_corner32()) == tuple(words)
    # the greedy rule keeps grid neighbours close in Hamming distance
    dist = []
    code = {pair: w for w, pair in enumerate(words)}
    for (u, v), w in code.items():
        for du, dv in ((1, 0), (0, 1)):
            nb = (u + du, v + dv)
            if nb in code:
                dist.append(bin(w ^ code[nb]).count("1"))
    assert np.mean(dist) < 1.6, np.mean(dist)


@pytest.mark.parametrize("bad, good", [((0, 0), (0, 1)), ((0, 5), (0, 4)), ((5, 0), (5, 1)), ((5, 5), (5, 4))])
def test_pam6_invalid_pair_decodes_with_the_second_symbol_moved_inward(bad, good):
    assert mp.PAM6_DECODE[bad] == mp.PAM6_DECODE[good]
    assert mp.PAM6_PAIR_WORDS[mp.PAM6_DECODE[good]] == good


def test_pam6_roundtrip_and_occupancy():
    bits = np.random.default_rng(2).integers(0, 2, 5 * 4000)
    sym = mp.bits_to_symbols(bits, "pam6")
    assert sym.size == 8000 and sym.min() >= 0 and sym.max() <= 5
    assert np.array_equal(mp.symbols_to_bits(sym, "pam6"), bits)
    assert mp.symbols_to_bits(sym[:-1], "pam6").size == bits.size - 5     # a trailing single symbol is dropped
    cfg = LinkConfig(modulation="pam6", sim=SimConfig(n_symbols=200_000, pattern="prbs13"))
    occ = np.bincount(make_pattern(cfg), minlength=6) / 200_000
    assert occ == pytest.approx(mp.level_probabilities("pam6"), abs=0.01)


def test_pam6_effective_bit_distance_matrix():
    b = mp.bit_distance_matrix("pam6")
    assert b.shape == (6, 6) and np.all(np.diag(b) == 0) and np.all(b >= 0)
    # one entry by hand: symbol 2 decided as 3, averaged over the partner and the position
    code = {pair: w for w, pair in enumerate(mp.PAM6_PAIR_WORDS)}
    acc = []
    for first in (True, False):
        ks = [k for k in range(6) if ((2, k) if first else (k, 2)) in code]
        for k in ks:
            sent = (2, k) if first else (k, 2)
            got = (3, k) if first else (k, 3)
            acc.append(bin(code[sent] ^ mp.PAM6_DECODE[got]).count("1"))
    assert b[2, 3] == pytest.approx(np.mean(acc))


# ------------------------------------------------------------------ counting

def test_count_bit_errors_pam6_counts_exact_pairs():
    code = {pair: w for w, pair in enumerate(mp.PAM6_PAIR_WORDS)}
    ref = np.array([1, 2, 3, 4, 2, 2, 1, 1, 4])      # four pairs and a trailing symbol
    rx = ref.copy()
    rx[0] = 2                                        # first symbol of pair 0
    rx[5] = 3                                        # second symbol of pair 2
    r = mp.count_bit_errors(ref, rx, "pam6")
    exp = bin(code[(1, 2)] ^ code[(2, 2)]).count("1") + bin(code[(2, 2)] ^ code[(2, 3)]).count("1")
    assert r.n_checked == 20 and r.n_errors == exp
    assert list(r.error_idx) == [0, 5]
    # pairs follow the transmitted index: shifted by one position, the pairs re-form
    r = mp.count_bit_errors(ref, rx, "pam6", ref_pos=np.arange(9) + 1)
    assert r.n_checked == 20
    exp = bin(code[(2, 3)] ^ code[(2, 3)]).count("1") + bin(code[(4, 2)] ^ code[(4, 3)]).count("1")
    assert r.n_errors == exp
    # a reference of -1 takes its pair out
    ref2 = ref.copy()
    ref2[1] = -1
    assert mp.count_bit_errors(ref2, rx, "pam6").n_checked == 15


def test_count_bit_errors_matches_the_checkers_on_nrz_pam4_pam8():
    rng = np.random.default_rng(3)
    ref = rng.integers(0, 4, 5000)
    rx = np.where(rng.random(5000) < 0.05, rng.integers(0, 4, 5000), ref)
    a, b = mp.count_bit_errors(ref, rx, "pam4"), symbol_checker(ref, rx, gray=True)
    assert (a.n_checked, a.n_errors) == (b.n_checked, b.n_errors) and np.array_equal(a.error_idx, b.error_idx)
    ref = rng.integers(0, 2, 1000)
    rx = ref ^ (rng.random(1000) < 0.1)
    r = mp.count_bit_errors(ref, rx, "nrz")
    assert r.n_checked == 1000 and r.n_errors == int(np.sum(ref != rx))
    ref = rng.integers(0, 8, 1000)
    rx = np.where(rng.random(1000) < 0.1, rng.integers(0, 8, 1000), ref)
    r = mp.count_bit_errors(ref, rx, "pam8")
    assert r.n_checked == 3000
    assert r.n_errors == int(np.sum(mp.symbols_to_bits(ref, "pam8") != mp.symbols_to_bits(rx, "pam8")))


# ------------------------------------------------------------------ patterns

@pytest.mark.parametrize("mod, n_lv", [("pam6", 6), ("pam8", 8)])
def test_make_pattern_for_the_new_formats(mod, n_lv):
    cfg = LinkConfig(modulation=mod, sim=SimConfig(n_symbols=30_000, pattern="prbs13"))
    s = make_pattern(cfg)
    assert s.size == 30_000 and s.min() == 0 and s.max() == n_lv - 1
    assert mp.bits_needed(30_000, mod) >= 30_000 * mp.bits_per_symbol_of(mod)
    for q in ("prbs13q", "prqs10"):
        with pytest.raises(ValueError, match="PAM4"):
            make_pattern(dataclasses.replace(cfg, sim=SimConfig(n_symbols=100, pattern=q)))


def test_pattern_period_for_the_new_formats():
    from halo_serdes.analysis import pattern_period

    assert pattern_period("prbs13", "pam6") == 2 * 8191
    assert pattern_period("prbs13", "pam8") == 8191
    assert pattern_period("prbs20", "pam8") == (2 ** 20 - 1) // 3
    assert pattern_period("prbs13", "pam4") == 8191 and pattern_period("prbs7", "nrz") == 127


def test_precode_mod_n_round_trips():
    for n in (6, 8):
        s = np.random.default_rng(n).integers(0, n, 500)
        assert np.array_equal(mp.unprecode_1plusd(mp.precode_1plusd(s, n), n), s)


def test_pam6_partner_conditional_matches_the_table_and_the_occupancy():
    """An outer symbol's partner is never outer; an inner one's is uniform;
    weighted by the occupancy the rows give the occupancy back; the other
    formats have independent symbols."""
    c = mp.neighbour_conditional("pam6")
    assert c.shape == (6, 6) and np.allclose(c.sum(axis=1), 1.0)
    assert np.allclose(c[0], [0, 0.25, 0.25, 0.25, 0.25, 0]) and np.allclose(c[5], c[0])
    assert np.allclose(c[1:5], 1.0 / 6.0)
    p = mp.level_probabilities("pam6")
    assert np.allclose(p @ c, p)
    joint = p[:, None] * c                         # a pair read either way round
    assert np.allclose(joint, joint.T)
    for mod in ("nrz", "pam4", "pam8"):
        assert mp.neighbour_conditional(mod) is None


def test_pam6_pair_bit_distance_is_the_matrix_before_averaging():
    """``pair_bit_distance`` per position and partner averages, over the
    positions and the partner conditional, to ``bit_distance_matrix``; a
    right decision costs nothing, a corner is never sent, and the cost of
    an outer level's error does depend on the partner (that is the point)."""
    from halo_serdes.core.mapping import (
        _PAM6_CORNERS, bit_distance_matrix, neighbour_conditional, pair_bit_distance)

    D, C, B = pair_bit_distance("pam6"), neighbour_conditional("pam6"), bit_distance_matrix("pam6")
    assert D.shape == (2, 6, 6, 6)
    avg = 0.5 * (np.einsum("km,kmj->kj", C, D[0]) + np.einsum("km,kmj->kj", C, D[1]))
    assert np.allclose(avg, B)
    assert np.all(D[:, np.arange(6), :, np.arange(6)] == 0)
    for k, m in _PAM6_CORNERS:
        assert not D[0, k, m].any() and not D[1, m, k].any()
    assert D[0, 0, :, 1].min() != D[0, 0, :, 1].max()
    for mod in ("nrz", "pam4", "pam8"):
        assert pair_bit_distance(mod) is None


def test_pam6_event_bits_are_the_pairs_words_the_event_touches():
    """A one-symbol event costs ``bit_distance_matrix``'s entry; an
    alternating two-symbol event costs less than two single steps because
    the pair it lands in moves its word diagonally; a brute-force count over
    the sent words reproduces one case; ``pair_word_bits`` is the table."""
    from halo_serdes.core.mapping import (
        _PAM6_CORNERS, PAM6_DECODE, bit_distance_matrix, pair_event_bits, pair_word_bits)

    B = bit_distance_matrix("pam6")
    for j in range(5):
        assert pair_event_bits((1,), j) == pytest.approx(B[j, j + 1])
        assert pair_event_bits((-1,), j + 1) == pytest.approx(B[j + 1, j])
    for j in range(5):
        assert pair_event_bits((1, -1), j) < B[j, j + 1] + max(B[k, k - 1] for k in range(1, 6))
    # brute force: first symbol 2 moving +1 as the pair's first position, the
    # second symbol moving -1, averaged with the second-position alignment
    def popc(x):
        return bin(int(x)).count("1")
    first = [popc(PAM6_DECODE[2, v] ^ PAM6_DECODE[3, v - 1]) for v in range(1, 6) if (2, v) not in _PAM6_CORNERS]
    second = []
    for u in range(6):                       # partner before, unmoved
        if (u, 2) in _PAM6_CORNERS or not 0 <= u <= 5:
            continue
        second.append(popc(PAM6_DECODE[u, 2] ^ PAM6_DECODE[u, 3]))
    nxt = []                                  # the -1 symbol opens the next pair
    for u in range(1, 6):
        for v in range(6):
            if (u, v) in _PAM6_CORNERS:
                continue
            nxt.append(popc(PAM6_DECODE[u, v] ^ PAM6_DECODE[u - 1, v]))
    expect = 0.5 * (np.mean(first) + np.mean(second) + np.mean(nxt))
    assert pair_event_bits((1, -1), 2) == pytest.approx(expect)
    W = pair_word_bits("pam6")
    assert W.shape == (6, 6, 6, 6) and W[1, 2, 1, 2] == 0 and not W[0, 0].any()
    assert W[1, 2, 1, 3] == popc(PAM6_DECODE[1, 2] ^ PAM6_DECODE[1, 3])
    assert pair_event_bits((1,), 0) > 0 and pair_word_bits("pam4") is None
