"""Transmitter figures of merit measured on the Tx output waveform.

TX SNDR: the waveform the configured transmitter sends against a reference
transmitter that is identical except for the DAC and the driver nonlinearity
(same levels, same FFE, same edges, same driver pole), both sampled at the
symbol centres. The reference is scaled by its least-squares gain first, so a
uniform amplitude loss (a compressed driver that is still linear in the
small) is not counted as distortion; what is left is quantisation, INL,
clipping and the nonlinear part of the compression.

R_LM: the four PAM4 level means at the symbol centres, through 802.3
120D.3.1.2 (``core.static_curve.rlm``).

Approximate, unsourced to a clause: 802.3dj states a TX SNDR and an R_LM
limit for its electrical transmitters (order of 30+ dB and 0.95); the clause
values were not reachable from this environment, so they are not quoted as
limits here.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from ..config.schema import LinkConfig
from ..core.sampler import baud_samples
from ..core.static_curve import rlm
from ..core.waveform import Waveform


def _centres(wave: Waveform | np.ndarray, osr: int, phase: int | None, skip: int) -> np.ndarray:
    y = wave.y if isinstance(wave, Waveform) else np.asarray(wave)
    s = baud_samples(y, osr, osr // 2 if phase is None else phase)
    return s[skip: s.size - skip] if skip else s


def tx_sndr_db(wave, ref_wave, osr: int, *, phase: int | None = None, skip: int = 16) -> float:
    """TX SNDR [dB] of ``wave`` against ``ref_wave`` (module docstring)."""
    w = _centres(wave, osr, phase, skip)
    r = _centres(ref_wave, osr, phase, skip)
    g = float(np.dot(w, r) / np.dot(r, r))
    err = w - g * r
    p_err = float(np.mean(err ** 2))
    if p_err == 0.0:
        return float("inf")
    return float(10 * np.log10(np.mean((g * r) ** 2) / p_err))


def measured_rlm(wave, symbols: np.ndarray, osr: int, *, phase: int | None = None,
                 skip: int = 16) -> float:
    """R_LM of the four level means at the symbol centres (PAM4 only)."""
    w = _centres(wave, osr, phase, skip)
    s = np.asarray(symbols)[skip: len(symbols) - skip] if skip else np.asarray(symbols)
    n = min(w.size, s.size)
    means = [float(w[:n][s[:n] == k].mean()) for k in range(4)]
    return rlm(sorted(means))


def reference_tx(cfg: LinkConfig) -> LinkConfig:
    """The same link with an ideal DAC, a linear driver and no Tx noise (the
    noise is part of what the SNDR measures: left in, the reference would
    draw the same noise and the difference would cancel it)."""
    tx = dataclasses.replace(cfg.tx, dac_bits=None, dac_fs=None, dac_thermo_msbs=0,
                             dac_unit_sigma=0.0, drv_nl="none", drv_compression=0.0,
                             noise_rms=0.0)
    return dataclasses.replace(cfg, tx=tx)


@dataclasses.dataclass
class TxReport:
    sndr_db: float
    rlm: float | None
    dac_clipped: int
    wave: Waveform
    ref_wave: Waveform
    line_symbols: np.ndarray


def tx_report(cfg: LinkConfig, symbols: np.ndarray | None = None) -> TxReport:
    """Build the configured and the reference Tx output for ``cfg``'s pattern
    (or ``symbols``) with the same edge draws, and measure both figures."""
    from ..engine.static_link import check_symbols, make_pattern
    from ..engine.timedomain import _tx_symbols
    from ..tx.pipeline import TxPipeline

    user = make_pattern(cfg) if symbols is None else check_symbols(cfg, symbols)
    line = _tx_symbols(cfg, user)
    out = []
    for c in (cfg, reference_tx(cfg)):
        pipe = TxPipeline.from_config(c)
        rng = np.random.default_rng(c.sim.seed)
        out.append((pipe.waveform(pipe.symbol_stage(line), rng), pipe))
    (wave, pipe), (ref, _) = out
    r = measured_rlm(wave, line, cfg.osr) if cfg.modulation == "pam4" else None
    return TxReport(sndr_db=tx_sndr_db(wave, ref, cfg.osr), rlm=r,
                    dac_clipped=int(pipe.stats["dac_clipped"]), wave=wave, ref_wave=ref,
                    line_symbols=line)
