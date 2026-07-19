"""Jitter tolerance (JTOL): the max sinusoidal jitter the receiver tolerates
vs SJ frequency, at a target BER.

Standard SerDes compliance deliverable. Inject Tx sinusoidal jitter (``tx.sj_ui``
amplitude at ``tx.sj_freq``) and binary-search the amplitude where BER crosses a
threshold. Below the CDR loop bandwidth the recovered clock tracks the jitter →
large tolerance; above it the sampling point drifts → tolerance falls to the
intrinsic timing margin. The tolerated amplitude vs frequency is compared to a
compliance mask (the tolerance must stay above it).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from ..config.schema import LinkConfig


@dataclass
class JtolResult:
    freqs: np.ndarray        # SJ frequency [Hz]
    tol_ui: np.ndarray       # tolerated SJ amplitude (0-pk) [UI]
    ber_threshold: float

    @property
    def tol_ui_pp(self) -> np.ndarray:
        return 2.0 * self.tol_ui

    def summary(self) -> str:
        lo = self.tol_ui[0] if self.tol_ui.size else float("nan")
        hi = self.tol_ui[-1] if self.tol_ui.size else float("nan")
        return (f"JTOL @ BER<{self.ber_threshold:.0e}: "
                f"{lo:.2f} UI at {self.freqs[0]/1e6:.2f} MHz -> "
                f"{hi:.3f} UI at {self.freqs[-1]/1e6:.1f} MHz (0-pk)")


def _ber_at(cfg: LinkConfig, sj_freq: float, sj_ui: float, channel) -> float:
    from ..engine import run_time_link

    tx = dataclasses.replace(cfg.tx, sj_ui=float(sj_ui), sj_freq=float(sj_freq))
    c = dataclasses.replace(cfg, tx=tx)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = run_time_link(c, channel=channel)
    return res.ber.ber


def jitter_tolerance(cfg: LinkConfig, freqs, ber_threshold: float = 1e-3,
                     amp_lo: float = 0.02, amp_hi: float = 2.0,
                     iters: int = 6, n_symbols: int | None = None,
                     channel=None) -> JtolResult:
    """Tolerated SJ amplitude (0-pk UI) at each frequency for BER < threshold.

    For each frequency a binary search brackets the amplitude where BER crosses
    ``ber_threshold``. ``amp_hi`` caps the search (report it when even the max
    tested amplitude passes); ``amp_lo`` is the floor (report 0 when the link
    fails even at the smallest amplitude). Use a moderate ``ber_threshold``
    (e.g. 1e-3) so a manageable ``n_symbols`` resolves it.
    """
    freqs = np.asarray(freqs, dtype=float)
    if n_symbols is not None:
        cfg = dataclasses.replace(
            cfg, sim=dataclasses.replace(cfg.sim, n_symbols=int(n_symbols)))
    if channel is None:
        from ..channel import ChannelModel

        channel = ChannelModel.from_config(cfg)

    def passes(f, amp):
        return _ber_at(cfg, f, amp, channel) < ber_threshold

    tol = np.zeros(freqs.size)
    for i, f in enumerate(freqs):
        if passes(f, amp_hi):
            tol[i] = amp_hi
            continue
        if not passes(f, amp_lo):
            tol[i] = 0.0
            continue
        lo, hi = amp_lo, amp_hi          # lo passes, hi fails
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            if passes(f, mid):
                lo = mid
            else:
                hi = mid
        tol[i] = lo
    return JtolResult(freqs=freqs, tol_ui=tol, ber_threshold=ber_threshold)


def jtol_mask(freqs, lf_max_ui: float = 5.0, f_corner: float = 4e6,
              hf_floor_ui: float = 0.1) -> np.ndarray:
    """A generic compliance mask (0-pk UI) vs frequency: flat ``lf_max_ui`` at
    low frequency, rolling off at 20 dB/dec below ``f_corner`` to a high-
    frequency floor ``hf_floor_ui``. Parameterize per standard as needed."""
    freqs = np.asarray(freqs, dtype=float)
    rolloff = lf_max_ui * f_corner / freqs
    return np.maximum(hf_floor_ui, np.minimum(lf_max_ui, rolloff))
