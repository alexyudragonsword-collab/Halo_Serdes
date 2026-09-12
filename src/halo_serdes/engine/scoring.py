"""Decision scoring shared by every time-domain receiver path.

Why this module exists
----------------------
Project invariant #2 says the two receiver architectures must stay *fairly
comparable*: differences are allowed only inside the two assembly functions and
each architecture's own blocks. Scoring is not one of those differences — and
yet it had been written out twice, some thirty-five near-identical lines apiece,
once in :func:`~halo_serdes.engine.timedomain.run_time_link` and again in
``_run_adc_link``. Two copies of a fairness-critical calculation is a slow leak:
a correction applied to one path leaves the other reporting on a different
basis, and nothing about the result says so. The same drift had already
happened to the slicer-SNR formula, which existed as three inlined copies
beside the canonical :func:`halo_serdes.analysis.metrics.slicer_snr_db`.

What belongs here is therefore anything decided *identically* by every
receiver: the training schedule, the CDR loop gains, and the scoring itself.
Everything here is deliberately decision-domain only — it takes the symbols a
slicer produced and the reference it should have produced, and knows nothing
about equalisers, sampling or architecture. That is exactly what lets all three
engines (mixed-signal, ADC-based, and the static link) share it.

This module holds no numerical decisions of its own: every expression was moved
here verbatim, and the refactor was gated on the engine outputs being bit-for-bit
unchanged across all nine presets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config.schema import LinkConfig
from ..core import prbs as prbs_mod
from ..core.prbs import BerResult

#: How much of the slicer-input waveform to hand back for diagnostics.
#:
#: A cap rather than the whole run because this rides inside every
#: :class:`~halo_serdes.engine.result.SimResult` — including the ones crossing
#: the JSON facade to Android — and a 500k-symbol run does not need to carry a
#: 500k-sample trace to draw a histogram from.
SLICER_CAPTURE_SYMBOLS = 20_000


@dataclass(frozen=True)
class TrainingSchedule:
    """When the receiver stops being told the answer.

    ``reference`` is the data-aided training vector the kernels take: real
    symbol indices up to ``train_end`` and ``-1`` afterwards, which is how the
    kernels are told to switch to decision-directed tracking.
    """

    settle: int
    train_end: int
    reference: np.ndarray


def training_schedule(cfg: LinkConfig, n_sym: int,
                      line_idx: np.ndarray) -> TrainingSchedule:
    """Staged startup: CDR settling, then data-aided LMS, then tracking.

    Both stages are clamped to a quarter of the run each, so a short sim
    degrades into "mostly tracking" instead of never leaving training.
    """
    settle = min(cfg.sim.cdr_settle, n_sym // 4)
    train = min(cfg.sim.train_symbols, n_sym // 4)
    train_end = settle + train
    reference = np.full(n_sym, -1, dtype=np.int64)
    reference[:train_end] = line_idx[:train_end]
    return TrainingSchedule(settle=settle, train_end=train_end,
                            reference=reference)


def cdr_gains(cdr_cfg, osr: int) -> tuple[float, float, float]:
    """Proportional/integral gains and phase clamp, in sample units.

    The config states them as hardware shift amounts (that is the point of
    ``kp_shift``/``ki_shift`` — they are barrel-shifter widths in the eventual
    RTL); the kernels want samples, hence the ``osr`` scaling here rather than
    in two kernels independently.
    """
    kp = osr * 2.0 ** (-cdr_cfg.kp_shift)
    ki = osr * 2.0 ** (-cdr_cfg.ki_shift)
    clamp = cdr_cfg.clamp * osr if cdr_cfg.clamp else 0.0
    return float(kp), float(ki), float(clamp)


def user_decisions(cfg: LinkConfig, dec: np.ndarray) -> np.ndarray:
    """Undo the precoder, mapping slicer decisions back to user symbols.

    With ``cfg.precode`` the slicer decides *line* symbols, so BER must be
    scored one transform later. Keeping this beside the scoring it feeds is
    what stops a path from scoring precoded symbols against user references.
    """
    if not cfg.precode:
        return dec
    from ..core.mapping import unprecode_1plusd

    return unprecode_1plusd(dec, 2 ** cfg.bits_per_symbol)


def warmup_symbols(cfg: LinkConfig, train_end: int, n_run: int) -> int:
    """Symbols discarded before scoring starts.

    Defaults to the end of training: an adapting equaliser has not converged
    before then, and counting those errors reports the startup transient as a
    link property.
    """
    warm = (cfg.sim.warmup_discard if cfg.sim.warmup_discard is not None
            else train_end)
    return min(warm, n_run - 1)


@dataclass(frozen=True)
class Score:
    """Everything scoring produces, for all three engines."""

    ber: BerResult
    ser: float
    snr_db: float
    ser_slicer: float
    warmup: int
    decisions: np.ndarray       # user-domain decisions actually scored
    reference: np.ndarray       # user-domain references they were scored against
    y_slicer: np.ndarray        # capped slicer-input trace, for diagnostics

    @property
    def n_scored(self) -> int:
        return int(self.decisions.size)


def score(cfg: LinkConfig, *, dec: np.ndarray, dec_slicer: np.ndarray,
          y_slicer: np.ndarray, levels: np.ndarray, line_idx: np.ndarray,
          user_idx: np.ndarray, n_run: int, warmup: int) -> Score:
    """Score one receiver's decisions.

    Parameters
    ----------
    dec
        Final decisions, after any sequence detector.
    dec_slicer
        Decisions straight from the slicer, before the sequence detector. Pass
        ``dec`` when there is none; ``ser_slicer`` is then the baseline the
        MLSD gain is measured against.
    y_slicer
        Slicer-input samples, one per symbol, in volts.
    levels
        Slicer levels in volts, indexed by *line* symbol.
    line_idx, user_idx
        What the slicer should have decided, and what BER is scored against.
        These differ exactly when ``cfg.precode`` is set.
    n_run
        Symbols the kernel actually produced (``dec.size``); can be shorter
        than the request when the waveform ran out.
    warmup
        Symbols to discard first — see :func:`warmup_symbols`. The static link
        passes 0: it has no adaptation transient to wait out.
    """
    dec_c = user_decisions(cfg, dec)[warmup:n_run]
    ref_c = user_idx[warmup:n_run]

    ser = float(np.mean(dec_c != ref_c))
    if cfg.modulation == "pam4":
        # Gray-coded PAM4: one symbol error is not one bit error, so BER comes
        # from the checker rather than from scaling SER.
        ber = prbs_mod.symbol_checker(ref_c, dec_c, gray=True)
    else:
        idx = np.nonzero(dec_c != ref_c)[0]
        ber = BerResult(n_checked=dec_c.size, n_errors=idx.size, error_idx=idx)

    # SNR is a slicer-input metric, so the reference is the *line* symbols the
    # slicer saw — not the user symbols BER is scored on.
    #
    # Imported here, not at module scope: analysis/__init__ pulls in com.py,
    # which imports engine.statistical, so a top-level import would close an
    # engine -> analysis -> engine cycle. timedomain.py already reaches for
    # analysis.jitter the same way.
    from ..analysis.metrics import slicer_snr_db

    ideal = levels[line_idx[warmup:n_run]]
    snr_db = slicer_snr_db(y_slicer[warmup:n_run], ideal)

    ser_slicer = float(np.mean(
        user_decisions(cfg, dec_slicer)[warmup:n_run] != ref_c))

    return Score(
        ber=ber, ser=ser, snr_db=snr_db, ser_slicer=ser_slicer,
        warmup=warmup, decisions=dec_c, reference=ref_c,
        y_slicer=y_slicer[warmup: warmup + SLICER_CAPTURE_SYMBOLS])
