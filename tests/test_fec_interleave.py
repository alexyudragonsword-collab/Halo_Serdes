"""fec.interleave: a lane's bit errors mapped through FEC multiplexing and
codeword interleaving, and the frame error rate extrapolated from its events.

The structural checks pin the mapping (every codeword gets its n symbols;
known bursts land where the layout says); the statistical ones pin the
extrapolation against direct counts and against the independent-bit
projection it has to reduce to."""

import dataclasses

import numpy as np
import pytest
from scipy.stats import poisson

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    AdcConfig, ChannelConfig, FfeConfig, RxConfig, SimConfig,
)
from halo_serdes.engine import run_static_link, run_time_link
from halo_serdes.fec import fec_threshold
from halo_serdes.fec.interleave import (
    BIT_MUX_2CW, SEQUENTIAL, SYMBOL_PAIR_4CW, FecLayout, bit_errors, burst_profile,
    compound_fer,
)

P_STAR = fec_threshold("kp4")
BIT_MUX_8 = FecLayout(lanes=2, pcs_lanes=8, mux_bits=1, interleave=2, stagger=1)
LAYOUTS = [SEQUENTIAL, FecLayout(interleave=2), BIT_MUX_2CW, BIT_MUX_8, SYMBOL_PAIR_4CW]


# ------------------------------------------------------------- the mapping

@pytest.mark.parametrize("lay", LAYOUTS, ids=lambda x: f"L{x.lanes}M{x.pcs_lanes}g{x.mux_bits}I{x.interleave}")
def test_every_codeword_gets_its_n_symbols(lay):
    """Over one codeword group the lane carries n * 10 * interleave / lanes
    bits: each codeword gets n / lanes distinct symbols, 10 bits apiece."""
    span = lay.n * 10 * lay.interleave // lay.lanes
    cw, sym = lay.locate(np.arange(3 * span))
    for g in range(3):
        c, s = cw[g * span:(g + 1) * span], sym[g * span:(g + 1) * span]
        assert set(np.unique(c)) == set(range(g * lay.interleave, (g + 1) * lay.interleave))
        for k in np.unique(c):
            assert np.unique(s[c == k]).size == lay.n // lay.lanes
            assert np.all(np.bincount(s[c == k])[np.unique(s[c == k])] == 10)


def test_sequential_is_consecutive_symbols_of_one_codeword():
    cw, sym = SEQUENTIAL.locate(np.array([0, 9, 10, 5439, 5440]))
    assert list(cw) == [0, 0, 0, 0, 1] and list(sym) == [0, 0, 1, 543, 544]


def test_a_short_burst_lands_where_the_layout_puts_it():
    """Four consecutive wire bits: one RS symbol on one lane; four FEC lanes
    under 4:1 bit multiplexing, two of them in each codeword (checkerboard);
    one symbol under symbol-pair multiplexing."""
    burst = np.arange(100, 104)
    marks = {name: sorted(burst_profile(burst, 10**6, lay).marks)
             for name, lay in (("seq", SEQUENTIAL), ("bit", BIT_MUX_2CW), ("pair", SYMBOL_PAIR_4CW))}
    assert marks == {"seq": [1], "bit": [2, 2], "pair": [1]}
    # interleaving alone splits a burst that straddles two symbols
    assert sorted(burst_profile(np.arange(8, 12), 10**6, SEQUENTIAL).marks) == [2]
    assert sorted(burst_profile(np.arange(8, 12), 10**6, FecLayout(interleave=2)).marks) == [1, 1]


def test_events_are_split_by_the_gap():
    e = np.array([0, 2, 4, 100, 1000])                  # PAM4 wire bits: symbols 0, 1, 2, 50, 500
    assert burst_profile(e, 10**6, gap_symbols=8).n_events == 3
    assert burst_profile(e, 10**6, gap_symbols=60).n_events == 2


def test_layout_is_validated():
    with pytest.raises(ValueError, match="divide evenly"):
        FecLayout(lanes=3)
    with pytest.raises(ValueError, match="mux_bits"):
        FecLayout(pcs_lanes=2, mux_bits=3)
    with pytest.raises(ValueError, match="stagger"):
        FecLayout(stagger=-1)
    with pytest.raises(ValueError, match="interleave"):
        FecLayout(interleave=0)


# ------------------------------------------------------------- bit errors

def test_bit_errors_count_gray_bits():
    ref = np.array([0, 1, 2, 3, 0, 1])
    dec = np.array([0, 2, 2, 0, 2, 1])                  # 1->2: MSB; 3->0: 10 vs 00, MSB; 0->2: both
    err, n = bit_errors(dec, ref)
    assert n == 12 and list(err) == [2, 6, 8, 9]


def test_bit_errors_skip_symbols_without_a_reference():
    err, n = bit_errors(np.array([1, 3, 0]), np.array([0, -1, 0]))
    assert n == 4 and list(err) == [1]
    err, n = bit_errors(np.array([1, 0, 1]), np.array([0, 0, -1]), modulation="nrz")
    assert n == 2 and list(err) == [0]


_ADC = LinkConfig(modulation="pam4", symbol_rate=53.125e9, osr=16,
                  channel=ChannelConfig(kind="analytic", length_m=0.1, rdc=5.0, r_skin=2e-3),
                  rx=RxConfig(arch="adc_dsp", noise_rms=0.045, ffe=FfeConfig(n_pre=2, n_post=6),
                              adc=AdcConfig(fullscale=0.6)),
                  sim=SimConfig(n_symbols=20_000, seed=3, pattern="prbs13q"))


@pytest.mark.parametrize("cfg", [_ADC, dataclasses.replace(_ADC, rx=RxConfig(noise_rms=0.05))],
                         ids=["adc_dsp", "mixed_signal"])
def test_the_engines_hand_back_what_they_scored_against(cfg):
    for r in (run_time_link(cfg), run_static_link(cfg)):
        dec, ref = r.extras["decisions"], r.extras["reference"]
        assert dec.shape == ref.shape
        assert np.mean(dec != ref) == pytest.approx(r.ser)
        err, n = bit_errors(dec, ref)
        assert err.size > 0 and n == 2 * dec.size
        assert err.size <= r.ber.n_errors                # the checker counts 0<->3 as two


# ------------------------------------------------------------- extrapolation

def test_compound_fer_of_single_symbol_events_is_a_poisson_tail():
    for lam in (0.3, 1.19, 4.0):
        assert compound_fer(lam, np.array([0.0, 1.0]), 15) == pytest.approx(poisson.sf(15, lam), rel=1e-9)


def _bursts(n_bits, rate, mean_len, rng):
    starts = np.flatnonzero(rng.random(n_bits) < rate)
    out = [s + np.flatnonzero(rng.random(rng.geometric(1 / mean_len)) < 0.6) for s in starts]
    e = np.unique(np.concatenate([o if o.size else np.array([s]) for o, s in zip(out, starts)]))
    return e[e < n_bits]


def _direct(lanes_err, lay, n_bits):
    """Map every lane's errors with its own FEC lanes and count each
    codeword's erred symbols: what the compound model extrapolates."""
    m, g = lay.pcs_lanes, lay.mux_bits
    per = lay.n * lay.interleave // (lay.lanes * m)
    keys = []
    for ell, e in enumerate(lanes_err):
        r, w = np.divmod(e, m * g)
        q = ell * m + w // g
        s = (r * g + w % g) // 10
        keys.append(np.stack([(s // per) * lay.interleave + (s + lay.stagger * q) % lay.interleave,
                              s * lay.lanes * m + q], 1))
    k = np.unique(np.concatenate(keys), axis=0)
    n_cw = n_bits * lay.lanes // (lay.n * 10)
    return np.bincount(k[:, 0], minlength=n_cw)[:n_cw]


@pytest.mark.parametrize("lay", [SEQUENTIAL, BIT_MUX_2CW, SYMBOL_PAIR_4CW],
                         ids=["sequential", "bit_mux", "symbol_pair"])
def test_compound_fer_matches_direct_counts(lay):
    """Bursty errors on every lane of the flow, counted per codeword, against
    the compound Poisson sum built from the events: P(> t) for small t, where
    a direct count can see it."""
    rng = np.random.default_rng(5)
    n_bits = 2_000_000
    lanes = [_bursts(n_bits, 1e-3, 6, rng) for _ in range(lay.lanes * (4 // lay.lanes))]
    counts = _direct(lanes[:lay.lanes], lay, n_bits)
    for extra in range(1, 4 // lay.lanes):              # more flows of the same layout
        counts = np.concatenate([counts, _direct(lanes[extra * lay.lanes:(extra + 1) * lay.lanes],
                                                 lay, n_bits)])
    profs = [burst_profile(e, n_bits, lay) for e in lanes]
    marks = np.concatenate([p.marks for p in profs])
    lam = marks.size * lay.n * 10 / (n_bits * len(lanes))
    pmf = np.bincount(marks) / marks.size
    for t in (2, 4, 6):
        direct = np.mean(counts > t)
        assert compound_fer(lam, pmf, t) == pytest.approx(direct, rel=0.12, abs=0.01), t


def test_isolated_errors_reproduce_the_independent_bit_threshold():
    """Errors far apart, one per event, one RS symbol each: the compound sum is
    a Poisson tail, which sits 1.4 % below the binomial at KP4's threshold."""
    n_bits = 20_000_000
    e = np.arange(0, n_bits, 4567)
    prof = burst_profile(e, n_bits)
    assert list(np.unique(prof.marks)) == [1]
    assert prof.threshold() / P_STAR == pytest.approx(0.986, abs=0.003)
    assert prof.frame_error_rate(prof.threshold()) == pytest.approx(
        burst_profile(e, n_bits).frame_error_rate(prof.threshold()))


@pytest.mark.parametrize("lay", [SEQUENTIAL, BIT_MUX_2CW, SYMBOL_PAIR_4CW],
                         ids=["sequential", "bit_mux", "symbol_pair"])
def test_random_errors_sit_just_below_the_projection(lay):
    """Independent errors at the threshold: within a few per cent of 1, below
    it -- two errors falling together by chance read as a burst."""
    rng = np.random.default_rng(1)
    n_bits = 16_000_000
    prof = burst_profile(np.flatnonzero(rng.random(n_bits) < P_STAR), n_bits, lay)
    assert 0.92 < prof.threshold() / P_STAR < 1.0


def test_runs_of_errors_split_the_layouts():
    """A PR-like run (three consecutive symbols, one bit each): one lane keeps
    it in one or two symbols of one codeword; 4:1 bit multiplexing spreads it
    over symbols of the same codeword; symbol pairs over different codewords.
    The threshold moves accordingly -- the clustering, not the symbol count,
    rules a t = 15 code."""
    rng = np.random.default_rng(2)
    n_bits = 16_000_000
    starts = np.sort(rng.choice(n_bits // 2 - 10, size=int(n_bits * P_STAR / 3), replace=False))
    e = np.unique(np.concatenate([2 * (starts + k) + rng.integers(0, 2, starts.size) for k in range(3)]))
    th = {name: burst_profile(e, n_bits, lay).threshold() / P_STAR
          for name, lay in (("seq", SEQUENTIAL), ("bit", BIT_MUX_2CW), ("pair", SYMBOL_PAIR_4CW))}
    assert th["pair"] > 1.3 > 1.0 > th["seq"] > th["bit"], th


def test_frame_error_rate_scales_with_the_event_rate():
    prof = burst_profile(np.arange(0, 10_000_000, 3001), 10_000_000)
    f = [prof.frame_error_rate(b) for b in (1e-4, 2e-4, 4e-4)]
    assert f[0] < f[1] < f[2]
    assert prof.frame_error_rate() == pytest.approx(prof.frame_error_rate(prof.ber))


def test_resample_keeps_the_event_count_and_is_reproducible():
    rng = np.random.default_rng(3)
    e = _bursts(4_000_000, 2e-4, 6, rng)
    prof = burst_profile(e, 4_000_000, BIT_MUX_2CW)
    a = prof.resample(np.random.default_rng(9))
    b = prof.resample(np.random.default_rng(9))
    assert a.n_events == prof.n_events and np.array_equal(a.marks, b.marks)
    assert np.array_equal(np.unique(a.event), np.arange(np.unique(a.event).size))
    assert np.all(np.diff(a.event) >= 0)


def test_no_errors_has_nothing_to_extrapolate():
    prof = burst_profile(np.array([], dtype=np.int64), 10**6)
    assert prof.n_events == 0 and prof.frame_error_rate() == 0.0
    with pytest.raises(ValueError, match="no errors"):
        prof.threshold()
