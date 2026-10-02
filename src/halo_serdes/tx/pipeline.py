"""The transmitter as one pipeline.

Symbol domain (one value per UI)::

    levels -> [PR filter] -> FFE -> [DAC]

then the single explicit domain change, a zero-order hold that places every
edge where the Tx clock puts it, and the waveform domain::

    ZOH + edge offsets -> [driver compression] -> driver pole

Every engine and analysis path that needs "what the Tx sends" builds it here,
so a stage added to the pipeline reaches all of them at once. Bracketed
stages are identity until configured; with all of them off the pipeline is
the FIR + ZOH + single pole the engines assembled by hand before, value for
value (``tests/golden/`` and the engine fingerprint pin that).

The order inside the symbol stage is fixed: partial-response shaping comes
before the FFE and the DAC, so a transmit-side 1+aD target is what the DAC
has to resolve (its extra levels cost DAC range).
"""

from __future__ import annotations

import numpy as np

from ..config.schema import LinkConfig
from ..core.sampler import hold, upsampled_taps
from ..core.waveform import Waveform
from .builder import apply_single_pole, symbols_to_voltages, tx_fir
from .jitter import edge_jitter_seq, jittered_zoh


class TxPipeline:
    """Symbols -> Tx output for one ``LinkConfig``.

    ``symbol_stage`` returns per-UI values (volts at the DAC output);
    ``waveform`` is the only method that returns a ``Waveform``.
    """

    def __init__(self, cfg: LinkConfig) -> None:
        self.cfg = cfg
        tx = cfg.tx
        self.fir_taps = np.asarray(tx.fir_taps, dtype=np.float64)
        self.fir_n_pre = int(tx.fir_n_pre)

    @classmethod
    def from_config(cls, cfg: LinkConfig, rng: np.random.Generator | None = None) -> "TxPipeline":
        return cls(cfg)

    # ------------------------------------------------------------ symbol domain
    @property
    def ffe_active(self) -> bool:
        # a single tap is skipped entirely (its n_pre is meaningless; see
        # cairn/architecture-invariants.md #1)
        return self.fir_taps.size > 1

    def levels(self, symbols: np.ndarray) -> np.ndarray:
        return symbols_to_voltages(symbols, self.cfg)

    def pr_filter(self, v: np.ndarray) -> np.ndarray:
        """Transmit-side partial-response shaping: not modelled yet (identity)."""
        return v

    def ffe(self, v: np.ndarray) -> np.ndarray:
        if self.ffe_active:
            return tx_fir(v, self.fir_taps, self.fir_n_pre)
        return v

    def dac(self, v: np.ndarray) -> np.ndarray:
        return v

    def symbol_stage(self, symbols: np.ndarray) -> np.ndarray:
        """Line symbols (level indices, after any precoding) -> per-UI volts."""
        return self.dac(self.ffe(self.pr_filter(self.levels(symbols))))

    # ----------------------------------------------------------- the crossing
    def edge_offsets(self, v_sym: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Per-boundary Tx clock offsets [s] (n + 1 values), drawn from ``rng``
        in the order the engines have always drawn them."""
        return edge_jitter_seq(v_sym.size, self.cfg, rng, v_sym)

    def _zoh(self, v_sym: np.ndarray, rng: np.random.Generator | None):
        if rng is None:
            # ideal edges: a plain hold, not a jittered fill with zero offsets
            # (the fractional fill rounds boundary samples differently)
            return hold(v_sym, self.cfg.osr), None
        jit = self.edge_offsets(v_sym, rng)
        return jittered_zoh(v_sym, self.cfg.osr, jit, self.cfg.ui), jit

    # ---------------------------------------------------------- waveform domain
    def driver(self, wave: Waveform) -> Waveform:
        if self.cfg.tx.bw is not None:
            wave = apply_single_pole(wave, self.cfg.tx.bw)
        return wave

    def waveform_and_edges(self, v_sym: np.ndarray, rng: np.random.Generator | None = None):
        """(Tx output waveform, edge offsets or None). ``rng=None`` means ideal
        edges (the static engine)."""
        y, jit = self._zoh(v_sym, rng)
        return self.driver(Waveform(y, self.cfg.dt)), jit

    def waveform(self, v_sym: np.ndarray, rng: np.random.Generator | None = None) -> Waveform:
        return self.waveform_and_edges(v_sym, rng)[0]

    # ------------------------------------------------------ statistical engine
    def equivalent_symbol_response(self, osr: int | None = None) -> np.ndarray | None:
        """The LTI part of the symbol stage on the sample grid, or None when it
        is the identity: what the statistical engine convolves into the pulse.

        The driver pole is not in it: the statistical engine has never modelled
        ``tx.bw`` (a known gap, recorded in cairn/DSP发端与PR.md)."""
        if not self.ffe_active:
            return None
        return upsampled_taps(self.fir_taps, self.cfg.osr if osr is None else osr)
