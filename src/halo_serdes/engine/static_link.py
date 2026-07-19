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
"""

from __future__ import annotations

import numpy as np

from ..afe import Ctle, Vga
from ..channel import ChannelModel
from ..config.schema import LinkConfig
from ..core import prbs as prbs_mod
from ..core.mapping import nrz_levels, pam4_levels
from ..core.prbs import BerResult
from ..core.waveform import Waveform
from ..dsp import apply_ffe, channel_cursors, dfe_static, mmse_ffe
from ..dsp.ffe import equalized_cursors
from ..tx import build_tx_waveform
from .result import SimResult


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

    # --- pattern & Tx ---
    symbols = make_pattern(cfg)
    tx_wave = build_tx_waveform(symbols, cfg)

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
    y_baud = rx_wave.y[phase::osr]
    # symbol alignment: pulse peak at sample `peak` means symbol k lands at
    # baud index k + peak//osr
    delay = peak // osr
    n_sym = symbols.size - delay - n_post_c
    y_baud = y_baud[delay: delay + n_sym]
    ref_symbols = symbols[:n_sym]

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
    w_dfe = eq_cursors[eq_pre + 1: eq_pre + 1 + n_dfe].copy() if n_dfe > 0 else np.zeros(0)

    levels = _levels(cfg) * main  # slicer levels scaled by the equalized main cursor
    dec, y_eq = dfe_static(y_ffe.astype(np.float64), w_dfe.astype(np.float64), levels)

    # --- metrics ---
    ideal = levels[ref_symbols]
    err_v = y_eq - ideal
    snr_db = 10.0 * np.log10(np.mean(ideal ** 2) / max(np.mean(err_v ** 2), 1e-30))

    ser = float(np.mean(dec != ref_symbols))
    if cfg.modulation == "pam4":
        ber = prbs_mod.symbol_checker(ref_symbols, dec, gray=True)
    else:
        n_err = int(np.sum(dec != ref_symbols))
        idx = np.nonzero(dec != ref_symbols)[0]
        ber = BerResult(n_checked=n_sym, n_errors=n_err, error_idx=idx)

    eye = None
    if collect_eye:
        eye = fold_eye(rx_wave.y, osr, phase, n_traces=min(2000, n_sym - 2))

    return SimResult(ber=ber, ser=ser, slicer_snr_db=snr_db, n_symbols=n_sym,
                     ffe_taps=w_ffe, dfe_taps=w_dfe, sample_phase=phase,
                     eye_data=eye, y_slicer=y_eq[: 20000],
                     extras={"cursors": cursors, "eq_cursors": eq_cursors,
                             "eq_pre": eq_pre, "main_cursor": main})


def fold_eye(y: np.ndarray, osr: int, phase: int, n_traces: int = 2000,
             n_ui: int = 2) -> np.ndarray:
    """Fold a waveform into 2-UI segments centered on the sampling phase."""
    span = n_ui * osr
    start = phase + osr // 2
    n_avail = (y.size - start) // span
    n_traces = min(n_traces, n_avail)
    seg = y[start: start + n_traces * span]
    return seg.reshape(n_traces, span)
