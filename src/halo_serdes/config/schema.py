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
class ClockConfig:
    """The clock a transmitter launches its edges on.

    Two kinds. ``"white"`` is the spec-sheet picture that was the whole model
    before this existed: independent Gaussian edge jitter (``rj_ui``), one
    sinusoidal tone (``sj_ui`` at ``sj_freq``) and duty-cycle distortion
    (``dcd_ui``). ``"profile"`` is the clock a PLL actually makes: a phase-noise
    profile file -- L(f) in dBc/Hz on an offset grid, a spur table and the
    carrier -- synthesised into *coloured* per-edge timing, with the three
    white-kind terms still added on top. A CDR is a high-pass to clock jitter,
    so two clocks of equal RMS leave very different residues behind it; one
    ``rj_ui`` number cannot ask that question, a profile can.

    The profile is a file, not an object, on purpose: a frozen ``LinkConfig``
    can name a file (invariant #1), and the producer (pll_simulator) and this
    consumer then need no import of each other. ``f0_hz`` overrides the
    carrier the file states; it is the frequency the *phase* is measured
    against, and timing is phase / (2 pi f0) -- a half-rate clock carries
    twice the seconds for the same dBc/Hz, which is why neither side derives
    f0 from the baud rate.
    """

    kind: Literal["white", "profile"] = "white"
    file: Optional[str] = None             # profile path (profile kind); repo-relative resolves like channel.file
    f0_hz: Optional[float] = None          # carrier override [Hz]; None takes the file's f0_hz
    rj_ui: float = 0.0                     # random jitter sigma [UI]
    sj_ui: float = 0.0                     # sinusoidal jitter amplitude [UI]
    sj_freq: float = 0.0                   # sinusoidal jitter frequency [Hz]
    dcd_ui: float = 0.0                    # duty-cycle distortion [UI]

    def __post_init__(self):
        # One dataclass serves tx.clock and rx.clock, so the messages name
        # the field without its owner.
        _require_in(self.kind, {"white", "profile"}, "clock.kind")
        # Caught here, at construction, so a profile clock with no file fails
        # in load_config with its field named -- not as a FileNotFoundError
        # out of the engine three layers down.
        _require(self.kind != "profile" or bool(self.file),
                 "clock.file is required when clock.kind is 'profile' (tx.clock / rx.clock)")
        _require(self.f0_hz is None or self.f0_hz > 0,
                 f"clock.f0_hz must be > 0 when set, got {self.f0_hz}")
        for nm in ("rj_ui", "sj_ui", "dcd_ui", "sj_freq"):
            _require(getattr(self, nm) >= 0,
                     f"clock.{nm} must be >= 0, got {getattr(self, nm)}")


@dataclass(frozen=True)
class TxConfig:
    fir_taps: tuple[float, ...] = (1.0,)   # main cursor inferred: last-but-pre convention set in Phase 1
    fir_n_pre: int = 0
    swing: float = 1.0                     # differential peak-to-peak [V]
    rlm: float = 1.0                       # PAM4 level mismatch ratio
    bw: Optional[float] = None             # single-pole driver bandwidth [Hz]
    # DSP Tx (tx/dac.py, tx/driver.py); every default is "off"
    dac_bits: Optional[int] = None         # None = ideal (unquantised) Tx
    dac_fs: Optional[float] = None         # DAC full scale, peak-to-peak [V]; None = FFE peak
    dac_thermo_msbs: int = 0               # thermometer-decoded MSBs (rest binary)
    dac_unit_sigma: float = 0.0            # unit-cell current mismatch sigma [LSB]
    drv_nl: Literal["none", "curve", "tanh", "cubic"] = "none"
    drv_compression: float = 0.0           # c of core.static_curve (= optical li_compression)
    drv_p1db_v: Optional[float] = None     # tanh: input amplitude at 1 dB compression [V]
    drv_oip3_v: Optional[float] = None     # cubic: output IP3 amplitude [V]
    # Edge timing lives on the clock. The four jitter fields that used to sit
    # here (rj_ui, sj_ui, sj_freq, dcd_ui) moved into ClockConfig when a clock
    # became something that could be a PLL profile rather than three numbers;
    # the YAML loader still accepts them at tx.* and migrates them.
    clock: ClockConfig = field(default_factory=ClockConfig)

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
        dac = self.dac_bits is not None
        _require(not dac or 1 <= self.dac_bits <= 16,
                 f"tx.dac_bits must be in [1, 16] when set, got {self.dac_bits}")
        _require(self.dac_fs is None or (dac and self.dac_fs > 0),
                 f"tx.dac_fs needs tx.dac_bits and must be > 0, got {self.dac_fs}")
        _require(0 <= self.dac_thermo_msbs <= (self.dac_bits if dac else 0),
                 f"tx.dac_thermo_msbs must be in [0, dac_bits], got {self.dac_thermo_msbs}")
        _require(self.dac_unit_sigma >= 0 and (dac or self.dac_unit_sigma == 0),
                 f"tx.dac_unit_sigma must be >= 0 and needs tx.dac_bits, got {self.dac_unit_sigma}")
        _require_in(self.drv_nl, ("none", "curve", "tanh", "cubic"), "tx.drv_nl")
        _require(0.0 <= self.drv_compression < 1.0,
                 f"tx.drv_compression must be in [0, 1), got {self.drv_compression}")
        _require(self.drv_compression == 0.0 or self.drv_nl == "curve",
                 "tx.drv_compression is the 'curve' driver's parameter: set tx.drv_nl='curve'")
        for name in ("drv_p1db_v", "drv_oip3_v"):
            v = getattr(self, name)
            _require(v is None or v > 0, f"tx.{name} must be > 0 when set, got {v}")
        _require(self.drv_nl != "tanh" or self.drv_p1db_v is not None,
                 "tx.drv_nl='tanh' needs tx.drv_p1db_v")
        _require(self.drv_nl != "cubic" or self.drv_oip3_v is not None,
                 "tx.drv_nl='cubic' needs tx.drv_oip3_v")


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
    # MM PD source (DragonPHY mux). "auto" resolves by modulation through
    # LinkConfig.mm_pd_input: on PAM4 the raw ADC samples lock MM off the
    # eye's peak, while on NRZ the equalised samples can leave MM with no
    # timing gradient (light-ISI links wander ~0.4 UI) -- see
    # cairn/DSP发端与PR.md §5 for the sweep behind this.
    pd_input: Literal["auto", "adc", "ffe"] = "auto"
    clamp: Optional[float] = None       # per-update phase step clamp [UI]
    loop_latency_symbols: int = 0       # digital pipeline latency in the loop

    def __post_init__(self):
        _require_in(self.kind, {"bang_bang", "mueller_muller"}, "cdr.kind")
        _require_in(self.pd_input, {"auto", "adc", "ffe"}, "cdr.pd_input")
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
    # The receiver's own sampling clock. Default: ideal (white, all zero), so
    # every configuration written before this field existed is unchanged.
    # ``rj_ui``/``sj_ui``/``sj_freq``/``file`` move the sampling instants the
    # CDR hands the sampler; ``dcd_ui`` has no meaning for a sampler and is
    # ignored (edge polarity is a transmit-side notion).
    clock: ClockConfig = field(default_factory=ClockConfig)

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
    chunk_symbols: int = 65536          # receiver-loop chunk: progress / cancel granularity
    # time engine: produce the waveform block by block for a sliding receiver
    # window (memory flat in n_symbols); noise and the Tx pole become FIRs, so
    # results match the default statistically, not bit for bit (engine/stream.py)
    stream: bool = False
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
    # bit-true CDR (dsp/fixed_loop.py): phase-interpolator steps per UI as a
    # power of two, and the phase register's bits below the PI code
    pi_bits: int = 7
    phase_frac_bits: int = 24
    # bit-true sliding MLSD (dsp/fixed_mlsd.py): width of the summed squared-
    # error metric; squares are shifted right as far as needed to fit it
    mlsd_metric_bits: int = 24
    # integer LMS: the weight accumulators' bits below the weight LSB (too few
    # and the small updates round to nothing -- the loop stops adapting)
    lms_guard_bits: int = 24

    def __post_init__(self):
        _require_in(self.mode, {"float", "fixed"}, "numeric.mode")
        _require(1 <= self.pi_bits <= 20, f"numeric.pi_bits must be in [1, 20], got {self.pi_bits}")
        _require(0 <= self.phase_frac_bits <= 32,
                 f"numeric.phase_frac_bits must be in [0, 32], got {self.phase_frac_bits}")
        _require(0 <= self.lms_guard_bits <= 40,
                 f"numeric.lms_guard_bits must be in [0, 40], got {self.lms_guard_bits}")
        _require(4 <= self.mlsd_metric_bits <= 62,
                 f"numeric.mlsd_metric_bits must be in [4, 62], got {self.mlsd_metric_bits}")


@dataclass(frozen=True)
class OpticalConfig:
    """The three optical blocks between two electrical segments: E/O, fibre, O/E.

    Stage 1 of the optical-interconnect model: every block is a small-signal
    transfer function cascaded into the channel, plus the noise the photodiode
    and TIA add, which depends on the optical power of the level being
    received. No retiming, no L-I nonlinearity, no TDECQ.

    Two kinds. ``"vcsel_mmf"`` is the 100G/lambda short-reach picture (IEEE
    802.3db 100GBASE-SR1: 850 nm VCSEL over OM4/OM5): a second-order laser
    response with relaxation-oscillation frequency ``f_r_hz`` and damping
    ``damping_hz``, a Gaussian modal-bandwidth fibre set by
    ``modal_bw_mhz_km`` / ``length_m``. ``"eml_smf"`` is the 200G/lambda
    picture (802.3dj 200GBASE-DR1/FR1: 1310 nm EML over G.652): a single-pole
    modulator whose 3 dB bandwidth is ``f_r_hz`` (``damping_hz`` is unused),
    and chromatic dispersion ``dispersion_ps_nm_km`` with chirp ``chirp_alpha``.

    The optical power scale comes from ``oma_dbm`` and ``er_db`` alone:
    P_low = OMA / (ER - 1), P_high = ER * OMA / (ER - 1). The electrical
    waveform is never rescaled by it; it only sets how much shot and RIN noise
    each level carries (``optical/noise.py``). Values are standards magnitudes,
    not a particular device; see the docstrings in ``optical/`` for the
    clause each default is taken from.
    """

    kind: Literal["none", "vcsel_mmf", "eml_smf"] = "none"
    # electro-optic (E/O)
    f_r_hz: float = 22.0e9              # VCSEL relaxation-oscillation frequency, or EML 3 dB bandwidth [Hz]
    damping_hz: float = 30.0e9          # VCSEL damping rate gamma / 2 pi [Hz]
    er_db: float = 4.0                  # extinction ratio P_high / P_low [dB]
    oma_dbm: float = 0.0                # outer optical modulation amplitude P_high - P_low [dBm]
    rin_db_hz: float = -140.0           # relative intensity noise [dB/Hz]
    # fibre
    length_m: float = 100.0
    modal_bw_mhz_km: Optional[float] = None       # MMF effective modal bandwidth (vcsel_mmf)
    dispersion_ps_nm_km: Optional[float] = None   # SMF chromatic dispersion (eml_smf)
    chirp_alpha: float = 0.0                      # transmitter linewidth-enhancement (chirp) factor
    wavelength_nm: float = 1310.0                 # carrier wavelength (dispersion phase only)
    # Large-signal E/O curve (stage 3): 0 is a linear E/O. For a VCSEL it is
    # L-I rollover, for an EML the EAM's exponential absorption curve; either
    # way 1 - (smaller end slope / larger end slope) over the outer levels, so
    # OMA and ER stay what the fields above say and the inner PAM4 levels move
    # (optical/eo.py, StaticCurve).
    li_compression: float = 0.0
    # opto-electric (O/E): photodiode + TIA
    responsivity_a_w: float = 0.7
    tia_bw_hz: float = 40.0e9           # second-order (Butterworth) 3 dB bandwidth [Hz]
    tia_noise_pa_sqrthz: float = 12.0   # input-referred current noise density [pA/sqrt(Hz)]
    tz_ohm: float = 2000.0              # transimpedance, for reporting the physical output swing [ohm]

    def __post_init__(self):
        _require_in(self.kind, {"none", "vcsel_mmf", "eml_smf"}, "optical.kind")
        if self.kind == "none":
            return
        for nm in ("f_r_hz", "damping_hz", "er_db", "responsivity_a_w",
                   "tia_bw_hz", "tz_ohm", "wavelength_nm"):
            _require(getattr(self, nm) > 0,
                     f"optical.{nm} must be > 0, got {getattr(self, nm)}")
        _require(self.length_m >= 0.0,
                 f"optical.length_m must be >= 0, got {self.length_m}")
        _require(self.tia_noise_pa_sqrthz >= 0.0,
                 f"optical.tia_noise_pa_sqrthz must be >= 0, got {self.tia_noise_pa_sqrthz}")
        _require(self.rin_db_hz < 0.0,
                 f"optical.rin_db_hz must be < 0 dB/Hz, got {self.rin_db_hz}")
        _require(0.0 <= self.li_compression < 1.0,
                 f"optical.li_compression must be in [0, 1), got {self.li_compression}")
        if self.kind == "vcsel_mmf":
            _require(self.modal_bw_mhz_km is not None and self.modal_bw_mhz_km > 0,
                     "optical.modal_bw_mhz_km must be set (> 0) for kind 'vcsel_mmf'")
        else:
            _require(self.dispersion_ps_nm_km is not None,
                     "optical.dispersion_ps_nm_km must be set for kind 'eml_smf'")

    # -- derived optical power scale [W] ------------------------------------

    @property
    def oma_w(self) -> float:
        return 10.0 ** (self.oma_dbm / 10.0) * 1e-3

    @property
    def p_low_w(self) -> float:
        er = 10.0 ** (self.er_db / 10.0)
        return self.oma_w / (er - 1.0)

    @property
    def p_high_w(self) -> float:
        return self.p_low_w + self.oma_w


def _analytic_segment() -> ChannelConfig:
    # A segment defaults to a lossless analytic trace, not to the library's
    # touchstone default: a topology with no file named must still build.
    return ChannelConfig(kind="analytic")


@dataclass(frozen=True)
class TopologyConfig:
    """Host TX -> electrical segment A -> E/O -> fibre -> O/E -> segment B -> host RX.

    LPO, CPO and a retimed module are this one chain cut at different places;
    at stage 1 they differ only in what segments A and B lose, the optical
    blocks are shared. ``LinkConfig.topology`` set replaces ``channel`` with
    this cascade; left ``None`` the link is the electrical one it always was.
    """

    seg_a: ChannelConfig = field(default_factory=_analytic_segment)
    optical: OpticalConfig = field(default_factory=OpticalConfig)
    seg_b: ChannelConfig = field(default_factory=_analytic_segment)
    # Retiming (stage 2). "both" puts a retimer at the module's ingress and
    # egress: the link becomes three links in series (host TX -> seg A ->
    # retimer; retimer -> optics -> retimer; retimer -> seg B -> host RX),
    # each with its own receiver and clean re-transmission of its decisions,
    # and the end-to-end BER is scored on the host's decisions against the
    # host's symbols. The retimer's receiver and transmitter are ordinary
    # RxConfig / TxConfig; FEC stays end to end (no termination in the module).
    retimer: Literal["none", "both"] = "none"
    retimer_rx: RxConfig = field(default_factory=RxConfig)
    retimer_tx: TxConfig = field(default_factory=TxConfig)

    def __post_init__(self):
        _require_in(self.retimer, {"none", "both"}, "topology.retimer")
        # The cascade multiplies H(f) point by point, so both segments must
        # be evaluated on one grid; the grid is seg_a's and seg_b must agree.
        _require(self.seg_a.n_freq == self.seg_b.n_freq,
                 f"topology.seg_b.n_freq must equal seg_a.n_freq "
                 f"({self.seg_a.n_freq}), got {self.seg_b.n_freq}")
        _require(self.seg_a.f_max == self.seg_b.f_max,
                 f"topology.seg_b.f_max must equal seg_a.f_max "
                 f"({self.seg_a.f_max}), got {self.seg_b.f_max}")


@dataclass(frozen=True)
class PrConfig:
    """Partial-response target the receiver equalises to: ``target`` is the
    equalised pulse's cursors from the main one on, ``(1.0,)`` a delta (no
    shaping), ``(1.0, a)`` the 1 + aD family. A sequence detector then
    resolves the controlled cursor instead of the FFE inverting it, which
    costs less noise enhancement on a lossy channel.

    ``at="tx"`` shapes in the transmitter instead, before the FFE and the
    DAC, scaled by 1 / (1 + a) so the Tx peak swing stays what it was (the
    DAC and driver range is the constraint, and the extra levels come out of
    it); the receiver then detects the same 1 + aD target.

    ``(1.0, a, b)`` is the 1 + aD + bD^2 family (a in [0, 2], b in [-1, 1];
    EPR4, 1 + 2D + D^2, is the edge):
    the sequence detector's trellis grows by a factor N, the per-symbol
    decision subtracts both controlled cursors. Longer targets are out of
    scope (the trellis grows as N^L).

    ``adapt`` chooses a instead of taking the configured one (receive side
    only): ``"mmse"`` solves the start-up pulse for the a that minimises the
    FFE's mean-square error with the main cursor held at 1 and keeps it;
    ``"lms"`` starts there and adapts a with the FFE, step ``mu``
    (dimensionless: normalised by the mean symbol power), b too for a
    three-cursor target. Both minimise the same cost, so LMS tracks what the
    start-up solve predicted."""
    target: tuple[float, ...] = (1.0,)
    at: Literal["rx", "tx"] = "rx"
    adapt: Literal["none", "mmse", "lms"] = "none"
    mu: float = 2e-4

    def __post_init__(self):
        _require_in(self.at, {"rx", "tx"}, "pr.at")
        _require(len(self.target) in (1, 2, 3),
                 f"pr.target must be (1.0,), (1.0, a) or (1.0, a, b), got {self.target!r}")
        _require(self.target[0] == 1.0,
                 f"pr.target[0] must be 1.0 (the main cursor), got {self.target[0]}")
        if len(self.target) == 2:
            _require(0.0 <= self.target[1] <= 1.0,
                     f"pr.target alpha must be in [0, 1], got {self.target[1]}")
        if len(self.target) == 3:
            _require(0.0 <= self.target[1] <= 2.0 and -1.0 <= self.target[2] <= 1.0,
                     f"pr.target (1.0, a, b) needs a in [0, 2], b in [-1, 1], got {self.target!r}")
        _require_in(self.adapt, {"none", "mmse", "lms"}, "pr.adapt")
        _require(self.adapt == "none" or (len(self.target) >= 2 and self.at == "rx"),
                 f"pr.adapt={self.adapt!r} chooses a receive-side target: it needs "
                 f"pr.target=(1.0, a[, b]) and pr.at='rx', got {self.target!r}, at={self.at!r}")
        _require(self.mu > 0.0, f"pr.mu must be > 0, got {self.mu}")

    @property
    def active(self) -> bool:
        return len(self.target) > 1

    @property
    def at_tx(self) -> bool:
        return self.active and self.at == "tx"

    @property
    def alpha(self) -> float:
        return float(self.target[1]) if self.active else 0.0

    @property
    def beta(self) -> float:
        return float(self.target[2]) if len(self.target) == 3 else 0.0


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
    # Optical interconnect: segment A -> E/O -> fibre -> O/E -> segment B in
    # place of ``channel``. None is the electrical link, byte for byte.
    topology: Optional[TopologyConfig] = None
    # Receive-side partial-response target (ADC receiver only)
    pr: PrConfig = field(default_factory=PrConfig)

    def __post_init__(self):
        _require_in(self.modulation, {"nrz", "pam4"}, "modulation")
        # A form needs an "off" state it can express as a field value, so a
        # topology whose optics are "none" is accepted and normalised here to
        # the one representation the engines test for: topology is None.
        if self.topology is not None and self.topology.optical.kind == "none":
            object.__setattr__(self, "topology", None)
        _require(self.symbol_rate > 0,
                 f"symbol_rate must be > 0 Baud, got {self.symbol_rate}")
        # a non-positive osr silently yields a negative/infinite dt
        _require(self.osr >= 1, f"osr must be >= 1 sample/UI, got {self.osr}")
        # a DAC that cannot even hold the symbol alphabet is a typo, not a design
        n_lv = 4 if self.modulation == "pam4" else 2
        _require(self.tx.dac_bits is None or 2 ** self.tx.dac_bits >= n_lv,
                 f"tx.dac_bits={self.tx.dac_bits} cannot represent {n_lv} {self.modulation} levels")
        # the mixed-signal slicer has no digital equaliser to shape a target
        # with; that receiver's own blocks are where the two may differ
        _require(not (self.pr.active and self.rx.arch == "mixed_signal"),
                 "pr.target with rx.arch='mixed_signal': partial-response "
                 "equalisation needs the ADC receiver's digital FFE")
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
    def mm_pd_input(self) -> str:
        """The MM phase detector's input with "auto" resolved: equalised
        samples on PAM4, raw ADC samples on NRZ."""
        pd = self.rx.cdr.pd_input
        if pd != "auto":
            return pd
        return "ffe" if self.modulation == "pam4" else "adc"

    @property
    def bits_per_symbol(self) -> int:
        return 2 if self.modulation == "pam4" else 1

    @property
    def data_rate(self) -> float:
        """Data rate [bit/s] = symbol_rate * bits_per_symbol."""
        return self.symbol_rate * self.bits_per_symbol
