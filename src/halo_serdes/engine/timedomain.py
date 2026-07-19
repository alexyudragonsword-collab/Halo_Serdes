"""Time-domain engine (Phase 2: mixed-signal architecture).

Full dynamic chain: jittered Tx -> channel+CTLE (overlap-save LTI pass) ->
AWGN -> joint DFE + bang-bang CDR serial kernel (Farrow fractional sampling)
with the staged startup sequence:

    1. CDR settling (adaptation frozen),
    2. data-aided LMS (known pattern reference),
    3. decision-directed tracking.

BER is counted only after warmup. The ADC-based architecture plugs into the
same front half in Phase 4.
"""

from __future__ import annotations

import numpy as np

from ..afe import Ctle, Vga
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core import prbs as prbs_mod
from ..core.prbs import BerResult
from ..core.waveform import Waveform
from ..cdr import ms_rx
from ..dsp import channel_cursors
from ..tx.builder import symbols_to_voltages, tx_fir
from ..tx.jitter import build_jittered_tx
from .lti import fft_filter
from .result import SimResult
from .static_link import _levels, fold_eye, make_pattern


def run_time_link(cfg: LinkConfig, channel: ChannelModel | None = None,
                  collect_eye: bool = False) -> SimResult:
    if cfg.rx.arch != "mixed_signal":
        raise NotImplementedError("Phase 2 time engine supports arch='mixed_signal'; "
                                  "adc_dsp arrives in Phase 4")
    rng = np.random.default_rng(cfg.sim.seed)
    osr = cfg.osr

    # --- pattern, Tx (FIR + jittered edges) ---
    symbols = make_pattern(cfg)
    v = symbols_to_voltages(symbols, cfg)
    if len(cfg.tx.fir_taps) > 1:
        v = tx_fir(v, cfg.tx.fir_taps, cfg.tx.fir_n_pre)
    tx_wave, _ = build_jittered_tx(v, cfg, rng)

    # --- channel + CTLE (single LTI impulse, overlap-save) ---
    if channel is None:
        channel = ChannelModel.from_config(cfg)
    ch_rs = channel.response_set(cfg.dt)
    h = ch_rs.h.y
    ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist) if cfg.rx.ctle.enable else None
    if ctle is not None:
        nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
        f = np.fft.rfftfreq(nfft, d=cfg.dt)
        h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    h = h * cfg.rx.vga_gain

    rx_y = fft_filter(tx_wave.y, h)
    if cfg.rx.noise_rms > 0:
        rx_y += rng.normal(scale=cfg.rx.noise_rms, size=rx_y.size)

    # --- pulse-response analysis: main cursor, initial phase, initial DFE taps ---
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    n_dfe = cfg.rx.dfe.n_taps
    cursors = channel_cursors(pulse, osr, 0, max(n_dfe, 1), peak_idx=peak)
    main = cursors[0]
    # DFE feedback multiplies slicer levels (which carry the main-cursor
    # scale), so tap weights are postcursors normalized to the main cursor.
    w_dfe0 = cursors[1: 1 + n_dfe] / main if n_dfe else np.zeros(0)
    # loop-delay constraint: tap 1 unusable if decision comes back too late
    if n_dfe and cfg.rx.dfe.loop_delay_ui > 1.0:
        w_dfe0 = w_dfe0.copy()
        w_dfe0[0] = 0.0

    levels = _levels(cfg) * abs(main)
    delay = peak // osr
    phase0 = float(peak % osr)

    # --- reference alignment: kernel starts at pos=peak, so decision k
    # samples the main cursor of symbol k directly ---
    n_sym_max = (rx_y.size - peak - 4 * osr) // osr - 2
    n_sym = min(symbols.size - delay - 1, n_sym_max)
    ref_idx = symbols[:n_sym].astype(np.int64)

    settle = min(cfg.sim.cdr_settle, n_sym // 4)
    train = min(cfg.sim.train_symbols, n_sym // 4)
    train_end = settle + train

    sum_alpha = 1.0
    if cfg.rx.dfe.sum_bw is not None:
        sum_alpha = float(1.0 - np.exp(-2.0 * np.pi * cfg.rx.dfe.sum_bw * cfg.ui))

    kp = osr * 2.0 ** (-cfg.rx.cdr.kp_shift)
    ki = osr * 2.0 ** (-cfg.rx.cdr.ki_shift)
    clamp = cfg.rx.cdr.clamp * osr if cfg.rx.cdr.clamp else 0.0

    mu = cfg.rx.dfe.mu if cfg.rx.dfe.adapt != "none" else 0.0
    # training uses reference decisions from the start; adaptation begins
    # after CDR settling
    ref_arr = np.full(n_sym, -1, dtype=np.int64)
    ref_arr[:train_end] = ref_idx[:train_end]

    dec, y_sum, phase, w_dfe, pd_hist = ms_rx(
        rx_y, osr, float(peak), n_sym,
        levels.astype(np.float64), np.asarray(w_dfe0, dtype=np.float64),
        float(mu), 100, float(kp), float(ki), float(clamp),
        float(sum_alpha), ref_arr, int(train_end), int(settle))

    n_run = dec.size
    warm = cfg.sim.warmup_discard if cfg.sim.warmup_discard is not None else train_end
    warm = min(warm, n_run - 1)

    dec_c = dec[warm:n_run]
    ref_c = ref_idx[warm:n_run]

    ser = float(np.mean(dec_c != ref_c))
    if cfg.modulation == "pam4":
        ber = prbs_mod.symbol_checker(ref_c, dec_c, gray=True)
    else:
        idx = np.nonzero(dec_c != ref_c)[0]
        ber = BerResult(n_checked=dec_c.size, n_errors=idx.size, error_idx=idx)

    ideal = levels[ref_c]
    err_v = y_sum[warm:n_run] - ideal
    snr_db = 10.0 * np.log10(np.mean(ideal ** 2) / max(np.mean(err_v ** 2), 1e-30))

    eye = fold_eye(rx_y, osr, int(phase0) % osr, n_traces=2000) if collect_eye else None

    return SimResult(
        ber=ber, ser=ser, slicer_snr_db=snr_db, n_symbols=dec_c.size,
        ffe_taps=None, dfe_taps=w_dfe, sample_phase=int(phase0) % osr,
        eye_data=eye, y_slicer=y_sum[warm: warm + 20000],
        extras={"phase_track": phase, "pd_hist": pd_hist, "main_cursor": main,
                "levels": levels, "warmup": warm, "w_dfe0": np.asarray(w_dfe0),
                "settle": settle, "train_end": train_end})
