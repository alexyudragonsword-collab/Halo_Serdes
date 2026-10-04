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

This module held no numerical decisions of its own when it was split out: every
expression was moved here verbatim, and the refactor was gated on the engine
outputs being bit-for-bit unchanged across all nine presets. The one it has
gained since is :func:`find_slips` (whole-UI CDR slips are scored around, not
read as BER 0.5); a run without a slip still scores exactly as before.
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


#: Window the slip search counts errors over. Long enough that a misaligned
#: window (errors at chance, a half of NRZ symbols and three quarters of PAM4)
#: cannot be mistaken for a bad aligned one, short enough to bracket a slip.
SLIP_WINDOW = 512

#: How far from the current offset a window looks, in UI either way. The
#: offset itself is unbounded: a loop that keeps slipping walks it along.
SLIP_MAX_STEP = 4


def find_slips(dec: np.ndarray, ref: np.ndarray, start: int) -> np.ndarray | None:
    """Per-decision offset ``s`` with ``dec[i]`` matching ``ref[start + i + s[i]]``.

    A loop that slips a whole UI goes on deciding correctly, one symbol off;
    against a fixed alignment every later decision reads as chance and the run
    reports BER ~0.5. A real link's FEC framer finds the frame again, so the
    model should too: count the slip, score each segment at its own offset.

    Returns None when no window strays from offset 0 (the common case, which
    then scores exactly as before). An offset is only taken over when the
    current one is near chance (> 1/4 of a window wrong) and the new one has
    less than half its errors: a merely bad aligned link sits at its own SER
    under offset 0 and at chance under every other, so it never switches.
    """
    n, w = dec.size, SLIP_WINDOW
    n_w = n // w
    if n_w < 2:
        return None
    pos = start + np.arange(n)

    def miss(shift: int, a: int, b: int) -> np.ndarray:
        j = pos[a:b] + shift
        ok = (j >= 0) & (j < ref.size)
        out = ~ok
        out[ok] = dec[a:b][ok] != ref[j[ok]]
        return out

    if not np.any(miss(0, 0, n_w * w).reshape(n_w, w).sum(axis=1) > w // 4):
        return None

    bounds, offs = [0], [0]
    for k in range(n_w):
        cur = offs[-1]
        a, b = k * w, (k + 1) * w
        e_cur = int(miss(cur, a, b).sum())
        if e_cur <= w // 4:
            continue
        cands = [cur + d for d in range(-SLIP_MAX_STEP, SLIP_MAX_STEP + 1) if d]
        errs = [int(miss(c, a, b).sum()) for c in cands]
        best = cands[int(np.argmin(errs))]
        if 2 * min(errs) >= e_cur:
            continue
        # the slip sits in this window or the one before: put the boundary
        # where the old offset's errors before it plus the new one's after it
        # are fewest
        lo = max(a - w, bounds[-1])
        before = np.concatenate([[0], np.cumsum(miss(cur, lo, b))])
        after = np.concatenate([np.cumsum(miss(best, lo, b)[::-1])[::-1], [0]])
        bounds.append(lo + int(np.argmin(before + after)))
        offs.append(best)
    if len(offs) == 1:
        return None
    off = np.empty(n, dtype=np.int64)
    for b0, b1, o in zip(bounds, bounds[1:] + [n], offs):
        off[b0:b1] = o
    return off


@dataclass(frozen=True)
class Score:
    """Everything scoring produces, for all three engines."""

    ber: BerResult
    ser: float
    snr_db: float
    ser_slicer: float
    warmup: int
    decisions: np.ndarray       # user-domain decisions, as the receiver produced them
    reference: np.ndarray       # what each was scored against; -1 where a slip left none
    y_slicer: np.ndarray        # capped slicer-input trace, for diagnostics
    cycle_slips: int = 0        # whole-UI slips found and scored around
    slip_at: tuple = ()         # where each took effect, in scored-decision index

    @property
    def n_scored(self) -> int:
        return int(self.decisions.size)


def score(cfg: LinkConfig, *, dec: np.ndarray, dec_slicer: np.ndarray,
          y_slicer: np.ndarray, levels: np.ndarray, line_idx: np.ndarray,
          user_idx: np.ndarray, n_run: int, warmup: int,
          pr_alpha: float = 0.0, pr_beta: float = 0.0) -> Score:
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
    pr_alpha
        A receive-side 1 + aD target: the slicer input is then expected to
        carry ``pr_alpha`` times the previous symbol too, and SNR is taken
        against that composite.
    pr_beta
        The second controlled cursor of a 1 + aD + bD^2 target, likewise.
    """
    dec_c = user_decisions(cfg, dec)[warmup:n_run]
    off = find_slips(dec_c, user_idx, warmup)
    if off is None:
        j = None
        ref_c = user_idx[warmup:n_run]
        dec_s, ref_s = dec_c, ref_c
    else:
        j = warmup + np.arange(dec_c.size) + off
        ok = (j >= 0) & (j < user_idx.size)
        ref_c = np.full(dec_c.size, -1, dtype=user_idx.dtype)
        ref_c[ok] = user_idx[j[ok]]
        dec_s, ref_s = dec_c[ok], ref_c[ok]

    ser = float(np.mean(dec_s != ref_s))
    if cfg.modulation == "pam4":
        # Gray-coded PAM4: one symbol error is not one bit error, so BER comes
        # from the checker rather than from scaling SER.
        ber = prbs_mod.symbol_checker(ref_s, dec_s, gray=True)
    else:
        idx = np.nonzero(dec_s != ref_s)[0]
        ber = BerResult(n_checked=dec_s.size, n_errors=idx.size, error_idx=idx)

    # SNR is a slicer-input metric, so the reference is the *line* symbols the
    # slicer saw — not the user symbols BER is scored on.
    #
    # Imported here, not at module scope: analysis/__init__ pulls in com.py,
    # which imports engine.statistical, so a top-level import would close an
    # engine -> analysis -> engine cycle. timedomain.py already reaches for
    # analysis.jitter the same way.
    from ..analysis.metrics import slicer_snr_db

    if j is None:
        ideal = levels[line_idx[warmup:n_run]]
        for lag, c in ((1, pr_alpha), (2, pr_beta)):
            if c:
                prev = line_idx[max(warmup - lag, 0):n_run - lag]
                if warmup < lag:
                    prev = np.concatenate([np.full(lag - warmup, line_idx[0]), prev])
                ideal = ideal + c * levels[prev]
        snr_db = slicer_snr_db(y_slicer[warmup:n_run], ideal)
        ser_slicer = float(np.mean(
            user_decisions(cfg, dec_slicer)[warmup:n_run] != ref_c))
        slips, slip_at = 0, ()
    else:
        jk = j[ok]
        ideal = levels[line_idx[jk]]
        for lag, c in ((1, pr_alpha), (2, pr_beta)):
            if c:
                ideal = ideal + c * levels[line_idx[np.maximum(jk - lag, 0)]]
        snr_db = slicer_snr_db(y_slicer[warmup:n_run][ok], ideal)
        ser_slicer = float(np.mean(
            user_decisions(cfg, dec_slicer)[warmup:n_run][ok] != ref_s))
        # an offset already non-zero at the first scored symbol slipped
        # during training and counts too
        change = np.nonzero(np.diff(np.concatenate([[0], off])))[0]
        slips, slip_at = int(change.size), tuple(int(i) for i in change)

    return Score(
        ber=ber, ser=ser, snr_db=snr_db, ser_slicer=ser_slicer,
        warmup=warmup, decisions=dec_c, reference=ref_c,
        y_slicer=y_slicer[warmup: warmup + SLICER_CAPTURE_SYMBOLS],
        cycle_slips=slips, slip_at=slip_at)
