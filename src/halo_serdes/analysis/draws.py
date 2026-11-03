"""One link, several random draws: the time engine per seed against the
statistical engine's average over draws.

``sim.seed`` sets more than the noise: every per-link draw comes from it too
-- the time-interleaved ADC's lane offset, gain and skew, the comparator
offsets, the receiver clock's start. A link with mismatch is then one chip
per seed, and on the ``PAM4 224G ADC (TI mismatch)`` preset eight seeds read
from no errors to 1e-2 (2026-11-03). The statistical engine takes the
mismatch as white noise over the draws (``engine.stat_noise.ti_mismatch_sigma``),
so it is the pooled count, not any one seed, that it is checked against.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import numpy as np

from ..config.schema import LinkConfig


@dataclass(frozen=True)
class SeedSpread:
    """Per-seed time-domain BER and what the draws pool to."""

    seeds: np.ndarray        # the seeds run
    ber: np.ndarray          # time-domain BER at each seed
    n_errors: np.ndarray     # errors counted at each seed
    n_checked: np.ndarray    # bits checked at each seed
    stat_ber: Optional[float]  # the statistical engine (the average over draws), or None

    @property
    def pooled_ber(self) -> float:
        """All seeds' errors over all their bits: the estimate of the average
        over draws that ``stat_ber`` is compared with."""
        n = int(np.sum(self.n_checked))
        return float(np.sum(self.n_errors)) / n if n else float("nan")

    @property
    def median_ber(self) -> float:
        """The typical seed -- one chip, which a link with mismatch can put
        an order of magnitude or more below the pooled figure."""
        return float(np.median(self.ber))

    @property
    def spread_decades(self) -> Optional[float]:
        """log10(max / min) over the seeds that counted errors; None with
        fewer than two such seeds."""
        b = self.ber[self.n_errors > 0]
        if b.size < 2:
            return None
        return float(np.log10(np.max(b) / np.min(b)))


def seed_spread(cfg: LinkConfig, seeds: Iterable[int] = range(1, 9), *,
                n_symbols: Optional[int] = None, channel=None, with_stat: bool = True,
                checkpoint: Optional[Callable[[], None]] = None) -> SeedSpread:
    """Run the time engine once per seed and, with ``with_stat``, the
    statistical engine once.

    ``n_symbols``: per seed (default ``cfg.sim.n_symbols``). ``checkpoint`` is
    called before each run (a study's cancel point). The statistical engine
    on an ADC receiver takes the first seed's converged FFE -- the per-seed
    taps differ by well under a percent on the mismatch preset, and the
    statistical read with converged or start-up taps by 0.5 %.
    """
    from ..channel import ChannelModel
    from ..engine import run_time_link
    from ..engine.statistical import run_statistical

    seeds = np.asarray(list(seeds), dtype=np.int64)
    if seeds.size == 0:
        raise ValueError("seed_spread needs at least one seed")
    channel = channel if channel is not None else ChannelModel.from_config(cfg)
    n = int(n_symbols if n_symbols is not None else cfg.sim.n_symbols)
    ber, errs, checked = [], [], []
    taps = None
    for sd in seeds:
        if checkpoint is not None:
            checkpoint()
        run = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, seed=int(sd),
                                                               n_symbols=n))
        t = run_time_link(run, channel=channel)
        ber.append(float(t.ber.ber))
        errs.append(int(t.ber.n_errors))
        checked.append(int(t.ber.n_checked))
        if taps is None:
            taps = getattr(t, "ffe_taps", None)
    stat = None
    if with_stat:
        if checkpoint is not None:
            checkpoint()
        kw = {}
        if cfg.rx.arch == "adc_dsp" and taps is not None and len(taps) > 1:
            kw = dict(ffe_taps=taps, ffe_pre=cfg.rx.ffe.n_pre)
        stat = float(run_statistical(cfg, channel=channel, **kw).ber)
    return SeedSpread(seeds=seeds, ber=np.asarray(ber), n_errors=np.asarray(errs),
                      n_checked=np.asarray(checked), stat_ber=stat)
