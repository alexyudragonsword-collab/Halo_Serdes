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
from .builder import symbols_to_voltages, tx_fir
from .dac import TxDac
from .driver import DriverNl, apply_single_pole
from .jitter import edge_jitter_seq, jittered_zoh


class TxPipeline:
    """Symbols -> Tx output for one ``LinkConfig``.

    ``symbol_stage`` returns per-UI values (volts at the DAC output);
    ``waveform`` is the only method that returns a ``Waveform``.
    """

    def __init__(self, cfg: LinkConfig, dac: TxDac | None = None,
                 driver_nl: DriverNl | None = None) -> None:
        self.cfg = cfg
        tx = cfg.tx
        self.fir_taps = np.asarray(tx.fir_taps, dtype=np.float64)
        self.fir_n_pre = int(tx.fir_n_pre)
        self.dac_model = dac
        self.driver_nl = driver_nl
        self.stats = {"dac_clipped": 0}

    @classmethod
    def from_config(cls, cfg: LinkConfig, rng: np.random.Generator | None = None) -> "TxPipeline":
        """``rng`` draws the DAC's unit-cell mismatch. By default it is a
        stream of its own (seeded from ``sim.seed``), never the link's
        generator: the mismatch is a property of the chip, and switching the
        DAC on must not move the edge, noise or ADC draws that follow."""
        tx = cfg.tx
        dac = None
        if tx.dac_bits is not None:
            if rng is None and tx.dac_unit_sigma > 0:
                rng = np.random.default_rng([cfg.sim.seed, 0x7DAC])
            dac = TxDac(tx.dac_bits, cls.dac_fullscale(cfg), thermo_msbs=tx.dac_thermo_msbs,
                        unit_sigma=tx.dac_unit_sigma, rng=rng)
        nl = None
        if tx.drv_nl != "none":
            nl = DriverNl(tx.drv_nl, compression=tx.drv_compression,
                          fullscale=0.5 * cls.dac_fullscale(cfg),
                          p1db_v=tx.drv_p1db_v, oip3_v=tx.drv_oip3_v)
        return cls(cfg, dac, nl)

    @staticmethod
    def dac_fullscale(cfg: LinkConfig) -> float:
        """Peak-to-peak range the DAC (and the driver's curve) spans: ``tx.dac_fs``,
        or by default exactly the FFE's peak output, swing * sum|taps|, so an
        unset full scale never clips."""
        if cfg.tx.dac_fs is not None:
            return float(cfg.tx.dac_fs)
        return float(cfg.tx.swing * np.abs(np.asarray(cfg.tx.fir_taps, dtype=np.float64)).sum())

    # ------------------------------------------------------------ symbol domain
    @property
    def ffe_active(self) -> bool:
        # a single tap is skipped entirely (its n_pre is meaningless; see
        # cairn/architecture-invariants.md #1)
        return self.fir_taps.size > 1

    def levels(self, symbols: np.ndarray) -> np.ndarray:
        return symbols_to_voltages(symbols, self.cfg)

    def pr_filter(self, v: np.ndarray) -> np.ndarray:
        """Transmit-side partial-response shaping: not modelled yet (identity).
        Its place is fixed: before the FFE and the DAC."""
        return v

    def ffe(self, v: np.ndarray) -> np.ndarray:
        if self.ffe_active:
            return tx_fir(v, self.fir_taps, self.fir_n_pre)
        return v

    def dac(self, v: np.ndarray) -> np.ndarray:
        if self.dac_model is None:
            return v
        self.stats["dac_clipped"] += self.dac_model.n_over_range(v)
        return self.dac_model(v)

    @property
    def dac_sigma_q(self) -> float:
        """Equivalent white error of the DAC per UI [V] (0 without one)."""
        return 0.0 if self.dac_model is None else self.dac_model.sigma_q

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
        # Hammerstein: the output stage compresses on its instantaneous drive,
        # then its load sets the bandwidth
        if self.driver_nl is not None:
            wave = Waveform(self.driver_nl(wave.y), wave.dt, wave.t0)
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

    # ------------------------------------------------------ receiver analysis
    def receiver_view(self, h: np.ndarray, osr: int | None = None) -> tuple[np.ndarray, int]:
        """The impulse a receiver's pulse analysis has to look at, and its lead.

        The Tx waveform already carries the FFE, but the engines filter it
        with ``h`` alone (channel, CTLE, VGA). Solving the receiver's starting
        FFE, DFE seeds, slicer scale, sampling phase and symbol delay from
        ``h`` alone equalises a pulse the receiver never sees. This returns
        ``h`` with the FFE's symbol response folded in, plus the number of
        samples by which that response's main cursor sits *later* than in the
        waveform (``tx_fir`` drops its first ``fir_n_pre`` outputs to align
        the main tap with the symbol): subtract it from the peak index before
        using it as a position in the received waveform.

        Without an FFE it is ``(h, 0)`` -- the same array, untouched.
        """
        osr = self.cfg.osr if osr is None else osr
        resp = self.equivalent_symbol_response(osr)
        if resp is None:
            return h, 0
        return np.convolve(h, resp), self.fir_n_pre * osr

    # ------------------------------------------------------ statistical engine
    def equivalent_symbol_response(self, osr: int | None = None) -> np.ndarray | None:
        """The LTI part of the symbol stage on the sample grid, or None when it
        is the identity: what the statistical engine convolves into the pulse.

        The DAC is not in it (it reaches the statistical engine as the
        equivalent noise ``dac_sigma_q``), nor is the driver pole: the
        statistical engine has never modelled ``tx.bw`` (a known gap, recorded
        in cairn/DSP发端与PR.md)."""
        if not self.ffe_active:
            return None
        return upsampled_taps(self.fir_taps, self.cfg.osr if osr is None else osr)
