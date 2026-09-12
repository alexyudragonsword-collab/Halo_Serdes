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

from ..afe import Ctle
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.waveform import Waveform
from ..cdr import ms_rx
from ..dsp import channel_cursors
from ..tx.builder import symbols_to_voltages, tx_fir
from ..tx.jitter import build_jittered_tx
from .lti import fft_filter
from .result import SimResult
from .scoring import (
    cdr_gains,
    score,
    training_schedule,
    warmup_symbols,
)
from .static_link import _levels, fold_eye, make_pattern


#: Symbols per data-aided LMS averaging batch, handed to the mixed-signal
#: kernel and reported in ``extras`` so a plot of the tap trajectory can be
#: labelled. It was written as a bare ``100`` in both places, one an argument
#: and one a reported value, which is two chances to disagree.
LMS_BATCH_SYMBOLS = 100


def _stage_jitter(cfg: LinkConfig, stages: dict[str, np.ndarray]) -> dict | None:
    """Per-stage jitter decomposition over captured waveforms.

    Needs a repeating pattern (pattern averaging separates DDJ); returns None
    with a note when the sim spans fewer than ~4 pattern periods. Crossings
    are taken at the center threshold (0) — for PAM4 this measures the middle
    eye, the standard timing reference.
    """
    from ..analysis.jitter import pattern_period, stage_jitter_budget

    try:
        plen = pattern_period(cfg.sim.pattern, cfg.modulation)
    except ValueError:
        return None
    if cfg.sim.n_symbols < 4 * plen:
        return {"_note": (f"pattern period {plen} symbols needs >=4 reps for "
                          f"decomposition; ran {cfg.sim.n_symbols}")}
    return stage_jitter_budget(stages, cfg.dt, cfg.ui, plen, thresh=0.0)


def _tx_symbols(cfg: LinkConfig, symbols: np.ndarray) -> np.ndarray:
    """Symbols actually launched on the line.

    With ``cfg.precode`` the user symbols pass through the 1/(1+D) mod-N
    precoder; the slicer then decides *precoded* symbols (so data-aided
    training must reference these), and the Rx undoes it in
    :func:`~halo_serdes.engine.scoring.user_decisions`.
    """
    if not cfg.precode:
        return symbols
    from ..core.mapping import precode_1plusd

    return precode_1plusd(symbols, 2 ** cfg.bits_per_symbol)


def _residual_ratios(eq_cursors: np.ndarray, eq_pre: int, n_dfe: int,
                     memory: int) -> np.ndarray:
    """Postcursors left for the sequence detector, normalized to the main one.

    The FFE shapes the pulse and the DFE cancels the first ``n_dfe``
    postcursors; whatever follows is the residual ISI the MLSD works over.
    """
    main = eq_cursors[eq_pre]
    start = eq_pre + 1 + n_dfe
    tail = eq_cursors[start: start + memory]
    if tail.size < memory:
        tail = np.pad(tail, (0, memory - tail.size))
    return tail / main


def _mlsd_post_detect(cfg: LinkConfig, y_slicer: np.ndarray, dec: np.ndarray,
                      levels: np.ndarray, resid: np.ndarray) -> np.ndarray:
    """Re-decide the symbol stream with the configured sequence detector.

    ``levels`` are the slicer levels in volts, so the trellis cursor vector is
    ``[1, r1, r2, ...]`` — the residual ratios scale those same volt levels.
    Returns ``dec`` unchanged when MLSD is off or the residual is negligible.
    """
    mcfg = cfg.rx.mlsd
    if mcfg.kind == "none":
        return dec
    from ..dsp.mlsd import post_detect

    cursors = np.concatenate([[1.0], np.asarray(resid, dtype=float)])
    if np.all(np.abs(cursors[1:]) < 1e-9):    # nothing left to detect over
        return dec
    method = "viterbi" if mcfg.kind == "viterbi" else "sliding"
    out = post_detect(np.asarray(y_slicer, dtype=float), cursors,
                      np.asarray(levels, dtype=float), method,
                      seq_len=mcfg.seq_len, margin=mcfg.margin)
    return np.asarray(out, dtype=np.int64)


def _apply_ami(cfg, h, tx_wave, tx_ami, rx_ami):
    """Fold AMI Tx/Rx models into the chain.

    GetWave models process the time-domain waveform (Tx before the channel,
    Rx after); Init-only LTI models transform the folded channel+CTLE impulse
    (convolution commutes, so Tx/Rx side is equivalent here). Returns the
    possibly-modified ``(h, tx_wave_y)``; the caller applies Rx GetWave after
    the channel pass.
    """
    tx_y = tx_wave.y
    if tx_ami is not None:
        if tx_ami.has_getwave:
            tx_y, _ = tx_ami.get_wave(tx_y, cfg.dt, cfg.ui)
        else:
            h = tx_ami.init(h, cfg.dt, cfg.ui)
    if rx_ami is not None and not rx_ami.has_getwave:
        h = rx_ami.init(h, cfg.dt, cfg.ui)
    return h, tx_y


def run_time_link(cfg: LinkConfig, channel: ChannelModel | None = None,
                  collect_eye: bool = False,
                  collect_jitter: bool = False,
                  tx_ami=None, rx_ami=None, xtalk=None) -> SimResult:
    if cfg.rx.arch == "adc_dsp":
        return _run_adc_link(cfg, channel, collect_eye, collect_jitter,
                             tx_ami, rx_ami, xtalk)
    from ..config.schema import (
        MS_COMFORT_DATA_RATE,
        MS_HARD_MAX_BAUD,
        MS_LIMIT_DATA_RATE,
    )

    comfort = MS_COMFORT_DATA_RATE.get(cfg.modulation, 16.0e9)
    limit = MS_LIMIT_DATA_RATE.get(cfg.modulation, 16.0e9)
    tol = 1 + 1e-9
    mod = cfg.modulation.upper()
    if cfg.data_rate > limit * tol or cfg.symbol_rate > MS_HARD_MAX_BAUD * tol:
        import warnings

        warnings.warn(
            f"mixed_signal arch at {cfg.data_rate / 1e9:.3g} Gb/s {mod} "
            f"exceeds the mixed-signal envelope (absolute limit "
            f"{limit / 1e9:.0f} Gb/s {mod} / hard ceiling "
            f"{MS_HARD_MAX_BAUD / 1e9:.0f} GBd): the eye closes on the "
            "reference channel and/or unmodeled circuit walls apply — use "
            "rx.arch='adc_dsp' (exploration runs allowed, results outside "
            "the supported envelope)",
            stacklevel=2)
    elif cfg.data_rate > comfort * tol:
        import warnings

        warnings.warn(
            f"mixed_signal arch at {cfg.data_rate / 1e9:.3g} Gb/s {mod} is "
            f"in the marginal zone (comfort {comfort / 1e9:.0f} Gb/s, "
            f"absolute limit {limit / 1e9:.0f} Gb/s {mod}): post-DFE eye "
            "margin on the reference channel drops below ~25% of the level "
            "spacing — expect FEC-dependent operation and verify per channel",
            stacklevel=2)
    rng = np.random.default_rng(cfg.sim.seed)
    osr = cfg.osr

    # --- pattern, Tx (optionally 1/(1+D) precoded; FIR + jittered edges) ---
    symbols = make_pattern(cfg)
    line_symbols = _tx_symbols(cfg, symbols)
    v = symbols_to_voltages(line_symbols, cfg)
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

    # --- optional AMI Tx/Rx models (Init folds into h, GetWave into the wave) ---
    h, tx_y = _apply_ami(cfg, h, tx_wave, tx_ami, rx_ami)

    rx_y = fft_filter(tx_y, h)
    if rx_ami is not None and rx_ami.has_getwave:
        rx_y, _ = rx_ami.get_wave(rx_y, cfg.dt, cfg.ui)

    # --- FEXT/NEXT crosstalk: sum independent aggressors at the victim node ---
    if xtalk:
        from ..channel.crosstalk import inject_crosstalk

        rx_y = inject_crosstalk(rx_y, xtalk, osr, cfg.modulation)

    # --- optional per-stage jitter budget (Tx / channel / after-CTLE) ---
    jitter_budget = None
    if collect_jitter:
        ch_only = fft_filter(tx_y, ch_rs.h.y * cfg.rx.vga_gain)
        jitter_budget = _stage_jitter(cfg, {
            "tx": tx_y, "chnl": ch_only, "ctle": rx_y})

    if cfg.rx.noise_rms > 0:
        rx_y += rng.normal(scale=cfg.rx.noise_rms, size=rx_y.size)

    # --- pulse-response analysis: main cursor, initial phase, initial DFE taps ---
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    n_dfe = cfg.rx.dfe.n_taps
    # take enough postcursors for the DFE *and* the residual an MLSD works over
    n_post_c = max(n_dfe, 1) + (cfg.rx.mlsd.memory if cfg.rx.mlsd.kind != "none"
                                else 0)
    cursors = channel_cursors(pulse, osr, 0, n_post_c, peak_idx=peak)
    main = cursors[0]
    # DFE feedback multiplies slicer levels (which carry the main-cursor
    # scale), so tap weights are postcursors normalized to the main cursor.
    w_dfe0 = cursors[1: 1 + n_dfe] / main if n_dfe else np.zeros(0)
    if n_dfe and cfg.rx.dfe.init == "zero":
        w_dfe0 = np.zeros(n_dfe)
    # loop-delay constraint: with direct analog feedback, tap 1 is unusable
    # if the decision comes back later than 1 UI; the unrolled tap-1 relaxes
    # the critical path to a mux and keeps the tap.
    tap1_unrolled = 1 if cfg.rx.dfe.tap1_mode == "unrolled" else 0
    if n_dfe and cfg.rx.dfe.loop_delay_ui > 1.0 and not tap1_unrolled:
        w_dfe0 = w_dfe0.copy()
        w_dfe0[0] = 0.0

    levels = _levels(cfg) * abs(main)
    delay = peak // osr
    phase0 = float(peak % osr)

    # --- reference alignment: kernel starts at pos=peak, so decision k
    # samples the main cursor of symbol k directly ---
    n_sym_max = (rx_y.size - peak - 4 * osr) // osr - 2
    n_sym = min(symbols.size - delay - 1, n_sym_max)
    # the slicer decides *line* symbols; BER is scored on user symbols
    ref_idx = line_symbols[:n_sym].astype(np.int64)
    user_idx = symbols[:n_sym].astype(np.int64)

    # training uses reference decisions from the start; adaptation begins
    # after CDR settling
    sched = training_schedule(cfg, n_sym, ref_idx)
    settle, train_end = sched.settle, sched.train_end

    sum_alpha = 1.0
    if cfg.rx.dfe.sum_bw is not None:
        sum_alpha = float(1.0 - np.exp(-2.0 * np.pi * cfg.rx.dfe.sum_bw * cfg.ui))

    kp, ki, clamp = cdr_gains(cfg.rx.cdr, osr)

    mu = cfg.rx.dfe.mu if cfg.rx.dfe.adapt != "none" else 0.0

    # per-branch comparator offsets for the unrolled tap-1 slicer bank
    if tap1_unrolled and cfg.rx.dfe.comparator_offset_sigma > 0:
        branch_off = rng.normal(scale=cfg.rx.dfe.comparator_offset_sigma,
                                size=levels.size)
    else:
        branch_off = np.zeros(levels.size)

    dec, y_sum, phase, w_dfe, pd_hist, w_dfe_hist = ms_rx(
        rx_y, osr, float(peak), n_sym,
        levels.astype(np.float64), np.asarray(w_dfe0, dtype=np.float64),
        float(mu), LMS_BATCH_SYMBOLS, float(kp), float(ki), float(clamp),
        float(sum_alpha), sched.reference, int(train_end), int(settle),
        int(tap1_unrolled), branch_off)

    n_run = dec.size
    # --- optional MLSD over the postcursors the DFE left behind ---
    resid_ratios = _residual_ratios(cursors, 0, n_dfe, cfg.rx.mlsd.memory)
    dec_slicer = dec
    dec = _mlsd_post_detect(cfg, y_sum[:n_run], dec, levels, resid_ratios)

    warm = warmup_symbols(cfg, train_end, n_run)
    sc = score(cfg, dec=dec, dec_slicer=dec_slicer, y_slicer=y_sum,
               levels=levels, line_idx=ref_idx, user_idx=user_idx,
               n_run=n_run, warmup=warm)

    eye = fold_eye(rx_y, osr, int(phase0) % osr, n_traces=2000) if collect_eye else None

    return SimResult(
        ber=sc.ber, ser=sc.ser, slicer_snr_db=sc.snr_db, n_symbols=sc.n_scored,
        ffe_taps=None, dfe_taps=w_dfe, sample_phase=int(phase0) % osr,
        eye_data=eye, y_slicer=sc.y_slicer,
        extras={"phase_track": phase, "pd_hist": pd_hist, "main_cursor": main,
                "levels": levels, "warmup": warm, "w_dfe0": np.asarray(w_dfe0),
                "settle": settle, "train_end": train_end,
                "w_dfe_hist": w_dfe_hist, "n_ave": LMS_BATCH_SYMBOLS,
                "jitter_budget": jitter_budget,
                "mlsd_resid": resid_ratios,
                "ser_slicer": sc.ser_slicer,
                "precode": cfg.precode})


def _run_adc_link(cfg: LinkConfig, channel: ChannelModel | None = None,
                  collect_eye: bool = False,
                  collect_jitter: bool = False,
                  tx_ami=None, rx_ami=None, xtalk=None) -> SimResult:
    """ADC-based RX: light CTLE -> TI-ADC -> digital FFE/DFE -> MM-CDR.

    Primary metrics for this architecture are slicer-input SNR and SER
    (post-EQ eye information is low); BER via the same checkers.
    """
    from ..afe.adc import TiAdc
    from ..cdr.adc_kernel import adc_rx
    from ..dsp import mmse_ffe
    from ..dsp.ffe import equalized_cursors

    rng = np.random.default_rng(cfg.sim.seed)
    osr = cfg.osr

    # --- pattern, Tx (optionally 1/(1+D) precoded) ---
    symbols = make_pattern(cfg)
    line_symbols = _tx_symbols(cfg, symbols)
    v = symbols_to_voltages(line_symbols, cfg)
    if len(cfg.tx.fir_taps) > 1:
        v = tx_fir(v, cfg.tx.fir_taps, cfg.tx.fir_n_pre)
    tx_wave, _ = build_jittered_tx(v, cfg, rng)

    # --- channel + CTLE front end ---
    if channel is None:
        channel = ChannelModel.from_config(cfg)
    ch_rs = channel.response_set(cfg.dt)
    h = ch_rs.h.y
    if cfg.rx.ctle.enable:
        ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
        nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
        f = np.fft.rfftfreq(nfft, d=cfg.dt)
        h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    h = h * cfg.rx.vga_gain

    h, tx_y = _apply_ami(cfg, h, tx_wave, tx_ami, rx_ami)

    rx_y = fft_filter(tx_y, h)
    if rx_ami is not None and rx_ami.has_getwave:
        rx_y, _ = rx_ami.get_wave(rx_y, cfg.dt, cfg.ui)

    if xtalk:
        from ..channel.crosstalk import inject_crosstalk

        rx_y = inject_crosstalk(rx_y, xtalk, osr, cfg.modulation)

    jitter_budget = None
    if collect_jitter:
        ch_only = fft_filter(tx_y, ch_rs.h.y * cfg.rx.vga_gain)
        jitter_budget = _stage_jitter(cfg, {
            "tx": tx_y, "chnl": ch_only, "ctle": rx_y})

    if cfg.rx.noise_rms > 0:
        rx_y += rng.normal(scale=cfg.rx.noise_rms, size=rx_y.size)

    # --- pulse analysis: initial FFE (MMSE), DFE, slicer levels ---
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    fcfg = cfg.rx.ffe
    n_pre_c, n_post_c = fcfg.n_pre + 4, fcfg.n_post + 12
    cursors = channel_cursors(pulse, osr, n_pre_c, n_post_c, peak_idx=peak)
    n_taps = fcfg.n_pre + 1 + fcfg.n_post
    w_ffe0 = mmse_ffe(cursors, n_pre_c, n_taps, fcfg.n_pre,
                      noise_var=cfg.rx.noise_rms ** 2)
    eq_cursors, eq_pre = equalized_cursors(cursors, w_ffe0, n_pre_c, fcfg.n_pre)
    main = eq_cursors[eq_pre]
    n_dfe = cfg.rx.dfe.n_taps
    w_dfe0 = (eq_cursors[eq_pre + 1: eq_pre + 1 + n_dfe] / main
              if n_dfe else np.zeros(0))
    levels = _levels(cfg) * abs(main)

    # --- TI-ADC ---
    adc = TiAdc(cfg.rx.adc, osr, rng)
    delay = peak // osr
    n_sym_max = (rx_y.size - peak - (fcfg.n_pre + 6) * osr) // osr - 2
    n_sym = min(symbols.size - delay - fcfg.n_pre - 2, n_sym_max)
    # the slicer decides *line* symbols; BER is scored on user symbols
    ref_idx = line_symbols[:n_sym].astype(np.int64)
    user_idx = symbols[:n_sym].astype(np.int64)
    enob_noise = (rng.normal(scale=adc.noise_sigma, size=n_sym + fcfg.n_pre + 2)
                  if adc.noise_sigma > 0 else np.zeros(n_sym + fcfg.n_pre + 2))

    sched = training_schedule(cfg, n_sym, ref_idx)
    settle, train_end = sched.settle, sched.train_end

    ccfg = cfg.rx.cdr
    kp, ki, clamp = cdr_gains(ccfg, osr)
    lat_blocks = max(0, ccfg.loop_latency_symbols // max(cfg.rx.adc.n_lanes, 1))

    mu_f = fcfg.mu if fcfg.adapt != "none" else 0.0
    mu_d = cfg.rx.dfe.mu if cfg.rx.dfe.adapt != "none" else 0.0

    dec, y_sl, phase, w_ffe, w_dfe, lane_of, q_hist = adc_rx(
        rx_y, osr, float(peak), n_sym, levels.astype(np.float64),
        adc.n_lanes, adc.offsets, adc.gains, adc.skews,
        adc.q_step, adc.code_max, enob_noise,
        np.asarray(w_ffe0, dtype=np.float64), fcfg.n_pre, float(mu_f),
        np.asarray(w_dfe0, dtype=np.float64), float(mu_d),
        float(kp), float(ki), float(clamp), float(ccfg.pd_offset),
        1 if ccfg.pd_input == "ffe" else 0, lat_blocks,
        sched.reference, int(train_end), int(settle))

    n_run = dec.size
    # --- optional MLSD over the residual the FFE/DFE left behind ---
    eq_final, eq_pre_f = equalized_cursors(cursors, w_ffe, n_pre_c, fcfg.n_pre)
    resid_ratios = _residual_ratios(eq_final, eq_pre_f, n_dfe,
                                    cfg.rx.mlsd.memory)
    dec_slicer = dec
    dec = _mlsd_post_detect(cfg, y_sl[:n_run], dec, levels, resid_ratios)

    warm = warmup_symbols(cfg, train_end, n_run)
    # score() undoes the precoder first: the slicer decided line symbols
    sc = score(cfg, dec=dec, dec_slicer=dec_slicer, y_slicer=y_sl,
               levels=levels, line_idx=ref_idx, user_idx=user_idx,
               n_run=n_run, warmup=warm)
    dec_c, ref_c = sc.decisions, sc.reference

    # per-lane SER (TI mismatch diagnostics)
    lane_c = lane_of[warm:n_run]
    lane_ser = np.array([
        float(np.mean(dec_c[lane_c == ln] != ref_c[lane_c == ln]))
        if np.any(lane_c == ln) else 0.0
        for ln in range(adc.n_lanes)])

    return SimResult(
        ber=sc.ber, ser=sc.ser, slicer_snr_db=sc.snr_db, n_symbols=sc.n_scored,
        ffe_taps=w_ffe, dfe_taps=w_dfe, sample_phase=peak % osr,
        eye_data=None, y_slicer=sc.y_slicer,
        extras={"phase_track": phase, "levels": levels, "main_cursor": main,
                "warmup": warm, "settle": settle, "train_end": train_end,
                "w_ffe0": np.asarray(w_ffe0), "w_dfe0": np.asarray(w_dfe0),
                "lane_ser": lane_ser, "adc": adc, "q_hist_head": q_hist[:8192],
                "jitter_budget": jitter_budget,
                "mlsd_resid": resid_ratios,
                # SER of the raw slicer, before the sequence detector — the
                # baseline the MLSD gain is measured against
                "ser_slicer": sc.ser_slicer,
                "precode": cfg.precode})
