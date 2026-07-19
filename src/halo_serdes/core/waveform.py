"""Signal containers.

Two rate domains with an explicit boundary (never implicitly converted):

- ``Waveform``: uniformly-sampled oversampled analog-domain signal (Tx output,
  channel output, CTLE/VGA output). Carries its time grid metadata.
- ``SymbolStream``: baud-rate sequence (decisions, DSP domain), carrying UI and
  the sampling phase it was taken at.

``ResponseSet`` bundles the four standard responses of an LTI block
(impulse/step/pulse/frequency), following PyBERT's ``calc_resps`` convention:
impulse responses are stored as *per-sample weights* (dimensionless), so that
``y = np.convolve(x, h)`` maps volts to volts and ``sum(h) == H(0)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

import numpy as np


@dataclass
class Waveform:
    y: np.ndarray          # samples [V] (float64)
    dt: float              # sample interval [s]
    t0: float = 0.0        # time of first sample [s]

    def __post_init__(self) -> None:
        self.y = np.asarray(self.y, dtype=np.float64)

    def __len__(self) -> int:
        return self.y.size

    @property
    def t(self) -> np.ndarray:
        return self.t0 + np.arange(self.y.size) * self.dt

    @property
    def duration(self) -> float:
        return self.y.size * self.dt

    def convolve(self, h: "Waveform") -> "Waveform":
        """Convolve with an impulse response in per-sample-weight convention."""
        if not np.isclose(self.dt, h.dt, rtol=1e-9):
            raise ValueError(f"dt mismatch: {self.dt} vs {h.dt}")
        y = np.convolve(self.y, h.y)[: self.y.size]
        return Waveform(y, self.dt, self.t0 + h.t0)

    def delayed(self, n_samples: int) -> "Waveform":
        return Waveform(self.y, self.dt, self.t0 + n_samples * self.dt)


@dataclass
class SymbolStream:
    y: np.ndarray          # one value per UI
    ui: float              # unit interval [s]
    phase: float = 0.0     # sampling instant offset within the UI [s]

    def __post_init__(self) -> None:
        self.y = np.asarray(self.y)

    def __len__(self) -> int:
        return self.y.size


@dataclass
class ResponseSet:
    """Impulse response plus lazily-derived step/pulse/frequency responses."""

    h: Waveform
    name: str = ""
    _cache: dict = field(default_factory=dict, repr=False)

    @cached_property
    def s(self) -> Waveform:
        """Step response: cumulative sum of the per-sample-weight impulse."""
        return Waveform(np.cumsum(self.h.y), self.h.dt, self.h.t0)

    def p(self, osr: int) -> Waveform:
        """Pulse (single-UI) response: s(t) - s(t - UI)."""
        key = ("p", osr)
        if key not in self._cache:
            s = self.s.y
            shifted = np.concatenate([np.zeros(osr), s[:-osr]]) if osr < s.size else np.zeros_like(s)
            self._cache[key] = Waveform(s - shifted, self.h.dt, self.h.t0)
        return self._cache[key]

    def H(self, n_freq: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        """One-sided frequency response ``(f, H)`` via zero-padded rFFT."""
        n = self.h.y.size if n_freq is None else 2 * (n_freq - 1)
        H = np.fft.rfft(self.h.y, n=n)
        f = np.fft.rfftfreq(n, d=self.h.dt)
        return f, H


def cascade(*responses: ResponseSet) -> ResponseSet:
    """Cascade LTI blocks by convolving their impulse responses."""
    if not responses:
        raise ValueError("cascade() needs at least one ResponseSet")
    acc = responses[0].h
    names = [responses[0].name]
    for r in responses[1:]:
        if not np.isclose(acc.dt, r.h.dt, rtol=1e-9):
            raise ValueError("cascade(): dt mismatch between responses")
        acc = Waveform(np.convolve(acc.y, r.h.y), acc.dt, acc.t0 + r.h.t0)
        names.append(r.name)
    return ResponseSet(acc, name="*".join(n for n in names if n))
