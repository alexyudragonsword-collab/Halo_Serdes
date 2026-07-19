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

# Product-grade mixed-signal envelope: the CTLE + DFE + BB-CDR partition
# (no RX FFE), per modulation in DATA-rate terms — both correspond to the
# same 16 GBd symbol-rate ceiling; PAM4 doubles the data rate at the cost of
# a ~9.5 dB level-spacing penalty (so it needs a milder channel or FEC):
#   NRZ : 16 Gb/s (validated: -18.7 dB channel, 105 mV post-DFE eye, BER 0)
#   PAM4: 32 Gb/s (validated: -7.9 dB channel, BER 0; marginal ~5e-4 on the
#         -18.7 dB Whisper — KR4-FEC territory)
# Above the envelope the time engine warns; ADC/DSP is the supported path.
MS_PRODUCT_MAX_DATA_RATE: dict[str, float] = {"nrz": 16.0e9, "pam4": 32.0e9}
MS_PRODUCT_MAX_BAUD = 16.0e9  # the common symbol-rate ceiling behind both


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


@dataclass(frozen=True)
class FfeConfig:
    n_pre: int = 4
    n_post: int = 10                    # 802.3dj reference receiver: 15 taps total
    adapt: Literal["none", "lms", "wiener"] = "none"
    mu: float = 1e-3


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


@dataclass(frozen=True)
class RxConfig:
    arch: Literal["mixed_signal", "adc_dsp"] = "mixed_signal"
    ctle: CtleConfig = field(default_factory=CtleConfig)
    vga_gain: float = 1.0
    adc: AdcConfig = field(default_factory=AdcConfig)
    ffe: FfeConfig = field(default_factory=FfeConfig)
    dfe: DfeConfig = field(default_factory=DfeConfig)
    cdr: CdrConfig = field(default_factory=CdrConfig)
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
    # (the validated upper limit of the CTLE+DFE partition, MS_PRODUCT_MAX_BAUD)
    modulation: Literal["nrz", "pam4"] = "nrz"
    symbol_rate: float = MS_PRODUCT_MAX_BAUD   # [Baud]
    osr: int = 32                       # oversampling ratio (samples per UI)
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    tx: TxConfig = field(default_factory=TxConfig)
    rx: RxConfig = field(default_factory=RxConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    numeric: NumericConfig = field(default_factory=NumericConfig)

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
