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

import copy

import numpy as np

from ..afe import Ctle
from ..channel import ChannelModel
from ..channel.response import pulse_from_impulse
from ..config.schema import LinkConfig
from ..core.waveform import Waveform
from ..cdr.kernels import MsRxRun
from ..dsp import channel_cursors
from ..cdr.rx_clock import rx_clock_offsets_samples
from ..tx.pipeline import TxPipeline
from .lti import fft_filter, receiver_awgn
from .result import SimResult
from .scoring import (
    cdr_gains,
    score,
    training_schedule,
    warmup_symbols,
)
from .static_link import _levels, check_symbols, fold_eye, make_pattern


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
                     memory: int, n_target: int = 1) -> np.ndarray:
    """Postcursors left for the sequence detector, normalized to the main one.

    The FFE shapes the pulse (``n_target`` cursors from the main one on are
    its partial-response target) and the DFE cancels the next ``n_dfe``
    postcursors; whatever follows is the residual ISI the MLSD works over.
    """
    main = eq_cursors[eq_pre]
    start = eq_pre + n_target + n_dfe
    tail = eq_cursors[start: start + memory]
    if tail.size < memory:
        tail = np.pad(tail, (0, memory - tail.size))
    return tail / main


def _mlsd_post_detect(cfg: LinkConfig, y_slicer: np.ndarray, dec: np.ndarray,
                      levels: np.ndarray, resid: np.ndarray,
                      head: np.ndarray | None = None) -> np.ndarray:
    """Re-decide the symbol stream with the configured sequence detector.

    ``levels`` are the slicer levels in volts, so the trellis cursor vector is
    ``[1, r1, r2, ...]`` — the residual ratios scale those same volt levels --
    or, with a partial-response target, ``head`` (``[1, alpha]``) and then
    the residual. Returns ``dec`` unchanged when MLSD is off or there is
    nothing to detect over.
    """
    mcfg = cfg.rx.mlsd
    if mcfg.kind == "none":
        return dec
    from ..dsp.mlsd import post_detect

    cursors = np.concatenate([[1.0] if head is None else np.asarray(head, dtype=float),
                              np.asarray(resid, dtype=float)])
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


def _optical_stages(cfg: LinkConfig, channel: ChannelModel, tx_ami, rx_ami):
    """(stage-1, stage-2) impulses for an optical topology, else None.

    The cut is made in ``optical_stage`` so the statistical engine cuts in
    the same place. AMI models fold into one response or one waveform and
    have no notion of a node in the middle, so stage 1 declines them.
    """
    if getattr(channel, "optical", None) is None:
        return None
    if tx_ami is not None or rx_ami is not None:
        raise ValueError("AMI models are not supported together with an optical "
                         "topology (stage 1 has no AMI node between the segments)")
    from .optical_stage import split_impulses, split_impulses_nonlinear

    if channel.optical.curve is not None:
        return split_impulses_nonlinear(cfg, channel)
    return split_impulses(cfg, channel)


def _chain(stages) -> np.ndarray:
    """The small-signal impulse the stages make together (cursors, levels)."""
    h = stages[0]
    for s in stages[1:]:
        h = np.convolve(h, s)
    return h


def _optical_pass(tx_y: np.ndarray, stages, channel: ChannelModel,
                  rng: np.random.Generator) -> np.ndarray:
    """Tx waveform -> [drive -> E/O curve ->] photodiode node ->
    level-dependent noise -> receiver."""
    opt = channel.optical
    if len(stages) == 3:
        hd, ho, h2 = stages
        y_pd = fft_filter(opt.curve.apply(fft_filter(tx_y, hd), opt.drive_amplitude), ho)
    else:
        h1, h2 = stages
        y_pd = fft_filter(tx_y, h1)
    return fft_filter(opt.noise.inject(y_pd, rng), h2)


def _front_end(cfg: LinkConfig, channel: ChannelModel | None, tx_pipe: TxPipeline,
               line_symbols: np.ndarray, rng: np.random.Generator,
               tx_ami, rx_ami, xtalk, collect_jitter: bool):
    """Tx -> channel + CTLE + VGA -> [AMI] -> [crosstalk] -> noise.

    Returns ``(h, rx, jitter_budget)``: the impulse the receivers' pulse
    analysis works from and the received waveform -- an array, or with
    ``sim.stream`` a :class:`~halo_serdes.engine.stream.StreamRx` produced as
    the receiver consumes it. Shared by both receiver architectures.
    """
    osr = cfg.osr
    if cfg.sim.stream:
        v_sym = tx_pipe.symbol_stage(line_symbols)
        jit = tx_pipe.edge_offsets(v_sym, rng)
    else:
        tx_wave = tx_pipe.waveform(tx_pipe.symbol_stage(line_symbols), rng)

    if channel is None:
        channel = ChannelModel.from_config(cfg)
    if cfg.sim.stream:
        _check_streamable(channel, tx_ami, rx_ami, xtalk, collect_jitter)
    ch_rs = channel.response_set(cfg.dt)
    h = ch_rs.h.y
    if cfg.rx.ctle.enable:
        ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
        nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
        f = np.fft.rfftfreq(nfft, d=cfg.dt)
        h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f), nfft)[: h.size * 2]
    h = h * cfg.rx.vga_gain

    if cfg.sim.stream:
        from .stream import build_stream, skip_normals

        # the noise is the default engine's white draws (a copy of the
        # generator at that point reads them as the receiver goes), and the
        # link's generator skips past them, so the comparator offsets, the Rx
        # clock and the ADC draw what they draw without streaming
        noise_rng = None
        if cfg.rx.noise_rms > 0:
            noise_rng = copy.deepcopy(rng)
            skip_normals(rng, v_sym.size * osr)
        return h, build_stream(cfg, tx_pipe, v_sym, jit, h, noise_rng), None

    # optical topology: the response is the two stages' convolution, so it
    # lines up with the waveform that is built in two stages below
    stages = _optical_stages(cfg, channel, tx_ami, rx_ami)
    if stages is not None:
        h = _chain(stages)

    # --- optional AMI Tx/Rx models (Init folds into h, GetWave into the wave) ---
    h, tx_y = _apply_ami(cfg, h, tx_wave, tx_ami, rx_ami)

    rx_y = fft_filter(tx_y, h) if stages is None else _optical_pass(tx_y, stages, channel, rng)
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
        rx_y += receiver_awgn(rng, cfg.rx.noise_rms, rx_y.size, osr)
    return h, rx_y, jitter_budget


def _check_streamable(channel, tx_ami, rx_ami, xtalk, collect_jitter) -> None:
    """What needs the whole waveform at once cannot stream; say so instead
    of quietly falling back to the full-length engine."""
    why = []
    if tx_ami is not None or rx_ami is not None:
        why.append("IBIS-AMI models")
    if getattr(channel, "optical", None) is not None:
        why.append("an optical topology")
    if xtalk:
        why.append("crosstalk aggressors")
    if collect_jitter:
        why.append("the per-stage jitter budget (collect_jitter)")
    if why:
        raise ValueError("sim.stream does not support " + ", ".join(why)
                         + " yet: they work on the whole waveform; run with sim.stream=False")


def _run_rx(run, rx, cfg: LinkConfig, progress):
    """The receiver loop over a full waveform or a stream."""
    if isinstance(rx, np.ndarray):
        return _drive(run, cfg.sim.chunk_symbols, progress)
    from .stream import drive_stream

    return drive_stream(run, rx, cfg.sim.chunk_symbols, progress)


def _drive(run, chunk: int, progress):
    """Advance a receiver run ``chunk`` symbols at a time to its end.

    ``progress(done, total)`` is called after each chunk; whatever it raises
    stops the run there (that is how a caller cancels). The kernels carry
    their whole loop state across the calls, so the result does not depend
    on the chunk size.
    """
    step = max(int(chunk), 1)
    while not run.finished:
        run.advance(run.done + step)
        if progress is not None:
            progress(run.done, run.n_symbols)
    return run.result()


def run_time_link(cfg: LinkConfig, channel: ChannelModel | None = None,
                  collect_eye: bool = False,
                  collect_jitter: bool = False,
                  tx_ami=None, rx_ami=None, xtalk=None,
                  symbols: np.ndarray | None = None,
                  progress=None) -> SimResult:
    """``symbols``: the user symbol stream to send instead of ``sim.pattern``
    (symbol indices, one per UI). A retimer feeds one segment's decisions to
    the next this way; the stream enters the waveform domain only through
    ``tx/builder.py`` (invariant #5). With the pattern's own stream the
    result is identical to leaving it None.

    ``progress(done, total)``: called between receiver chunks of
    ``sim.chunk_symbols`` symbols; an exception it raises abandons the run
    (cancellation). The chunk size does not change the result."""
    if cfg.rx.arch == "adc_dsp":
        return _run_adc_link(cfg, channel, collect_eye, collect_jitter,
                             tx_ami, rx_ami, xtalk, symbols, progress)
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
    if cfg.numeric.mode == "fixed":
        raise ValueError("numeric.mode='fixed' is the ADC receiver's digital back end; "
                         "the mixed-signal receiver's DFE and CDR are analog (rx.arch='adc_dsp')")
    rng = np.random.default_rng(cfg.sim.seed)
    osr = cfg.osr

    # --- pattern, Tx (optionally 1/(1+D) precoded; FIR + jittered edges) ---
    symbols = make_pattern(cfg) if symbols is None else check_symbols(cfg, symbols)
    line_symbols = _tx_symbols(cfg, symbols)
    tx_pipe = TxPipeline.from_config(cfg)
    h, rx_y, jitter_budget = _front_end(cfg, channel, tx_pipe, line_symbols, rng,
                                        tx_ami, rx_ami, xtalk, collect_jitter)
    n_wave = rx_y.size if isinstance(rx_y, np.ndarray) else rx_y.n_total

    # --- pulse-response analysis: main cursor, initial phase, initial DFE taps ---
    # the pulse the receiver sees includes the Tx FFE; ``lead`` maps its peak
    # back onto the received waveform (TxPipeline.receiver_view)
    h_rx, lead = tx_pipe.receiver_view(h)
    pulse = pulse_from_impulse(Waveform(h_rx, cfg.dt), osr)
    peak_rx = int(np.argmax(np.abs(pulse.y)))
    peak = peak_rx - lead
    n_dfe = cfg.rx.dfe.n_taps
    # take enough postcursors for the DFE *and* the residual an MLSD works over
    n_post_c = max(n_dfe, 1) + (cfg.rx.mlsd.memory if cfg.rx.mlsd.kind != "none"
                                else 0)
    cursors = channel_cursors(pulse, osr, 0, n_post_c, peak_idx=peak_rx)
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
    n_sym_max = (n_wave - peak - 4 * osr) // osr - 2
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

    # the receiver's own clock, drawn last so an ideal clock changes nothing
    rx_clk = rx_clock_offsets_samples(n_sym, cfg, rng)

    if collect_eye and not isinstance(rx_y, np.ndarray):
        rx_y.keep_head(int(phase0) % osr + osr + 2000 * 2 * osr)
    dec, y_sum, phase, w_dfe, pd_hist, w_dfe_hist = _run_rx(MsRxRun(
        rx_y if isinstance(rx_y, np.ndarray) else np.zeros(0), osr, float(peak), n_sym,
        levels.astype(np.float64), np.asarray(w_dfe0, dtype=np.float64),
        float(mu), LMS_BATCH_SYMBOLS, float(kp), float(ki), float(clamp),
        float(sum_alpha), sched.reference, int(train_end), int(settle),
        int(tap1_unrolled), branch_off, rx_clk), rx_y, cfg, progress)

    n_run = dec.size
    # --- optional MLSD over the postcursors the DFE left behind ---
    resid_ratios = _residual_ratios(cursors, 0, n_dfe, cfg.rx.mlsd.memory)
    dec_slicer = dec
    dec = _mlsd_post_detect(cfg, y_sum[:n_run], dec, levels, resid_ratios)

    warm = warmup_symbols(cfg, train_end, n_run)
    sc = score(cfg, dec=dec, dec_slicer=dec_slicer, y_slicer=y_sum,
               levels=levels, line_idx=ref_idx, user_idx=user_idx,
               n_run=n_run, warmup=warm)

    eye = None
    if collect_eye:
        eye = fold_eye(rx_y if isinstance(rx_y, np.ndarray) else rx_y.head, osr,
                       int(phase0) % osr, n_traces=2000)

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
                "precode": cfg.precode,
                "cycle_slips": sc.cycle_slips, "slip_at": sc.slip_at,
                "decisions": sc.decisions})


def _fixed_back_end(cfg, rx_y, pos0, n_sym, levels, adc, noise, rx_clk, w_ffe0, w_dfe0,
                    sched, pr_args):
    """``numeric.mode == 'fixed'``: the ADC receiver's digital back end --
    FFE, DFE, slicer, LMS and the CDR loop -- in int64 (``dsp/fixed_loop.py``).

    Returns what the float kernel's result unpacks to (decisions, slicer
    values in volts, sample positions, lanes, sampled values) plus the run's
    record for the RTL lockstep."""
    if not isinstance(rx_y, np.ndarray):
        raise ValueError("numeric.mode='fixed' runs the loop a second time over the "
                         "waveform; it cannot with sim.stream")
    from ..dsp.fixed_loop import build_fixed_loop, run_fixed_loop

    # from the float run's starting weights, training and adapting itself
    # (integer LMS) on the float run's schedule
    fl = build_fixed_loop(cfg, w_ffe0, w_dfe0, levels, adc.q_step,
                          train_len=int(sched.train_end), adapt_start=int(sched.settle),
                          **pr_args)
    rec = run_fixed_loop(fl, rx_y, cfg.osr, pos0, n_sym, adc, noise, rx_clk,
                         ref=sched.reference)
    n = rec["xin"].size
    # the score below trims the last n_pre symbols (the FFE never decided
    # them), so hand it the decisions padded to the symbols run
    dec = np.zeros(n, dtype=np.int64)
    dec[: rec["n_dec"]] = rec["dec"]
    y_sl = np.zeros(n)
    y_sl[: rec["n_dec"]] = rec["v_out"] * fl.out_lsb
    lane_of = np.arange(n, dtype=np.int64) % adc.n_lanes
    return (dec, y_sl, rec["phase"], lane_of, rec["xin"] * fl.in_lsb,
            {"loop": fl, "record": rec})


def _run_adc_link(cfg: LinkConfig, channel: ChannelModel | None = None,
                  collect_eye: bool = False,
                  collect_jitter: bool = False,
                  tx_ami=None, rx_ami=None, xtalk=None,
                  symbols: np.ndarray | None = None, progress=None) -> SimResult:
    """ADC-based RX: light CTLE -> TI-ADC -> digital FFE/DFE -> MM-CDR.

    Primary metrics for this architecture are slicer-input SNR and SER
    (post-EQ eye information is low); BER via the same checkers.
    """
    from ..afe.adc import TiAdc
    from ..cdr.adc_kernel import AdcRxRun
    from ..dsp import mmse_ffe, mmse_pr_target
    from ..dsp.ffe import equalized_cursors

    rng = np.random.default_rng(cfg.sim.seed)
    osr = cfg.osr

    # --- pattern, Tx (optionally 1/(1+D) precoded) ---
    symbols = make_pattern(cfg) if symbols is None else check_symbols(cfg, symbols)
    line_symbols = _tx_symbols(cfg, symbols)
    tx_pipe = TxPipeline.from_config(cfg)
    h, rx_y, jitter_budget = _front_end(cfg, channel, tx_pipe, line_symbols, rng,
                                        tx_ami, rx_ami, xtalk, collect_jitter)
    n_wave = rx_y.size if isinstance(rx_y, np.ndarray) else rx_y.n_total

    # --- pulse analysis: initial FFE (MMSE), DFE, slicer levels ---
    # (the Tx FFE is part of the pulse the receiver equalises; see run_time_link)
    h_rx, lead = tx_pipe.receiver_view(h)
    pulse = pulse_from_impulse(Waveform(h_rx, cfg.dt), osr)
    peak_rx = int(np.argmax(np.abs(pulse.y)))
    if tx_pipe.pr_taps is not None:
        # a 1 + aD Tx pulse peaks on either of its two cursors; x_k's own is
        # where the unshaped pulse peaks
        h_un, _ = tx_pipe.receiver_view(h, shaping=False)
        peak_rx = int(np.argmax(np.abs(pulse_from_impulse(Waveform(h_un, cfg.dt), osr).y)))
    peak = peak_rx - lead
    fcfg = cfg.rx.ffe
    n_pre_c, n_post_c = fcfg.n_pre + 4, fcfg.n_post + 12
    cursors = channel_cursors(pulse, osr, n_pre_c, n_post_c, peak_idx=peak_rx)
    n_taps = fcfg.n_pre + 1 + fcfg.n_post
    pr = cfg.pr
    n_t = len(pr.target)
    target = tuple(pr.target) if pr.active else None
    if pr.active and pr.adapt != "none":
        # the monic MMSE target for this pulse, against the noise the FFE
        # sees: the receiver's and the ADC's (ENOB, or bare quantisation)
        acfg = cfg.rx.adc
        bits = acfg.enob if acfg.enob is not None else acfg.n_bits
        adc_var = acfg.fullscale ** 2 / 12.0 * 2.0 ** (-2.0 * bits)
        target = mmse_pr_target(cursors, n_pre_c, n_taps, fcfg.n_pre,
                                noise_var=cfg.rx.noise_rms ** 2 + adc_var,
                                symbol_power=float(np.mean(_levels(cfg) ** 2)), n_target=n_t)
        target = target + (0.0,) * (n_t - len(target))
    alpha = target[1] if pr.active else 0.0
    beta = target[2] if (pr.active and n_t == 3) else 0.0
    w_ffe0 = mmse_ffe(cursors, n_pre_c, n_taps, fcfg.n_pre,
                      noise_var=cfg.rx.noise_rms ** 2, target=target)
    eq_cursors, eq_pre = equalized_cursors(cursors, w_ffe0, n_pre_c, fcfg.n_pre)
    main = eq_cursors[eq_pre]
    n_dfe = cfg.rx.dfe.n_taps
    # the DFE cancels what follows the target's controlled cursors
    w_dfe0 = (eq_cursors[eq_pre + n_t: eq_pre + n_t + n_dfe] / main
              if n_dfe else np.zeros(0))
    levels = _levels(cfg) * abs(main)
    pr_mode, pr_levels, pd_offset = 0, np.zeros(1), float(cfg.rx.cdr.pd_offset)
    if pr.active:
        if cfg.rx.mlsd.kind == "none":
            import warnings

            warnings.warn(
                f"pr.target {tuple(pr.target)} without a sequence detector (rx.mlsd.kind "
                "'none'): the controlled cursor is only cancelled by decision feedback, "
                "which costs what the target saved -- set rx.mlsd.kind", stacklevel=2)
        # precoded 1 + D slices the composite levels and stops error
        # propagation; any other alpha subtracts it from the previous decision
        pr_mode = 2 if (cfg.precode and n_t == 2 and alpha == 1.0 and pr.adapt == "none") else 1
        if pr_mode == 2:
            n_lv = levels.size
            pr_levels = np.array([np.mean([levels[i] + levels[q - i]
                                           for i in range(max(0, q - n_lv + 1), min(q, n_lv - 1) + 1)])
                                  for q in range(2 * n_lv - 1)])

    # --- TI-ADC ---
    adc = TiAdc(cfg.rx.adc, osr, rng)
    delay = peak // osr
    n_sym_max = (n_wave - peak - (fcfg.n_pre + 6) * osr) // osr - 2
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

    # the receiver's own clock, drawn last so an ideal clock changes nothing
    rx_clk = rx_clock_offsets_samples(n_sym, cfg, rng)
    mu_a = pr.mu if (pr.active and pr.adapt == "lms") else 0.0
    alpha_out = np.array([alpha, beta], dtype=np.float64)

    dec, y_sl, phase, w_ffe, w_dfe, lane_of, q_hist = _run_rx(AdcRxRun(
        rx_y if isinstance(rx_y, np.ndarray) else np.zeros(0), osr, float(peak), n_sym,
        levels.astype(np.float64),
        adc.n_lanes, adc.offsets, adc.gains, adc.skews,
        adc.q_step, adc.code_max, enob_noise,
        np.asarray(w_ffe0, dtype=np.float64), fcfg.n_pre, float(mu_f),
        np.asarray(w_dfe0, dtype=np.float64), float(mu_d),
        float(kp), float(ki), float(clamp), pd_offset,
        1 if cfg.mm_pd_input == "ffe" else 0, lat_blocks,
        sched.reference, int(train_end), int(settle), rx_clk,
        float(alpha), int(pr_mode), np.asarray(pr_levels, dtype=np.float64),
        float(mu_a), alpha_out, float(beta), int(n_t if pr.active else 1)),
        rx_y, cfg, progress)

    fixed = None
    if cfg.numeric.mode == "fixed":
        # the float run trained the equaliser; the bit-true back end takes its
        # weights, frozen, and runs the whole loop again from the same start
        dec, y_sl, phase, lane_of, q_hist, fixed = _fixed_back_end(
            cfg, rx_y, float(peak), n_sym, levels, adc, enob_noise, rx_clk, w_ffe0, w_dfe0,
            sched, {"pr_mode": int(pr_mode), "pr_nt": int(n_t if pr.active else 1),
                    "alpha": float(alpha), "beta": float(beta), "pr_levels": pr_levels,
                    "mu_alpha": float(mu_a)})
        if pr.active:
            # the controlled cursor the bit-true loop ended on
            ab = fixed["record"]["ab"] * 2.0 ** -cfg.numeric.dfe_weight.fl
            alpha_out[0] = ab[0]
            if alpha_out.size > 1:
                alpha_out[1] = ab[1]
        # the weights the bit-true loop ended on, in float units
        w_ffe = fixed["record"]["wf"] * 2.0 ** -cfg.numeric.ffe_weight.fl
        w_dfe = fixed["record"]["wd"] * 2.0 ** -cfg.numeric.dfe_weight.fl

    # The FFE emits symbol k - n_pre at ADC sample k, so the kernel's last
    # n_pre decisions were never made (they hold the array's initial 0). They
    # used to be scored: a fixed tail of wrong symbols, 3/(2N) in BER.
    n_run = max(dec.size - fcfg.n_pre, 0)
    dec = dec[:n_run]
    # --- optional MLSD over the residual the FFE/DFE left behind ---
    eq_final, eq_pre_f = equalized_cursors(cursors, w_ffe, n_pre_c, fcfg.n_pre)
    resid_ratios = _residual_ratios(eq_final, eq_pre_f, n_dfe,
                                    cfg.rx.mlsd.memory, n_t)
    # the trellis takes the controlled cursor as the FFE actually shaped it
    head = (eq_final[eq_pre_f: eq_pre_f + n_t] / eq_final[eq_pre_f]
            if pr.active else None)
    dec_slicer = dec
    if fixed is not None and cfg.rx.mlsd.kind == "sliding":
        # the bit-true detector on the loop's slicer words (dsp/fixed_mlsd.py)
        from ..dsp.fixed_mlsd import build_fixed_sliding, run_fixed_sliding

        fl, rec = fixed["loop"], fixed["record"]
        # the float detector's residual is the first cursor after the main
        # one -- the target's own under PR -- and it starts from a plain slice
        rp = float(head[1]) if head is not None and len(head) > 1 else float(resid_ratios[0])
        fsd = build_fixed_sliding(cfg, rp, fl.levels_out, fl.out_lsb, fl.out_bits)
        v_fx = rec["v_out"][:n_run]
        dec0 = np.argmin(np.abs(v_fx[:, None] - fl.levels_out[None, :]), axis=1)
        dec = run_fixed_sliding(fsd, v_fx, dec0, fl.levels_out)
        fixed["mlsd"] = {"detector": fsd, "dec": dec}
    elif fixed is not None and cfg.rx.mlsd.kind == "viterbi":
        # the bit-true Viterbi on the loop's slicer words (dsp/fixed_viterbi.py),
        # over the trellis the float one would use
        from ..dsp.fixed_viterbi import build_fixed_viterbi, run_fixed_viterbi

        fl, rec = fixed["loop"], fixed["record"]
        cursors = np.concatenate([[1.0] if head is None else np.asarray(head, dtype=float),
                                  np.asarray(resid_ratios, dtype=float)])
        if not np.all(np.abs(cursors[1:]) < 1e-9):
            fvd = build_fixed_viterbi(cfg, cursors, fl.levels_out, fl.out_bits)
            dec = run_fixed_viterbi(fvd, rec["v_out"][:n_run], fl.levels_out.size)
            fixed["mlsd"] = {"detector": fvd, "dec": dec}
    else:
        dec = _mlsd_post_detect(cfg, y_sl[:n_run], dec, levels, resid_ratios, head)

    warm = warmup_symbols(cfg, train_end, n_run)
    # score() undoes the precoder first: the slicer decided line symbols
    sc = score(cfg, dec=dec, dec_slicer=dec_slicer, y_slicer=y_sl,
               levels=levels, line_idx=ref_idx, user_idx=user_idx,
               n_run=n_run, warmup=warm, pr_alpha=float(alpha_out[0]),
               pr_beta=float(alpha_out[1]) if n_t == 3 else 0.0)
    dec_c, ref_c = sc.decisions, sc.reference

    # per-lane SER (TI mismatch diagnostics)
    lane_c = lane_of[warm:n_run]
    if sc.cycle_slips:
        # the few decisions a slip left without a reference are not lane errors
        keep = ref_c >= 0
        dec_c, ref_c, lane_c = dec_c[keep], ref_c[keep], lane_c[keep]
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
                "precode": cfg.precode,
                # (start, end) of the controlled cursor a: equal unless pr.adapt is "lms"
                "pr_alpha": (alpha, float(alpha_out[0])) if pr.active else None,
                "pr_target": (1.0, float(alpha_out[0])) + ((float(alpha_out[1]),) if n_t == 3 else ())
                if pr.active else None,
                "cycle_slips": sc.cycle_slips, "slip_at": sc.slip_at,
                "decisions": sc.decisions,
                # numeric.mode 'fixed': the bit-true loop and its record
                # (dsp/fixed_loop.py; rtl/adc_dsp_loop.sv replays it)
                "fixed": fixed})
