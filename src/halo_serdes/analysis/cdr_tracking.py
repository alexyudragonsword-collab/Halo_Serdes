"""What the recovered clock did not follow: the CDR's tracking error, measured.

The time engine records the sampling position of every decision
(``SimResult.extras["phase_track"]``, in samples) and the transmit clock's
per-edge offsets are a deterministic function of the config and seed. Their
difference, with the lock offset removed, is the jitter that actually landed
on the sampler -- the quantity the statistical engine estimates in closed
form from the profile and the loop model (``cdr/linear.py``). Putting the two
side by side is how invariant #3 is checked for a coloured clock, so the
measurement lives here, next to the other acceptance instruments, and reads
only what the engine already produced.
"""

from __future__ import annotations

import numpy as np

from ..config.schema import LinkConfig
from ..engine.result import SimResult


def tx_edge_offsets_s(cfg: LinkConfig) -> np.ndarray:
    """The Tx clock offsets [s] the time engine applied for this config and seed.

    Regenerated rather than stored: the engine draws them first from
    ``default_rng(seed)``, before any other random number, so replaying that
    one step reproduces them exactly (``tests/test_clock_profile.py`` relies on
    the same order). Length ``n_symbols + 1``, boundary ``k`` starts symbol ``k``.
    """
    from ..engine.static_link import make_pattern
    from ..engine.timedomain import _tx_symbols
    from ..tx.pipeline import TxPipeline

    rng = np.random.default_rng(cfg.sim.seed)
    tx_pipe = TxPipeline.from_config(cfg)
    return tx_pipe.edge_offsets(tx_pipe.symbol_stage(_tx_symbols(cfg, make_pattern(cfg))), rng)


def cdr_tracking_error_s(cfg: LinkConfig, res: SimResult,
                         skip: int | None = None) -> np.ndarray:
    """Sampling-instant error [s] per decision: recovered clock minus Tx clock, mean removed.

    ``phase_track[k]`` is where decision ``k`` sampled, in samples; its
    nominal position is ``phase_track[0] + k * osr``. Decision ``k`` samples
    the main cursor of symbol ``k``, whose edge was moved by offset ``k``.
    The first ``skip`` decisions (default: the engine's CDR settling count)
    are dropped so the acquisition transient does not count as jitter.
    """
    phase = np.asarray(res.extras["phase_track"], dtype=np.float64)
    if phase.size == 0:
        return phase
    jit = tx_edge_offsets_s(cfg)
    n = min(phase.size, jit.size - 1)
    k = np.arange(n, dtype=np.float64)
    rx_t = (phase[:n] - phase[0] - k * cfg.osr) * cfg.dt
    err = rx_t - jit[:n]
    start = int(res.extras.get("settle", 0) if skip is None else skip)
    err = err[min(start, max(n - 1, 0)):]
    return err - err.mean() if err.size else err
