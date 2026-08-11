"""Single-source-of-truth configuration schema.

Every bit width, tap count, and loop gain in the framework appears exactly once,
here, and is loaded from YAML. RTL parameter packages can later be generated
from the same data (``to_sv_package`` hook, Phase 6).

All dataclasses are frozen: a loaded LinkConfig is immutable; derive variants
with ``dataclasses.replace``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Product mixed-signal envelope (CTLE + DFE + BB-CDR, no RX FFE) — three
# tiers, calibrated by the limit sweep on the Whisper reference backplane
# (examples/12; closure loss ~-30 dB NRZ / ~-20.5 dB PAM4, the 9.5 dB gap
# being the PAM4 level penalty):
#
#   default anchor : 16 GBd (canonical validated configs, ample margin)
#   comfort zone   : NRZ <= 24 Gb/s, PAM4 <= 32 Gb/s
#                    (post-DFE eye >= ~25% of level spacing on Whisper)
#   absolute limit : NRZ <= 30 Gb/s, PAM4 <= 36 Gb/s
#                    (last-open points; a few-mV eye — expect FEC territory)
#   hard ceiling   : 30 GBd symbol rate regardless of modulation — proxy for
#                    the circuit walls this behavioral model does NOT capture
#                    (CTLE gain-bandwidth, slicer aperture, clock path), which
#                    are what stop real mixed-signal designs near ~30 GBd.
#
# comfort < rate <= limit: the time engine warns "marginal"; beyond the limit
# or ceiling it warns "exceeds" — ADC/DSP is the supported path there.
MS_DEFAULT_BAUD = 16.0e9
MS_COMFORT_DATA_RATE: dict[str, float] = {"nrz": 24.0e9, "pam4": 32.0e9}
MS_LIMIT_DATA_RATE: dict[str, float] = {"nrz": 30.0e9, "pam4": 36.0e9}
MS_HARD_MAX_BAUD = 30.0e9


def _require(cond: bool, msg: str) -> None:
    """Reject an invalid config at construction.

    The config is the single source of truth for every module, so a bad field
    must fail here with a precise message rather than surfacing as a deep
    ZeroDivisionError — or, worse, silently wrong physics (a negative ``osr``
    yields a negative ``dt`` and reversed time).
    """
    if not cond:
        raise ValueError(msg)


def _require_in(value, allowed, field: str) -> None:
    _require(value in allowed,
             f"{field} must be one of {sorted(allowed)}, got {value!r}")


@dataclass(frozen=True)
class QFormat:
    """Fixed-point format: signed two's-complement, ``wl`` total bits with
    ``fl`` fractional bits. Rounding/saturation semantics match RTL ``>>>``
    conventions (Phase 6)."""

    wl: int
    fl: int
    signed: bool = True
    rounding: Literal["floor", "round"] = "round"
    saturate: bool = True

    def __post_init__(self):
        _require(self.wl >= 1, f"QFormat.wl must be >= 1, got {self.wl}")
        _require(self.fl >= 0, f"QFormat.fl must be >= 0, got {self.fl}")
        _require_in(self.rounding, {"floor", "round"}, "QFormat.rounding")


@dataclass(frozen=True)
class ChannelConfig:
    kind: Literal["touchstone", "analytic"] = "touchstone"
    file: Optional[str] = None          # Touchstone path (s2p/s4p/s8p/s12p)
    lane: int = 0                       # lane index for 8/12-port files
    renumber: bool = True               # auto-detect 1<->3 port ordering
    zs_diff: float = 100.0              # differential source termination [ohm]
    zl_diff: float = 100.0              # differential load termination [ohm]
    f_max: Optional[float] = None       # analysis grid max frequency [Hz]; default 2/UI
    n_freq: int = 4096                  # points on the uniform frequency grid (incl. DC)
    # analytic (RLGC) channel parameters, used when kind == "analytic"
    length_m: float = 0.2
    rdc: float = 0.0
    r_skin: float = 0.0                 # R(f) = rdc + r_skin*sqrt(f)
    l_per_m: float = 3.0e-7
    g_per_m: float = 0.0
    c_per_m: float = 1.2e-10
    loss_tangent: float = 0.0

    def __post_init__(self):
        _require_in(self.kind, {"touchstone", "analytic"}, "channel.kind")
        _require(self.n_freq >= 2,
                 f"channel.n_freq must be >= 2, got {self.n_freq}")
        _require(self.length_m >= 0.0,
                 f"channel.length_m must be >= 0, got {self.length_m}")
        _require(self.f_max is None or self.f_max > 0,
                 f"channel.f_max must be > 0 when set, got {self.f_max}")


@dataclass(frozen=True)
class TxConfig:
    fir_taps: tuple[float, ...] = (1.0,)   # main cursor inferred: last-but-pre convention set in Phase 1
    fir_n_pre: int = 0
    swing: float = 1.0                     # differential peak-to-peak [V]
    rlm: float = 1.0                       # PAM4 level mismatch ratio
    bw: Optional[float] = None             # single-pole driver bandwidth [Hz]
    rj_ui: float = 0.0                     # random jitter sigma [UI]
    sj_ui: float = 0.0                     # sinusoidal jitter amplitude [UI]
    sj_freq: float = 0.0                   # sinusoidal jitter frequency [Hz]
    dcd_ui: float = 0.0                    # duty-cycle distortion [UI]

    def __post_init__(self):
        _require(self.swing > 0, f"tx.swing must be > 0, got {self.swing}")
        _require(0.0 < self.rlm <= 1.0,
                 f"tx.rlm is a level-mismatch ratio in (0, 1], got {self.rlm}")
        _require(len(self.fir_taps) >= 1, "tx.fir_taps must not be empty")
        _require(self.fir_n_pre >= 0,
                 f"tx.fir_n_pre must be >= 0, got {self.fir_n_pre}")
        # a 1-tap "FIR" is a passthrough the engine skips outright, so its
        # n_pre is meaningless; only constrain it where the FIR is applied
        _require(len(self.fir_taps) == 1
                 or self.fir_n_pre < len(self.fir_taps),
                 f"tx.fir_n_pre must be < len(fir_taps)={len(self.fir_taps)}, "
                 f"got {self.fir_n_pre}")
        for nm in ("rj_ui", "sj_ui", "dcd_ui"):
            _require(getattr(self, nm) >= 0,
                     f"tx.{nm} must be >= 0, got {getattr(self, nm)}")


@dataclass(frozen=True)
class CtleConfig:
    enable: bool = True
    gdc_db: float = 0.0                 # DC gain [dB]
    peak_db: float = 6.0                # peaking at fp1 [dB]
    fz: Optional[float] = None          # zero [Hz]; default derived from peak_db
    fp1: Optional[float] = None         # first pole [Hz]; default Nyquist
    fp2: Optional[float] = None         # second pole [Hz]; default 2*Nyquist


@dataclass(frozen=True)
class AdcConfig:
    n_bits: int = 8
    n_lanes: int = 16                   # time-interleave factor
    enob: Optional[float] = None        # effective bits; None = ideal quantizer
    fullscale: float = 1.0              # full-scale range [V], differential
    offset_sigma: float = 0.0           # per-lane offset mismatch sigma [V]
    gain_sigma: float = 0.0             # per-lane gain mismatch sigma [ratio]
    skew_sigma_ui: float = 0.0          # per-lane sampling skew sigma [UI]
    calibrated: bool = False            # behavioral offset/gain calibration

    def __post_init__(self):
        _require(self.n_bits >= 1, f"adc.n_bits must be >= 1, got {self.n_bits}")
        _require(self.n_lanes >= 1,
                 f"adc.n_lanes must be >= 1, got {self.n_lanes}")
        _require(self.fullscale > 0,
                 f"adc.fullscale must be > 0, got {self.fullscale}")
        _require(self.enob is None or self.enob > 0,
                 f"adc.enob must be > 0 when set, got {self.enob}")


@dataclass(frozen=True)
class FfeConfig:
    n_pre: int = 4
    n_post: int = 10                    # 802.3dj reference receiver: 15 taps total
    adapt: Literal["none", "lms", "wiener"] = "none"
    mu: float = 1e-3

    def __post_init__(self):
        _require(self.n_pre >= 0, f"ffe.n_pre must be >= 0, got {self.n_pre}")
        _require(self.n_post >= 0, f"ffe.n_post must be >= 0, got {self.n_post}")
        _require_in(self.adapt, {"none", "lms", "wiener"}, "ffe.adapt")
        _require(self.mu >= 0, f"ffe.mu must be >= 0, got {self.mu}")


@dataclass(frozen=True)
class DfeConfig:
    n_taps: int = 1                     # 802.3dj reference receiver: 1 tap
    adapt: Literal["none", "lms", "sign_sign"] = "none"
    mu: float = 1e-3
    tap_limits: Optional[tuple[float, ...]] = None
    sum_bw: Optional[float] = None      # mixed-signal summing-node bandwidth [Hz]
    loop_delay_ui: float = 0.0          # mixed-signal decision feedback delay [UI]
    # tap-1 implementation (mixed-signal): "direct" = analog feedback into the
    # summing node (subject to sum_bw settling; loop_delay_ui > 1 kills it);
    # "unrolled" = speculative/loop-unrolled first tap — per-branch slicer
    # thresholds muxed by the previous decision (escapes sum_bw; critical
    # path is the mux; per-branch comparators carry independent offsets).
    tap1_mode: Literal["direct", "unrolled"] = "direct"
    comparator_offset_sigma: float = 0.0  # per-branch comparator offset [V] (unrolled)
    init: Literal["cursor", "zero"] = "cursor"  # tap seed: pulse cursors, or
    # cold start from zero (exercises the full adaptation transient)

    def __post_init__(self):
        _require(self.n_taps >= 0, f"dfe.n_taps must be >= 0, got {self.n_taps}")
        _require_in(self.adapt, {"none", "lms", "sign_sign"}, "dfe.adapt")
        _require(self.mu >= 0, f"dfe.mu must be >= 0, got {self.mu}")
        _require(self.loop_delay_ui >= 0,
                 f"dfe.loop_delay_ui must be >= 0, got {self.loop_delay_ui}")
        _require(self.sum_bw is None or self.sum_bw > 0,
                 f"dfe.sum_bw must be > 0 when set, got {self.sum_bw}")
        _require_in(self.tap1_mode, {"direct", "unrolled"}, "dfe.tap1_mode")
        _require_in(self.init, {"cursor", "zero"}, "dfe.init")
        _require(self.tap_limits is None
                 or len(self.tap_limits) == self.n_taps,
                 f"dfe.tap_limits must have n_taps={self.n_taps} entries, "
                 f"got {self.tap_limits}")


@dataclass(frozen=True)
class MlsdConfig:
    """Sequence-detection post-processor over the residual ISI.

    Runs after the FFE/DFE on the slicer-input samples, correcting decisions the
    memoryless slicer got wrong. ``sliding`` is DragonPHY's low-cost error-event
    detector; ``viterbi`` is full MLSE over ``memory`` residual postcursors
    (states = n_levels**memory, so keep it small).
    """

    kind: Literal["none", "sliding", "viterbi"] = "none"
    memory: int = 1                     # residual postcursors in the trellis
    seq_len: int = 4                    # sliding-detector squared-error window
    margin: float = 0.0                 # sliding-detector acceptance margin

    def __post_init__(self):
        _require_in(self.kind, {"none", "sliding", "viterbi"}, "mlsd.kind")
        _require(self.memory >= 1,
                 f"mlsd.memory must be >= 1, got {self.memory}")
        _require(self.seq_len >= 2,
                 f"mlsd.seq_len must be >= 2, got {self.seq_len}")
        _require(self.margin >= 0,
                 f"mlsd.margin must be >= 0, got {self.margin}")


@dataclass(frozen=True)
class CdrConfig:
    kind: Literal["bang_bang", "mueller_muller"] = "bang_bang"
    kp_shift: int = 6                   # proportional gain = 2**-kp_shift [UI/update]
    ki_shift: int = 12                  # integral gain = 2**-ki_shift
    pd_offset: float = 0.0              # MM sampling-point bias
    pd_input: Literal["adc", "ffe"] = "adc"  # MM PD source (DragonPHY mux);
    # NOTE: "ffe" on a fully-equalized signal leaves MM without a timing
    # gradient — use with pd_offset or partial equalization only.
    clamp: Optional[float] = None       # per-update phase step clamp [UI]
    loop_latency_symbols: int = 0       # digital pipeline latency in the loop

    def __post_init__(self):
        _require_in(self.kind, {"bang_bang", "mueller_muller"}, "cdr.kind")
        _require_in(self.pd_input, {"adc", "ffe"}, "cdr.pd_input")
        _require(self.clamp is None or self.clamp > 0,
                 f"cdr.clamp must be > 0 when set, got {self.clamp}")
        _require(self.loop_latency_symbols >= 0,
                 f"cdr.loop_latency_symbols must be >= 0, "
                 f"got {self.loop_latency_symbols}")


@dataclass(frozen=True)
class RxConfig:
    arch: Literal["mixed_signal", "adc_dsp"] = "mixed_signal"
    ctle: CtleConfig = field(default_factory=CtleConfig)
    vga_gain: float = 1.0
    adc: AdcConfig = field(default_factory=AdcConfig)
    ffe: FfeConfig = field(default_factory=FfeConfig)
    dfe: DfeConfig = field(default_factory=DfeConfig)
    cdr: CdrConfig = field(default_factory=CdrConfig)
    mlsd: MlsdConfig = field(default_factory=MlsdConfig)
    noise_rms: float = 0.0              # input-referred AWGN sigma [V]

    @staticmethod
    def product_mixed_signal(noise_rms: float = 0.003) -> "RxConfig":
        """The validated product mixed-signal receiver (<= 16 GBd NRZ):
        CTLE (7 dB) + 4-tap DFE with unrolled tap-1 + bang-bang CDR.
        No RX FFE — linear EQ beyond the CTLE belongs to the Tx FIR
        (backchannel-trained, see engine.backchannel)."""
        return RxConfig(
            arch="mixed_signal",
            ctle=CtleConfig(enable=True, peak_db=7.0),
            dfe=DfeConfig(n_taps=4, adapt="sign_sign", mu=5e-4,
                          tap1_mode="unrolled"),
            cdr=CdrConfig(kind="bang_bang", kp_shift=5, ki_shift=12),
            noise_rms=noise_rms,
        )


@dataclass(frozen=True)
class SimConfig:
    n_symbols: int = 100_000
    seed: int = 1
    engine: Literal["time", "stat", "both"] = "time"
    pattern: str = "prbs31"             # prbs7|prbs13|prbs31|prbs13q|prbs31q|prqs10
    chunk_symbols: int = 65536          # streaming block size for the time engine
    # staged startup (hard rule: CDR settle -> data-aided training -> DD)
    cdr_settle: int = 2000              # symbols for CDR to lock before adaptation
    train_symbols: int = 4000           # data-aided LMS span after settle
    warmup_discard: int | None = None   # symbols excluded from BER (default: settle+train)

    def __post_init__(self):
        _require(self.n_symbols >= 1,
                 f"sim.n_symbols must be >= 1, got {self.n_symbols}")
        _require_in(self.engine, {"time", "stat", "both"}, "sim.engine")
        _require(self.chunk_symbols >= 1,
                 f"sim.chunk_symbols must be >= 1, got {self.chunk_symbols}")
        for nm in ("cdr_settle", "train_symbols"):
            _require(getattr(self, nm) >= 0,
                     f"sim.{nm} must be >= 0, got {getattr(self, nm)}")
        _require(self.warmup_discard is None or self.warmup_discard >= 0,
                 f"sim.warmup_discard must be >= 0 when set, "
                 f"got {self.warmup_discard}")


@dataclass(frozen=True)
class NumericConfig:
    mode: Literal["float", "fixed"] = "float"
    # Default Q formats follow DragonPHY2 silicon-proven widths (Phase 6).
    adc_code: QFormat = field(default_factory=lambda: QFormat(8, 0))
    ffe_weight: QFormat = field(default_factory=lambda: QFormat(10, 8))
    dfe_weight: QFormat = field(default_factory=lambda: QFormat(10, 8))
    error: QFormat = field(default_factory=lambda: QFormat(9, 0))


@dataclass(frozen=True)
class LinkConfig:
    # defaults define the product mixed-signal anchor point: 16 GBd NRZ
    # (the canonical validated configuration, well inside the comfort zone)
    modulation: Literal["nrz", "pam4"] = "nrz"
    symbol_rate: float = MS_DEFAULT_BAUD   # [Baud]
    osr: int = 32                       # oversampling ratio (samples per UI)
    # 1/(1+D) mod-N precoding at the Tx, undone at the Rx. Standard PAM4
    # practice with DFE/MLSD: it terminates error bursts (a decision error no
    # longer feeds back forever) at the cost of turning each isolated symbol
    # error into two — a trade that pays off once bursts dominate.
    precode: bool = False
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    tx: TxConfig = field(default_factory=TxConfig)
    rx: RxConfig = field(default_factory=RxConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    numeric: NumericConfig = field(default_factory=NumericConfig)

    def __post_init__(self):
        _require_in(self.modulation, {"nrz", "pam4"}, "modulation")
        _require(self.symbol_rate > 0,
                 f"symbol_rate must be > 0 Baud, got {self.symbol_rate}")
        # a non-positive osr silently yields a negative/infinite dt
        _require(self.osr >= 1, f"osr must be >= 1 sample/UI, got {self.osr}")
        # NOTE: a touchstone channel with no file is intentionally allowed here
        # — LinkConfig() defaults to it, and ChannelModel.from_config raises a
        # precise error at load time if it is actually used.

    @property
    def ui(self) -> float:
        """Unit interval [s]."""
        return 1.0 / self.symbol_rate

    @property
    def dt(self) -> float:
        """Oversampled-waveform time step [s]."""
        return self.ui / self.osr

    @property
    def f_nyquist(self) -> float:
        return self.symbol_rate / 2.0

    @property
    def bits_per_symbol(self) -> int:
        return 2 if self.modulation == "pam4" else 1

    @property
    def data_rate(self) -> float:
        """Data rate [bit/s] = symbol_rate * bits_per_symbol."""
        return self.symbol_rate * self.bits_per_symbol
