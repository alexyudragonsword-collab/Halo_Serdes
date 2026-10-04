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
has to resolve (its extra levels cost DAC range). It is scaled by 1/(1+a):
the composite peak equals the unshaped one, so the DAC and driver span what
they spanned before and the level spacing pays for the extra levels.
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
        t = np.asarray(cfg.pr.target, dtype=np.float64) if cfg.pr.at_tx else np.ones(1)
        # 1/sum|t| keeps the composite peak at the unshaped one (for 1 + aD,
        # a >= 0, that is the 1/(1 + a) of before)
        self.pr_taps = t / np.abs(t).sum() if np.any(t[1:] != 0.0) else None
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
        """Transmit-side shaping by the target over its absolute sum,
        (v[k] + a v[k-1] [+ b v[k-2]]) / (1 + |a| [+ |b|]); identity unless
        ``pr.at == "tx"`` with a controlled cursor. Causal: the first symbols
        have no predecessors (the sequence starts from 0)."""
        if self.pr_taps is None:
            return v
        out = v * self.pr_taps[0]
        for lag in range(1, self.pr_taps.size):
            out[lag:] += self.pr_taps[lag] * v[:-lag]
        return out

    def _symbol_taps(self, shaping: bool = True) -> tuple[np.ndarray, int] | None:
        """PR filter and FFE as one symbol-spaced response and its precursor
        count, or None when both are identity."""
        taps = self.fir_taps if self.ffe_active else None
        n_pre = self.fir_n_pre if self.ffe_active else 0
        if shaping and self.pr_taps is not None:
            taps = self.pr_taps if taps is None else np.convolve(taps, self.pr_taps)
        return None if taps is None else (taps, n_pre)

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
    def receiver_view(self, h: np.ndarray, osr: int | None = None,
                      shaping: bool = True) -> tuple[np.ndarray, int]:
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
        ``shaping=False`` leaves a transmit PR filter out: a 1 + aD pulse has
        two comparable cursors and its peak can be the a x_{k-1} one, so the
        main cursor is located on the unshaped pulse (same lead, same start).
        """
        osr = self.cfg.osr if osr is None else osr
        resp = self.equivalent_symbol_response(osr, shaping)
        if resp is None:
            return h, 0
        return np.convolve(h, resp), self.symbol_response_lead(osr)

    # ------------------------------------------------------ statistical engine
    def driver_response(self, osr: int | None = None) -> tuple[np.ndarray, int] | None:
        """The driver pole as a sampled impulse, and how many of its samples
        lie before t = 0; None without ``tx.bw``.

        It is the inverse rFFT of the same H(f) that ``apply_single_pole``
        multiplies the waveform by, so both engines see the same operator,
        aliasing included. H is not band-limited, so that inverse rings: up
        to ~10 % of the peak just before t = 0, then an alternating tail that
        only falls as 1/n. The non-causal samples are kept and reported as
        the lead; the window is +-32 UI (or 40 time constants after t = 0 if
        that is longer), which leaves the weight within ~6e-5 of the DC gain.
        """
        bw = self.cfg.tx.bw
        if bw is None:
            return None
        osr = self.cfg.osr if osr is None else osr
        dt = self.cfg.ui / osr
        tau = 1.0 / (2.0 * np.pi * bw)
        pre = 32 * osr
        keep = max(int(np.ceil(40.0 * tau / dt)) + 1, pre)
        n = 1 << int(np.ceil(np.log2(4 * (keep + pre))))
        h = np.fft.irfft(1.0 / (1.0 + 1j * np.fft.rfftfreq(n, d=dt) / bw), n)
        return np.concatenate([h[n - pre:], h[:keep]]), pre

    def equivalent_symbol_response(self, osr: int | None = None,
                                   shaping: bool = True) -> np.ndarray | None:
        """The LTI part of the Tx after the levels -- PR filter, FFE and driver pole -- on
        the sample grid, or None when it is the identity: what the statistical
        engine convolves into the pulse and the receivers' pulse analysis sees.

        The DAC is not in it: it reaches the statistical engine as the
        equivalent noise ``dac_sigma_q`` (see ``after_dac_response``). Its main
        cursor sits ``symbol_response_lead`` samples later than in the
        waveform."""
        osr = self.cfg.osr if osr is None else osr
        st = self._symbol_taps(shaping)
        resp = None if st is None else upsampled_taps(st[0], osr)
        drv = self.driver_response(osr)
        if drv is None:
            return resp
        return drv[0] if resp is None else np.convolve(resp, drv[0])

    def symbol_response_lead(self, osr: int | None = None) -> int:
        """Samples by which ``equivalent_symbol_response`` is late against
        the waveform: the FFE's dropped precursor outputs plus the driver
        impulse's non-causal samples."""
        osr = self.cfg.osr if osr is None else osr
        drv = self.driver_response(osr)
        st = self._symbol_taps()
        return (0 if st is None else st[1] * osr) + (0 if drv is None else drv[1])

    def after_dac_response(self, osr: int | None = None) -> np.ndarray | None:
        """What follows the DAC inside the Tx (the driver pole), or None: the
        path its quantisation error takes before the channel."""
        drv = self.driver_response(osr)
        return None if drv is None else drv[0]
