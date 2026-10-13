"""Where a lane's bit errors land in the RS code: multiplexing, interleaving
and the frame error rate of bursty errors.

``pre_to_post_fec_ber`` projects post-FEC performance from the pre-FEC BER as
if every bit error were independent. A partial-response target with a Viterbi
detector makes its errors in runs instead (example 42's 1 + aD: 2.6 errors
per event near the KP4 threshold), and what a run costs RS(544,514) depends on
where its bits land:

- one lane, consecutive 10-bit symbols: a run mostly stays inside one or two
  RS symbols, so the RS symbol error rate is half the independent-bit one --
  and yet the frame error rate is higher, because a t = 15 code fails on its
  tail and the tail is ruled by how many of one codeword's symbols a single
  event takes, not by the mean (the threshold drops to about 0.5x);
- 802.3 spreads a lane over several FEC (PCS) lanes -- bit by bit in the
  400GBASE-R PMA, by 10-bit symbol pairs at 200G per lane -- and interleaves
  codewords symbol by symbol. Bit multiplexing puts every errored bit of a run
  in a different RS symbol, often of the same codeword (0.25x, 0.05x at 8:1);
  symbol pairs with four codewords keep the run in few symbols and give each
  codeword one of them (about 1.9x: the projection is pessimistic).

This module evaluates it: a lane's bit error positions (``bit_errors``) are
mapped through a :class:`FecLayout` to (codeword, RS symbol) and grouped into
error events; the frame error rate is extrapolated from the event rate and the
measured footprints as a compound Poisson sum (``BurstProfile``), and
``BurstProfile.threshold`` gives the pre-FEC BER at which that frame error
rate equals the independent-bit projection's at its own threshold. Example 43
has the measurements; ``cairn/多lane建模.md`` §6 the record.

What it assumes
---------------
- Events are independent and their rate scales with BER while their
  footprint does not (so a run measured above the threshold can be read
  below it). ``gap_symbols`` decides what one event is; errors that close
  together at the measured BER also merge by chance, which is why the run
  should be near the threshold.
- Every physical lane of the flow sees independent events with the same
  statistics (no correlated errors across lanes, e.g. from crosstalk).
- The layout is a model of 802.3's, not a transcription: the clause text
  was not reachable (the summaries are second hand, ``cairn/多lane建模.md``),
  so the presets carry the structure that material describes -- 16 PCS lanes
  bit-multiplexed four to a lane with a two-codeword checkerboard (400GBASE-R),
  symbol-pair multiplexing eight to a lane with four codewords (200G/lane) --
  and every parameter is open.
- The frame error criterion: frame loss, not post-FEC BER. Comparing at equal
  frame error rate needs no assumption about how many bits a failed frame
  leaves wrong (``pre_to_post_fec_ber``'s 1.5 per symbol).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

from ..core.mapping import gray_decode_symbols
from .reach import fec_threshold
from .rs import M_BITS, post_fec_frame_error_rate


@dataclass(frozen=True)
class FecLayout:
    """How one physical lane's wire bits reach RS symbols and codewords.

    ``lanes`` physical lanes carry a flow of ``lanes * pcs_lanes`` FEC (PCS)
    lanes, ``pcs_lanes`` each, multiplexed onto the wire in units of
    ``mux_bits`` (1: bit multiplexing; 10: by RS symbol; 20: by symbol pair).
    The flow's RS symbols go round robin over the FEC lanes, from
    ``interleave`` codewords at a time: the symbol at position s of FEC lane q
    belongs to codeword ``(s + stagger * q) % interleave`` of its group, so
    each FEC lane alternates codewords and ``stagger`` offsets neighbouring
    lanes (1 with two codewords: the checkerboard).

    One lane is enough to evaluate: the others add independent events at the
    same rate, and which lane an event is on only relabels the codewords."""

    lanes: int = 1
    pcs_lanes: int = 1
    mux_bits: int = 10
    interleave: int = 1
    stagger: int = 0
    n: int = 544
    t: int = 15

    def __post_init__(self):
        for name in ("lanes", "pcs_lanes", "mux_bits", "interleave"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1, got {getattr(self, name)}")
        if self.stagger < 0:
            raise ValueError(f"stagger must be >= 0, got {self.stagger}")
        if not 0 <= self.t < self.n:
            raise ValueError(f"t must be in [0, n), got t={self.t}, n={self.n}")
        if self.n % (self.lanes * self.pcs_lanes):
            raise ValueError(f"a codeword's {self.n} symbols do not divide evenly over "
                             f"{self.lanes * self.pcs_lanes} FEC lanes")
        g = self.mux_bits
        if self.pcs_lanes > 1 and M_BITS % g and g % M_BITS:
            raise ValueError(f"mux_bits must divide or be a multiple of the {M_BITS}-bit "
                             f"RS symbol, got {g}")

    def locate(self, bits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(codeword, RS symbol) of each wire bit index on the lane; both
        are labels, unique across the run."""
        b = np.asarray(bits, dtype=np.int64)
        m, g = self.pcs_lanes, self.mux_bits
        r, w = np.divmod(b, m * g)
        q = w // g                                  # FEC lane on this physical lane
        s = (r * g + w % g) // M_BITS               # RS symbol index along it
        per = self.n * self.interleave // (self.lanes * m)   # per FEC lane per codeword group
        cw = (s // per) * self.interleave + (s + self.stagger * q) % self.interleave
        return cw, s * m + q


#: One lane, consecutive 10-bit symbols, one codeword at a time.
SEQUENTIAL = FecLayout()
#: 400GBASE-R-like: 16 PCS lanes bit-multiplexed four to a lane, two
#: codewords in a checkerboard (802.3bs, second hand).
BIT_MUX_2CW = FecLayout(lanes=4, pcs_lanes=4, mux_bits=1, interleave=2, stagger=1)
#: 200G/lane-like: 16 PCS lanes symbol-pair multiplexed eight to a lane,
#: four codewords, odd lanes two codewords on (802.3df/dj, second hand).
SYMBOL_PAIR_4CW = FecLayout(lanes=2, pcs_lanes=8, mux_bits=20, interleave=4, stagger=2)


def bit_errors(decisions: np.ndarray, reference: np.ndarray,
               modulation: str = "pam4") -> tuple[np.ndarray, int]:
    """Wire-bit indices in error, and how many bits were checked.

    ``decisions`` and ``reference`` are user-domain symbols as the time-domain
    engines return them (``SimResult.extras``); a PAM4 symbol carries its
    Gray-decoded bits MSB first. Symbols whose reference is -1 (a cycle slip
    left none) are not checked."""
    dec = np.asarray(decisions, dtype=np.int64)
    ref = np.asarray(reference, dtype=np.int64)
    ok = ref >= 0
    if modulation == "pam4":
        wrong = gray_decode_symbols(np.where(ok, dec, 0)) != gray_decode_symbols(np.where(ok, ref, 0))
        return np.flatnonzero(wrong), 2 * int(ok.sum())
    if modulation == "nrz":
        return np.flatnonzero(ok & (dec != ref)), int(ok.sum())
    raise ValueError(f"modulation must be 'pam4' or 'nrz', got {modulation!r}")


def compound_fer(lam: float, pmf: np.ndarray, t: int) -> float:
    """P(more than ``t`` symbol errors in a codeword) when events reach it as
    Poisson(``lam``) and each puts ``x`` symbol errors in it with
    probability ``pmf[x]`` (x >= 1).

    Summed as tails, not as 1 - P(<= t): the answer sits near 1e-13."""
    return _fer(lam, _event_tails(pmf, t), t)


def _event_tails(pmf: np.ndarray, t: int) -> np.ndarray:
    """P(k events put more than t symbol errors in a codeword), k = 1..t."""
    pmf = np.asarray(pmf, dtype=np.float64)
    tail = np.empty(t)
    conv = np.array([1.0])
    for k in range(t):
        conv = np.convolve(conv, pmf)
        tail[k] = conv[t + 1:].sum()
    return tail


def _fer(lam: float, tail: np.ndarray, t: int) -> float:
    # t + 1 events or more always fail
    return float(poisson.sf(t, lam) + poisson.pmf(np.arange(1, t + 1), lam) @ tail)


@dataclass(frozen=True)
class BurstProfile:
    """A lane's error events as one codeword sees them.

    ``marks`` has one entry per (event, codeword it reached): how many of that
    codeword's RS symbols the event put in error; ``event`` says which event."""

    layout: FecLayout
    n_bits: int
    n_errors: int
    n_events: int
    marks: np.ndarray
    event: np.ndarray

    @property
    def ber(self) -> float:
        return self.n_errors / self.n_bits

    @property
    def touch_rate(self) -> float:
        """Events reaching one codeword, on average, at the measured BER: a
        codeword spans n * 10 bits of the flow, and each lane's events reach
        it at the rate the lane makes them."""
        return self.marks.size * self.layout.n * M_BITS / self.n_bits

    @property
    def pmf(self) -> np.ndarray:
        return np.bincount(self.marks) / self.marks.size

    @property
    def symbol_error_rate(self) -> float:
        """RS symbol error rate: errored symbols per symbol carried."""
        return float(self.marks.sum()) * M_BITS / self.n_bits

    def frame_error_rate(self, ber: float | None = None) -> float:
        """Compound Poisson FER at ``ber`` (default: the measured one), the
        event rate scaled with it and the footprints held."""
        if self.n_events == 0:
            return 0.0
        scale = 1.0 if ber is None else ber / self.ber
        return _fer(self.touch_rate * scale, _event_tails(self.pmf, self.layout.t), self.layout.t)

    def resample(self, rng: np.random.Generator) -> BurstProfile:
        """The same number of events drawn with replacement (a bootstrap
        replicate: how much of ``threshold`` the event count decides)."""
        pick = rng.integers(0, self.n_events, self.n_events)
        start = np.searchsorted(self.event, np.arange(self.n_events + 1))
        counts = np.diff(start)[pick]
        first = np.cumsum(counts) - counts
        idx = np.repeat(start[pick] - first, counts) + np.arange(counts.sum())
        return BurstProfile(self.layout, self.n_bits, self.n_errors, self.n_events,
                            self.marks[idx], np.repeat(np.arange(self.n_events), counts))

    def threshold(self, code: str = "kp4", target: float = 1e-15) -> float:
        """Pre-FEC BER at which this profile's FER equals the independent-bit
        projection's FER at ``fec_threshold(code, target=target)``.

        The ratio to that threshold is how far the projection errs for these
        errors under this layout: above 1 it is pessimistic."""
        if self.n_events == 0:
            raise ValueError("no errors: nothing to extrapolate from")
        p_star = fec_threshold(code, target=target)
        ser = 1.0 - (1.0 - p_star) ** M_BITS
        goal = post_fec_frame_error_rate(ser, self.layout.n, self.layout.t)
        tail, t = _event_tails(self.pmf, self.layout.t), self.layout.t
        lo, hi = np.log10(p_star) - 3.0, np.log10(p_star) + 3.0
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if _fer(self.touch_rate * 10.0 ** mid / self.ber, tail, t) > goal:
                hi = mid
            else:
                lo = mid
        return 10.0 ** (0.5 * (lo + hi))


def burst_profile(errors: np.ndarray, n_bits: int, layout: FecLayout = SEQUENTIAL,
                  gap_symbols: int = 8, bits_per_symbol: int = 2) -> BurstProfile:
    """Group a lane's bit errors into events and map each through ``layout``.

    Errors closer than ``gap_symbols`` symbols (``bits_per_symbol`` wire bits
    each: 2 for PAM4, 1 for NRZ) belong to the same event."""
    e = np.sort(np.asarray(errors, dtype=np.int64))
    if e.size == 0:
        none = np.zeros(0, dtype=np.int64)
        return BurstProfile(layout, int(n_bits), 0, 0, none, none)
    event = np.concatenate([[0], np.cumsum(np.diff(e) > gap_symbols * bits_per_symbol)])
    cw, sym = layout.locate(e)
    order = np.lexsort((sym, cw, event))
    ev, cw, sym = event[order], cw[order], sym[order]
    # distinct (event, codeword, symbol), then how many per (event, codeword)
    first = np.concatenate([[True], (np.diff(ev) != 0) | (np.diff(cw) != 0) | (np.diff(sym) != 0)])
    ev, cw = ev[first], cw[first]
    start = np.concatenate([[True], (np.diff(ev) != 0) | (np.diff(cw) != 0)])
    first_of = np.flatnonzero(start)
    marks = np.diff(np.concatenate([first_of, [ev.size]]))
    return BurstProfile(layout, int(n_bits), int(e.size), int(event[-1]) + 1, marks, ev[first_of])
