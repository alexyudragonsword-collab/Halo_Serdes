"""Phase 1 static link: full LTI chain + static equalization + MC BER.

Pipeline: pattern -> Tx (FIR, ZOH) -> channel (frequency-domain convolution)
-> CTLE/VGA -> AWGN -> best-phase baud sampling -> FFE (ZF/MMSE from the
equalized pulse) -> static DFE -> slicer -> checker.

No CDR and no adaptation here: the sampling phase comes from the pulse-response
peak (serdespy shift_signal upgraded to pulse-peak alignment), and FFE/DFE
weights are solved once from the pulse response. The streaming time-domain
engine with CDR/adaptive loops replaces this in Phase 2; this module stays as
the fast "link feasibility" evaluator and as the reference for closed-form
tests.

.. note:: **EQ semantics — link budget, not RX circuit.** The FFE solved here
   represents the *total linear equalization budget* of the link, wherever it
   physically lives. Product mixed-signal receivers carry no FFE (the split
   is Tx FIR + RX CTLE + RX DFE, with Tx taps trained over a backchannel —
   see ``engine.backchannel.train_tx_fir``); RX-side many-tap FFE is real
   only in the ADC/DSP architecture (``rx.arch = "adc_dsp"``). Use this
   evaluator to answer "how much linear EQ does this channel need", then map
   the budget onto an architecture with the time-domain engine.
"""

from __future__ import annotations

import numpy as np

from ..afe import Ctle, Vga
from ..channel import ChannelModel
from ..config.schema import LinkConfig
from ..core import prbs as prbs_mod
from ..core.mapping import nrz_levels, pam4_levels
from ..core.sampler import sample_baud
from ..core.waveform import Waveform
from ..dsp import apply_ffe, channel_cursors, dfe_static, mmse_ffe
from ..dsp.ffe import equalized_cursors
from ..tx import build_tx_waveform
from .result import SimResult
from .scoring import score


def make_pattern(cfg: LinkConfig) -> np.ndarray:
    """Pattern name -> symbol index stream (NRZ: 0/1, PAM4: 0..3)."""
    name = cfg.sim.pattern.lower()
    n = cfg.sim.n_symbols
    if name.startswith("prbs") and name.endswith("q"):
        order = int(name[4:-1])
        return prbs_mod.prbs_q_symbols(order, n)
    if name == "prqs10":
        return prbs_mod.prqs10(n)
    if name.startswith("prbs"):
        order = int(name[4:])
        bits = prbs_mod.prbs_bits(order, n * cfg.bits_per_symbol)
        if cfg.modulation == "pam4":
            from ..core.mapping import bits_to_pam4_symbols

            return bits_to_pam4_symbols(bits)
        return bits.astype(np.int64)
    raise ValueError(f"unknown pattern {cfg.sim.pattern!r}")


def _levels(cfg: LinkConfig) -> np.ndarray:
    if cfg.modulation == "pam4":
        return pam4_levels(cfg.tx.swing, cfg.tx.rlm)
    return nrz_levels(cfg.tx.swing)


def run_static_link(cfg: LinkConfig, channel: ChannelModel | None = None,
                    collect_eye: bool = True) -> SimResult:
    rng = np.random.default_rng(cfg.sim.seed)
    osr = cfg.osr

    # --- pattern & Tx (optionally 1/(1+D) precoded) ---
    symbols = make_pattern(cfg)
    line_symbols = symbols
    if cfg.precode:
        from ..core.mapping import precode_1plusd

        line_symbols = precode_1plusd(symbols, 2 ** cfg.bits_per_symbol)
    tx_wave = build_tx_waveform(line_symbols, cfg)

    # --- channel + CTLE + VGA as one LTI response (single frequency-domain pass) ---
    if channel is None:
        channel = ChannelModel.from_config(cfg)
    ch_rs = channel.response_set(cfg.dt)
    ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist) if cfg.rx.ctle.enable else None
    vga = Vga(cfg.rx.vga_gain)

    n = tx_wave.y.size
    f = np.fft.rfftfreq(n, d=cfg.dt)
    H_chain = np.fft.rfft(ch_rs.h.y, n=n)
    if ctle is not None:
        H_chain = H_chain * ctle.transfer(f)
    rx_y = np.fft.irfft(np.fft.rfft(tx_wave.y) * H_chain, n=n) * vga.gain

    # AWGN at the sampler input
    if cfg.rx.noise_rms > 0:
        rx_y = rx_y + rng.normal(scale=cfg.rx.noise_rms, size=rx_y.size)
    rx_wave = Waveform(rx_y, cfg.dt)

    # --- equalized pulse response for tap solving & sampling phase ---
    from ..channel.response import pulse_from_impulse

    h_chain = np.fft.irfft(H_chain, n=n)[: min(n, 400 * osr)]
    pulse = pulse_from_impulse(Waveform(h_chain * vga.gain, cfg.dt), osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    phase = peak % osr

    n_pre_c, n_post_c = 8, 24
    cursors = channel_cursors(pulse, osr, n_pre_c, n_post_c, peak_idx=peak)

    # --- baud sampling at the pulse-peak phase ---
    # The one domain crossing on this path, and now the only thing that
    # constructs a SymbolStream: the UI and the instant travel with it.
    y_baud = sample_baud(rx_wave, osr, phase).y
    # symbol alignment: pulse peak at sample `peak` means symbol k lands at
    # baud index k + peak//osr
    delay = peak // osr
    n_sym = symbols.size - delay - n_post_c
    y_baud = y_baud[delay: delay + n_sym]
    ref_symbols = line_symbols[:n_sym]   # what the slicer decides
    user_symbols = symbols[:n_sym]       # what BER is scored against

    # --- FFE (MMSE; noise_var=0 -> least-squares ZF) ---
    ffe_cfg = cfg.rx.ffe
    n_taps = ffe_cfg.n_pre + 1 + ffe_cfg.n_post
    noise_var = cfg.rx.noise_rms ** 2
    w_ffe = mmse_ffe(cursors, n_pre_c, n_taps, ffe_cfg.n_pre, noise_var=noise_var)
    y_ffe = apply_ffe(y_baud, w_ffe, ffe_cfg.n_pre)

    # --- static DFE from residual postcursors (serdespy 3_ffe_dfe recipe) ---
    eq_cursors, eq_pre = equalized_cursors(cursors, w_ffe, n_pre_c, ffe_cfg.n_pre)
    main = eq_cursors[eq_pre]
    n_dfe = cfg.rx.dfe.n_taps
    # weights normalized to the main cursor: feedback multiplies slicer levels
    w_dfe = (eq_cursors[eq_pre + 1: eq_pre + 1 + n_dfe] / main
             if n_dfe > 0 else np.zeros(0))

    levels = _levels(cfg) * main  # slicer levels scaled by the equalized main cursor
    dec, y_eq = dfe_static(y_ffe.astype(np.float64), w_dfe.astype(np.float64), levels)

    # --- metrics ---
    # warmup=0: nothing here adapts, so there is no startup transient to wait
    # out. dec_slicer=dec: no sequence detector on this path.
    sc = score(cfg, dec=dec, dec_slicer=dec, y_slicer=y_eq, levels=levels,
               line_idx=ref_symbols, user_idx=user_symbols,
               n_run=n_sym, warmup=0)

    eye = None
    if collect_eye:
        eye = fold_eye(rx_wave.y, osr, phase, n_traces=min(2000, n_sym - 2))

    return SimResult(ber=sc.ber, ser=sc.ser, slicer_snr_db=sc.snr_db,
                     n_symbols=n_sym,
                     ffe_taps=w_ffe, dfe_taps=w_dfe, sample_phase=phase,
                     eye_data=eye, y_slicer=sc.y_slicer,
                     extras={"cursors": cursors, "eq_cursors": eq_cursors,
                             "eq_pre": eq_pre, "main_cursor": main,
                             # the solved FFE is the link's total linear-EQ
                             # budget, not an RX circuit block (see module doc)
                             "eq_semantics": "link_budget"})


def fold_eye(y: np.ndarray, osr: int, phase: int, n_traces: int = 2000,
             n_ui: int = 2) -> np.ndarray:
    """Fold a waveform into n_ui-UI segments with the *sampling instant* at
    the segment center (for even n_ui: start one UI before a sampling point,
    so column span/2 lands exactly on the sampler)."""
    span = n_ui * osr
    start = phase + (n_ui // 2) * osr  # center column = phase (mod osr grid)
    n_avail = (y.size - start) // span
    n_traces = min(n_traces, n_avail)
    seg = y[start: start + n_traces * span]
    return seg.reshape(n_traces, span)
