"""The only place the waveform and symbol rate domains may cross.

Project invariant #5 says the two rate domains
(:class:`~halo_serdes.core.waveform.Waveform` at ``osr`` samples per UI, and
:class:`~halo_serdes.core.waveform.SymbolStream` at one value per UI) are never
implicitly converted — only through an explicit sampler. That was written down
and then not built: there was no sampler module, ``SymbolStream`` was defined
and never once constructed anywhere in the project, and eleven call sites did
the conversion inline with a stride slice or a ``np.repeat``. An invariant that
nothing enforces is a comment.

So this module holds the primitives, and ``tests/test_domain_boundary.py``
asserts that stride-by-``osr`` indexing appears nowhere else in
``src/halo_serdes``. Three distinct operations, which is why the inline versions
were easy to confuse:

:func:`baud_samples`
    Waveform -> symbol rate. Pick one sample per UI at a chosen phase.
:func:`hold`
    Symbol rate -> waveform, zero-order hold. Each symbol becomes ``osr``
    identical samples: an ideal NRZ drive before any pulse shaping.
:func:`upsampled_taps`
    A *filter* stated at symbol rate onto the sample grid, zero-stuffed.
    Not a signal conversion — the taps keep their values and gain ``osr - 1``
    zeros between them, which is what makes a baud-rate FIR convolvable against
    an oversampled waveform.

None of these is new arithmetic: each body was lifted verbatim from the sites it
replaced, and the change was gated on every engine output staying bit-for-bit
identical across all nine presets.
"""

from __future__ import annotations

import numpy as np

from .waveform import SymbolStream, Waveform


def baud_samples(y: np.ndarray, osr: int, phase: int = 0) -> np.ndarray:
    """One sample per UI, starting at ``phase`` within the first UI.

    The array form, for callers whose result is not a symbol stream — pulse
    response cursors, for instance, which are baud-spaced samples of a response
    rather than a sequence of symbols. When the result *is* a symbol stream,
    prefer :func:`sample_baud`, which carries the UI and phase with it.
    """
    if osr < 1:
        raise ValueError(f"osr must be >= 1, got {osr}")
    if not 0 <= phase < osr:
        raise ValueError(f"phase {phase} outside [0, {osr})")
    return y[phase::osr]


def sample_baud(wave: Waveform, osr: int, phase: int = 0) -> SymbolStream:
    """Sample a waveform down to its symbol rate, typed.

    Returns a :class:`~halo_serdes.core.waveform.SymbolStream` so the UI and the
    instant it was taken at travel with the data instead of living in a local
    variable — which is the whole point of invariant #5, and the reason that
    type exists.
    """
    return SymbolStream(baud_samples(wave.y, osr, phase),
                        ui=osr * wave.dt, phase=phase * wave.dt)


def hold(values: np.ndarray, osr: int) -> np.ndarray:
    """Zero-order hold: one symbol becomes ``osr`` identical samples.

    The ideal rectangular drive, before Tx FIR shaping or a rise-time filter.
    """
    if osr < 1:
        raise ValueError(f"osr must be >= 1, got {osr}")
    return np.repeat(values, osr)


def upsampled_taps(taps: np.ndarray, osr: int, *,
                   length: int | None = None) -> np.ndarray:
    """Place symbol-rate filter taps on the sample grid, zero-stuffed.

    ``length`` pads (or truncates) the result — several callers want the taps
    inside a buffer sized for an FFT rather than exactly ``osr * n_taps`` long.
    Default length is ``osr * (n_taps - 1) + 1``: the last tap lands on the
    final sample with no trailing zeros, matching ``arr[::osr] = taps`` on an
    array of exactly that size.
    """
    if osr < 1:
        raise ValueError(f"osr must be >= 1, got {osr}")
    taps = np.asarray(taps, dtype=np.float64)
    if length is None:
        length = osr * (taps.size - 1) + 1 if taps.size else 0
    out = np.zeros(length, dtype=np.float64)
    n = min(taps.size, (length + osr - 1) // osr)
    out[:n * osr:osr] = taps[:n]
    return out
