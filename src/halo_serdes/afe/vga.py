"""VGA: linear gain now; AGC loop and 3rd-order nonlinearity arrive with the
time-domain engine (Phase 2/4)."""

from __future__ import annotations

from ..core.waveform import Waveform


class Vga:
    def __init__(self, gain: float = 1.0) -> None:
        self.gain = gain

    def apply(self, wave: Waveform) -> Waveform:
        return Waveform(wave.y * self.gain, wave.dt, wave.t0)
