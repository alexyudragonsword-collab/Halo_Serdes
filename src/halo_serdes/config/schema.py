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
# (examples/12; last open eye at -29.0 dB NRZ / -18.7 dB PAM4 / -16.1 dB PAM6 /
# -14.5 dB PAM8 -- the gaps are roughly the level penalties, 20 log10 of 3,
# 5, 7. Recalibrated 2026-10-30 when the impulse began to be trimmed by its
# ISI tail (ROADMAP P1 #42): NRZ's limit 30 -> 28, PAM4's 36 -> 32 and its
# comfort 32 -> 28 Gb/s; the rest stayed):
#
#   default anchor : 16 GBd (canonical validated configs; NRZ with ample
#                    margin, PAM4 at its limit since the 2026-10-30 recalibration)
#   comfort zone   : NRZ <= 24 Gb/s, PAM4 <= 28, PAM6 <= 30, PAM8 <= 24 Gb/s
#                    (post-DFE eye >= ~25% of level spacing on Whisper)
#   absolute limit : NRZ <= 28 Gb/s, PAM4 <= 32, PAM6 <= 32.5, PAM8 <= 33 Gb/s
#                    (last-open points; a few-mV eye — expect FEC territory;
#                    PAM6 / PAM8 swept in 1 GBd steps on 2026-10-17, the
#                    mixed-signal receiver is 12-13 / 10-11 GBd there)
#   hard ceiling   : 30 GBd symbol rate regardless of modulation — proxy for
#                    the circuit walls this behavioral model does NOT capture
#                    (CTLE gain-bandwidth, slicer aperture, clock path), which
#                    are what stop real mixed-signal designs near ~30 GBd.
#
# comfort < rate <= limit: the time engine warns "marginal"; beyond the limit
# or ceiling it warns "exceeds" — ADC/DSP is the supported path there.
MS_DEFAULT_BAUD = 16.0e9
MS_COMFORT_DATA_RATE: dict[str, float] = {"nrz": 24.0e9, "pam4": 28.0e9, "pam6": 30.0e9, "pam8": 24.0e9}
MS_LIMIT_DATA_RATE: dict[str, float] = {"nrz": 28.0e9, "pam4": 32.0e9, "pam6": 32.5e9, "pam8": 33.0e9}
MS_HARD_MAX_BAUD = 30.0e9


def ms_envelope(modulation: str) -> tuple[float, float] | None:
    """(comfort, limit) data rate [bit/s] of the mixed-signal envelope --
    all four formats are calibrated by examples/12 (PAM6 / PAM8 since
    2026-10-17, ROADMAP P2 #34); None only for a format the tables do not
    know, where the hard baud ceiling alone applies."""
    if modulation in MS_COMFORT_DATA_RATE:
        return MS_COMFORT_DATA_RATE[modulation], MS_LIMIT_DATA_RATE[modulation]
    return None


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


#: The per-field numeric bounds ``__post_init__`` enforces, as data: what a
#: form shows before the configuration is built and which field to mark when
#: a value is out of range (ROADMAP P2 #41). ``"Class.field": (label, low,
#: high, low_open, high_open, unit)``; ``None`` is unbounded, and a field
#: whose value may be ``None`` is only bounded when set. Rules that read two
#: fields (``fir_n_pre < len(fir_taps)``, DAC fields needing ``dac_bits``)
#: stay in code.
FIELD_BOUNDS: dict[str, tuple[str, float | None, float | None, bool, bool, str]] = {
    "LinkConfig.symbol_rate": ("symbol_rate", 0, None, True, False, " Baud"),
    "LinkConfig.osr": ("osr", 1, None, False, False, " sample/UI"),
    "ChannelConfig.n_freq": ("channel.n_freq", 2, None, False, False, ""),
    "ChannelConfig.length_m": ("channel.length_m", 0, None, False, False, ""),
    "ChannelConfig.f_max": ("channel.f_max", 0, None, True, False, ""),
    "ClockConfig.f0_hz": ("clock.f0_hz", 0, None, True, False, ""),
    "ClockConfig.rj_ui": ("clock.rj_ui", 0, None, False, False, ""),
    "ClockConfig.sj_ui": ("clock.sj_ui", 0, None, False, False, ""),
    "ClockConfig.dcd_ui": ("clock.dcd_ui", 0, None, False, False, ""),
    "ClockConfig.sj_freq": ("clock.sj_freq", 0, None, False, False, ""),
    "TxConfig.swing": ("tx.swing", 0, None, True, False, ""),
    "TxConfig.rlm": ("tx.rlm", 0, 1, True, False, ""),
    "TxConfig.fir_n_pre": ("tx.fir_n_pre", 0, None, False, False, ""),
    "TxConfig.dac_bits": ("tx.dac_bits", 1, 16, False, False, ""),
    "TxConfig.drv_compression": ("tx.drv_compression", 0, 1, False, True, ""),
    "TxConfig.drv_p1db_v": ("tx.drv_p1db_v", 0, None, True, False, ""),
    "TxConfig.drv_oip3_v": ("tx.drv_oip3_v", 0, None, True, False, ""),
    "TxConfig.noise_rms": ("tx.noise_rms", 0, None, False, False, ""),
    "AdcCalConfig.fg_samples": ("adc.cal.fg_samples", 1, None, False, False, ""),
    "AdcCalConfig.fg_ref": ("adc.cal.fg_ref", 0, 1, True, True, ""),
    "AdcCalConfig.mu_offset": ("adc.cal.mu_offset", 0, 1, False, True, ""),
    "AdcCalConfig.mu_gain": ("adc.cal.mu_gain", 0, 1, False, True, ""),
    "AdcCalConfig.mu_skew": ("adc.cal.mu_skew", 0, 1, False, True, ""),
    "AdcConfig.n_bits": ("adc.n_bits", 1, None, False, False, ""),
    "AdcConfig.n_lanes": ("adc.n_lanes", 1, None, False, False, ""),
    "AdcConfig.fullscale": ("adc.fullscale", 0, None, True, False, ""),
    "AdcConfig.enob": ("adc.enob", 0, None, True, False, ""),
    "FfeConfig.n_pre": ("ffe.n_pre", 0, None, False, False, ""),
    "FfeConfig.n_post": ("ffe.n_post", 0, None, False, False, ""),
    "FfeConfig.mu": ("ffe.mu", 0, None, False, False, ""),
    "DfeConfig.n_taps": ("dfe.n_taps", 0, None, False, False, ""),
    "DfeConfig.mu": ("dfe.mu", 0, None, False, False, ""),
    "DfeConfig.loop_delay_ui": ("dfe.loop_delay_ui", 0, None, False, False, ""),
    "DfeConfig.train_gain": ("dfe.train_gain", 0, None, False, False, ""),
    "DfeConfig.sum_bw": ("dfe.sum_bw", 0, None, True, False, ""),
    "MlsdConfig.memory": ("mlsd.memory", 1, None, False, False, ""),
    "MlsdConfig.state_memory": ("mlsd.state_memory", 1, None, False, False, ""),
    "MlsdConfig.seq_len": ("mlsd.seq_len", 2, None, False, False, ""),
    "MlsdConfig.margin": ("mlsd.margin", 0, None, False, False, ""),
    "CdrConfig.clamp": ("cdr.clamp", 0, None, True, False, ""),
    "CdrConfig.loop_latency_symbols": ("cdr.loop_latency_symbols", 0, None, False, False, ""),
    "SimConfig.n_symbols": ("sim.n_symbols", 1, None, False, False, ""),
    "SimConfig.chunk_symbols": ("sim.chunk_symbols", 1, None, False, False, ""),
    "SimConfig.cdr_settle": ("sim.cdr_settle", 0, None, False, False, ""),
    "SimConfig.train_symbols": ("sim.train_symbols", 0, None, False, False, ""),
    "SimConfig.warmup_discard": ("sim.warmup_discard", 0, None, False, False, ""),
    "NumericConfig.pi_bits": ("numeric.pi_bits", 1, 20, False, False, ""),
    "NumericConfig.phase_frac_bits": ("numeric.phase_frac_bits", 0, 32, False, False, ""),
    "NumericConfig.lms_guard_bits": ("numeric.lms_guard_bits", 0, 40, False, False, ""),
    "NumericConfig.cal_frac_bits": ("numeric.cal_frac_bits", 1, 24, False, False, ""),
    "NumericConfig.cal_gain_bits": ("numeric.cal_gain_bits", 4, 24, False, False, ""),
    "NumericConfig.mlsd_metric_bits": ("numeric.mlsd_metric_bits", 4, 62, False, False, ""),
    "OpticalConfig.f_r_hz": ("optical.f_r_hz", 0, None, True, False, ""),
    "OpticalConfig.damping_hz": ("optical.damping_hz", 0, None, True, False, ""),
    "OpticalConfig.er_db": ("optical.er_db", 0, None, True, False, ""),
    "OpticalConfig.responsivity_a_w": ("optical.responsivity_a_w", 0, None, True, False, ""),
    "OpticalConfig.tia_bw_hz": ("optical.tia_bw_hz", 0, None, True, False, ""),
    "OpticalConfig.tz_ohm": ("optical.tz_ohm", 0, None, True, False, ""),
    "OpticalConfig.wavelength_nm": ("optical.wavelength_nm", 0, None, True, False, ""),
    "OpticalConfig.length_m": ("optical.length_m", 0, None, False, False, ""),
    "OpticalConfig.tia_noise_pa_sqrthz": ("optical.tia_noise_pa_sqrthz", 0, None, False, False, ""),
    "OpticalConfig.rin_db_hz": ("optical.rin_db_hz", None, 0, False, True, " dB/Hz"),
    "OpticalConfig.li_compression": ("optical.li_compression", 0, 1, False, True, ""),
    "OpticalConfig.drv_ctle_db": ("optical.drv_ctle_db", 0, 12, False, False, " dB"),
    "OpticalConfig.tia_ctle_db": ("optical.tia_ctle_db", 0, 12, False, False, " dB"),
    "PrConfig.mu": ("pr.mu", 0, None, True, False, ""),
    "AggressorConfig.skew_ui": ("crosstalk aggressor skew_ui", 0, None, False, False, ""),
    "AggressorConfig.f3db_hz": ("crosstalk aggressor f3db_hz", 0, None, True, False, ""),
    "CrosstalkConfig.spread_db": ("crosstalk.spread_db", 0, None, False, False, ""),
    "CrosstalkConfig.skew_ui": ("crosstalk.skew_ui", 0, None, False, False, ""),
}


def _num(x: float) -> str:
    return f"{x:g}"


def bound_text(bound: tuple) -> str:
    """``'> 0'``, ``'>= 1'``, ``'< 0 dB/Hz'``, ``'in [0, 1)'``: what a
    ``FIELD_BOUNDS`` entry requires, as its message and a form's hint say it."""
    _, lo, hi, lo_open, hi_open, unit = bound
    if lo is not None and hi is not None:
        return (f"in {'(' if lo_open else '['}{_num(lo)}, {_num(hi)}{')' if hi_open else ']'}"
                + unit)
    if lo is not None:
        return f"{'>' if lo_open else '>='} {_num(lo)}{unit}"
    return f"{'<' if hi_open else '<='} {_num(hi)}{unit}"


def within_bound(bound: tuple, v) -> bool:
    """Whether ``v`` satisfies a ``FIELD_BOUNDS`` entry (``None``: not set)."""
    if v is None:
        return True
    _, lo, hi, lo_open, hi_open, _ = bound
    if lo is not None and (v <= lo if lo_open else v < lo):
        return False
    return not (hi is not None and (v >= hi if hi_open else v > hi))


def _bounded(obj, *names: str) -> None:
    """Check ``obj``'s fields against ``FIELD_BOUNDS``."""
    cls = type(obj).__name__
    for nm in names:
        b = FIELD_BOUNDS[f"{cls}.{nm}"]
        v = getattr(obj, nm)
        opt = " when set" if obj.__dataclass_fields__[nm].default is None else ""
        _require(within_bound(b, v), f"{b[0]} must be {bound_text(b)}{opt}, got {v}")


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
        _bounded(self, "n_freq", "length_m", "f_max")


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
        _bounded(self, "f0_hz", "rj_ui", "sj_ui", "dcd_ui", "sj_freq")


@dataclass(frozen=True)
class TxConfig:
    fir_taps: tuple[float, ...] = (1.0,)   # main cursor inferred: last-but-pre convention set in Phase 1
    fir_n_pre: int = 0
    swing: float = 1.0                     # differential peak-to-peak [V]
    rlm: float = 1.0                       # R_LM: inner levels compressed by this (PAM4 / PAM6 / PAM8)
    # PAM6's 5-bits-per-pair map (core/mapping.py): "corner32" sends 32 of
    # the 36 pairs, the four with both symbols outer left out
    pam6_map: Literal["corner32"] = "corner32"
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
    # white noise per UI at the DAC output [V rms] -- the driver's and DAC's
    # thermal noise, ahead of the driver pole and (optical) the modulator's
    # band limit, so everything downstream shapes it as it shapes the signal;
    # its own random stream, so it moves no other draw. 0 = off.
    noise_rms: float = 0.0
    # Edge timing lives on the clock. The four jitter fields that used to sit
    # here (rj_ui, sj_ui, sj_freq, dcd_ui) moved into ClockConfig when a clock
    # became something that could be a PLL profile rather than three numbers;
    # the YAML loader still accepts them at tx.* and migrates them.
    clock: ClockConfig = field(default_factory=ClockConfig)

    def __post_init__(self):
        _bounded(self, "swing", "rlm", "fir_n_pre", "dac_bits", "drv_compression",
                 "drv_p1db_v", "drv_oip3_v", "noise_rms")
        _require_in(self.pam6_map, {"corner32"}, "tx.pam6_map")
        _require(len(self.fir_taps) >= 1, "tx.fir_taps must not be empty")
        # a 1-tap "FIR" is a passthrough the engine skips outright, so its
        # n_pre is meaningless; only constrain it where the FIR is applied
        _require(len(self.fir_taps) == 1
                 or self.fir_n_pre < len(self.fir_taps),
                 f"tx.fir_n_pre must be < len(fir_taps)={len(self.fir_taps)}, "
                 f"got {self.fir_n_pre}")
        dac = self.dac_bits is not None
        _require(self.dac_fs is None or (dac and self.dac_fs > 0),
                 f"tx.dac_fs needs tx.dac_bits and must be > 0, got {self.dac_fs}")
        _require(0 <= self.dac_thermo_msbs <= (self.dac_bits if dac else 0),
                 f"tx.dac_thermo_msbs must be in [0, dac_bits], got {self.dac_thermo_msbs}")
        _require(self.dac_unit_sigma >= 0 and (dac or self.dac_unit_sigma == 0),
                 f"tx.dac_unit_sigma must be >= 0 and needs tx.dac_bits, got {self.dac_unit_sigma}")
        _require_in(self.drv_nl, ("none", "curve", "tanh", "cubic"), "tx.drv_nl")
        _require(self.drv_compression == 0.0 or self.drv_nl == "curve",
                 "tx.drv_compression is the 'curve' driver's parameter: set tx.drv_nl='curve'")
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
class AdcCalConfig:
    """Background calibration of the TI-ADC lanes (``cdr/adc_kernel.py``).

    ``"background"``: digital, after the quantizer, from the data itself --
    each lane's offset is its running mean (the data are zero-mean) and its
    gain the one that brings its running power to the lanes' mean, both
    one-pole estimates updated every conversion of that lane. With
    ``mu_skew`` > 0 each lane's sampling delay is trimmed too, by its own
    Mueller-Muller detector (trims kept zero-mean: the common phase is the
    CDR's). The steps trade settling against the residual the
    estimates keep from the data's own variance: about ``mu / 2`` of it.
    ``AdcConfig.calibrated`` stays the ideal model (mismatch zeroed).

    ``"foreground"``: at power-up, before the link runs -- the input shorted
    for ``fg_samples`` conversions per lane (offset: the lane's mean code),
    then held at +-``fg_ref`` of half full scale (gain: the lane's code
    difference over the known step), through the lane's own quantiser and
    ENOB noise. The estimates are then frozen; what is left is the
    estimation error. Skew is not foreground-calibrated. The draws come from
    their own generator (seeded from ``sim.seed``), so the link instance is
    the uncalibrated one's.
    """
    mode: Literal["off", "background", "foreground"] = "off"
    mu_offset: float = 2.0 ** -10       # per conversion of the lane
    mu_gain: float = 2.0 ** -10
    # skew: per-lane delay trim [samples per unit of the lane's MM detector,
    # normalised to the outer level]; 0 leaves skew uncalibrated
    mu_skew: float = 0.0
    fg_samples: int = 4096              # foreground: conversions per lane per measurement
    fg_ref: float = 0.8                 # foreground: reference, fraction of half full scale

    def __post_init__(self):
        _require_in(self.mode, {"off", "background", "foreground"}, "adc.cal.mode")
        _bounded(self, "fg_samples", "fg_ref", "mu_offset", "mu_gain", "mu_skew")


@dataclass(frozen=True)
class AdcConfig:
    n_bits: int = 8
    n_lanes: int = 16                   # time-interleave factor
    enob: Optional[float] = None        # effective bits; None = ideal quantizer
    fullscale: float = 1.0              # full-scale range [V], differential
    offset_sigma: float = 0.0           # per-lane offset mismatch sigma [V]
    gain_sigma: float = 0.0             # per-lane gain mismatch sigma [ratio]
    skew_sigma_ui: float = 0.0          # per-lane sampling skew sigma [UI]
    calibrated: bool = False            # ideal offset/gain calibration (mismatch zeroed)
    cal: AdcCalConfig = field(default_factory=AdcCalConfig)   # a real (background) one

    def __post_init__(self):
        _bounded(self, "n_bits", "n_lanes", "fullscale", "enob")
        _require(not (self.calibrated and self.cal.mode != "off"),
                 "adc.calibrated (ideal: mismatch zeroed) and adc.cal.mode (a real "
                 "calibration of the mismatch) exclude each other; choose one")


@dataclass(frozen=True)
class FfeConfig:
    n_pre: int = 4
    n_post: int = 10                    # 802.3dj reference receiver: 15 taps total
    adapt: Literal["none", "lms", "wiener"] = "none"
    mu: float = 1e-3

    def __post_init__(self):
        _bounded(self, "n_pre", "n_post", "mu")
        _require_in(self.adapt, {"none", "lms", "wiener"}, "ffe.adapt")


@dataclass(frozen=True)
class DfeConfig:
    n_taps: int = 1                     # 802.3dj reference receiver: 1 tap
    adapt: Literal["none", "lms", "sign_sign"] = "none"
    mu: float = 1e-3
    # mixed-signal slicer levels (dLev): "sign_sign" lets them follow the
    # equalised amplitude at the loop's sampling point by sign-sign LMS on
    # the decision error (step mu x |main cursor| per LMS batch), as a real
    # PAM4 receiver does; it runs only while the DFE adapts (adapt != none).
    # Until 2026-10-21 the levels stayed at the pulse peak's main cursor for
    # the whole run while the bang-bang loop locked 0.06 UI after it, which
    # read PAM4 32G 1.3-1.8x pessimistic (ROADMAP P2 #31). "none" keeps
    # that. The ADC receiver's LMS FFE pulls its output onto the levels and
    # is unaffected.
    level_adapt: Literal["none", "sign_sign"] = "sign_sign"
    # mixed-signal: the taps' and levels' sign-sign step while training
    # (data-aided), as a multiple of the tracking step. The taps start at the
    # pulse peak's cursors and the levels at its main cursor, not the lock's,
    # and a level offset the start does not know about (DCD moves PAM4 32G's
    # 5-10 mV) is 1e5-1e6 symbols away at the tracking step, against a
    # 6000-symbol warmup. 64 since 2026-11-01 (1 before): PAM4 32G, 8 seeds x
    # 3e5 symbols, BER in the first 25k / after 200k -- 7 mV 2.8e-5 / 1.1e-5
    # -> 1.5e-5 / 1.1e-5 (with init "lock"), 0.12 UI DCD at 6 mV 1.6e-4 /
    # 2.7e-5 -> 3.5e-5 / 1.9e-5; 256 flattens DCD a little more (ROADMAP
    # P2 #45). 1 keeps one step throughout.
    train_gain: float = 64.0
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
    # tap (and mixed-signal level) seed: "lock" the pulse's cursors where a
    # bang-bang loop locks (cdr.linear.lock_offset_samples) -- the default
    # since 2026-11-01; "cursor" at the pulse peak, as before, where PAM4
    # 32G's tap 1 started at -0.017 against the -0.029 it converges to over
    # 4e5 symbols (ROADMAP P2 #45); "zero" a cold start (exercises the full
    # adaptation transient). The ADC receiver has no bang-bang loop: "lock"
    # is "cursor" there.
    init: Literal["lock", "cursor", "zero"] = "lock"

    def __post_init__(self):
        _bounded(self, "n_taps", "mu", "loop_delay_ui", "sum_bw", "train_gain")
        _require_in(self.adapt, {"none", "lms", "sign_sign"}, "dfe.adapt")
        _require_in(self.level_adapt, {"none", "sign_sign"}, "dfe.level_adapt")
        _require_in(self.tap1_mode, {"direct", "unrolled"}, "dfe.tap1_mode")
        _require_in(self.init, {"lock", "cursor", "zero"}, "dfe.init")
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

    The Viterbi trellis spans L = len(pr.target) - 1 + memory cursors after
    the main one. ``state_memory`` = K < L makes it a reduced-state one
    (decision-feedback sequence estimation, Duel-Hallen & Heegard 1989): a
    state holds the last K symbols (n_levels**K states) and the trellis's
    other L - K cursors are cancelled with each survivor's own earlier
    decisions. None is K = L, the full trellis.
    """

    kind: Literal["none", "sliding", "viterbi"] = "none"
    memory: int = 1                     # residual postcursors in the trellis
    seq_len: int = 4                    # sliding-detector squared-error window
    margin: float = 0.0                 # sliding-detector acceptance margin
    state_memory: Optional[int] = None  # Viterbi: symbols a state holds (None = all)

    def __post_init__(self):
        _require_in(self.kind, {"none", "sliding", "viterbi"}, "mlsd.kind")
        _bounded(self, "memory", "state_memory", "seq_len", "margin")
        _require(self.state_memory is None or self.kind == "viterbi",
                 f"mlsd.state_memory reduces a Viterbi trellis; rx.mlsd.kind is {self.kind!r}")


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
        _bounded(self, "clamp", "loop_latency_symbols")


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
        _bounded(self, "n_symbols", "chunk_symbols", "cdr_settle", "train_symbols",
                 "warmup_discard")
        _require_in(self.engine, {"time", "stat", "both"}, "sim.engine")


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
    # background ADC calibration (adc.cal) in the fixed loop: fraction bits
    # of the offset / power registers, and of the lane gains
    cal_frac_bits: int = 16
    cal_gain_bits: int = 14

    def __post_init__(self):
        _require_in(self.mode, {"float", "fixed"}, "numeric.mode")
        _bounded(self, "pi_bits", "phase_frac_bits", "lms_guard_bits", "cal_frac_bits",
                 "cal_gain_bits", "mlsd_metric_bits")


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
    # Module equalisation: OIF CEI-112G-LINEAR lets an LPO module's driver and
    # TIA each carry a simple CTLE. Peaking [dB] of the one-zero, two-pole
    # ``afe.Ctle`` (poles at Nyquist and 2x Nyquist, unity DC gain, so OMA is
    # unchanged); 0 leaves the block out entirely rather than inserting an
    # all-pass. The driver's sits between segment A and the E/O -- ahead of
    # the photodiode noise and of a large-signal curve, which sees its
    # overshoot; the TIA's after the photodiode, so it lifts that noise along
    # with the signal. The 12 dB ceiling bounds a "simple" CTLE; it is not a
    # number from the OIF text, which is not reachable from here.
    drv_ctle_db: float = 0.0
    tia_ctle_db: float = 0.0
    # opto-electric (O/E): photodiode + TIA
    responsivity_a_w: float = 0.7
    tia_bw_hz: float = 40.0e9           # second-order (Butterworth) 3 dB bandwidth [Hz]
    tia_noise_pa_sqrthz: float = 12.0   # input-referred current noise density [pA/sqrt(Hz)]
    tz_ohm: float = 2000.0              # transimpedance, for reporting the physical output swing [ohm]

    def __post_init__(self):
        _require_in(self.kind, {"none", "vcsel_mmf", "eml_smf"}, "optical.kind")
        if self.kind == "none":
            return
        _bounded(self, "f_r_hz", "damping_hz", "er_db", "responsivity_a_w", "tia_bw_hz",
                 "tz_ohm", "wavelength_nm", "length_m", "tia_noise_pa_sqrthz", "rin_db_hz",
                 "li_compression", "drv_ctle_db", "tia_ctle_db")
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
    blocks are shared (``optics_placement`` says which a link is, for the
    power model). ``LinkConfig.topology`` set replaces ``channel`` with
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
    # Where the E/O and O/E sit. "pluggable": a module in a front-panel cage
    # at the end of the host's board trace (LPO, or a retimed module with
    # ``retimer``); "co_packaged": an optical engine on the host's package
    # substrate (CPO). Only the power model reads it (``analysis/power.py``
    # prices a co-packaged engine without the module's own overhead); what
    # the shorter trace does to the signal is ``seg_a`` / ``seg_b``.
    optics_placement: Literal["pluggable", "co_packaged"] = "pluggable"

    def __post_init__(self):
        _require_in(self.retimer, {"none", "both"}, "topology.retimer")
        _require_in(self.optics_placement, {"pluggable", "co_packaged"}, "topology.optics_placement")
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
    decision subtracts both controlled cursors. ``(1.0, a, b, c)`` adds a
    third, 1 + aD + bD^2 + cD^3 (a in [0, 3], b in [-1, 3], c in [-1, 1];
    E2PR4, (1 + D)^3, is the edge), and the trellis grows by N again.
    Longer targets are out of scope (the trellis grows as N^L).

    ``adapt`` chooses a instead of taking the configured one (receive side
    only): ``"mmse"`` solves the start-up pulse for the a that minimises the
    FFE's mean-square error with the main cursor held at 1 and keeps it;
    ``"lms"`` starts there and adapts a with the FFE, step ``mu``
    (dimensionless: normalised by the mean symbol power), b (and c) too for
    a longer target. Both minimise the same cost, so LMS tracks what the
    start-up solve predicted."""
    target: tuple[float, ...] = (1.0,)
    at: Literal["rx", "tx"] = "rx"
    adapt: Literal["none", "mmse", "lms"] = "none"
    mu: float = 2e-4

    def __post_init__(self):
        _require_in(self.at, {"rx", "tx"}, "pr.at")
        _require(len(self.target) in (1, 2, 3, 4),
                 f"pr.target must be (1.0,), (1.0, a), (1.0, a, b) or (1.0, a, b, c), "
                 f"got {self.target!r}")
        _require(self.target[0] == 1.0,
                 f"pr.target[0] must be 1.0 (the main cursor), got {self.target[0]}")
        if len(self.target) == 2:
            _require(0.0 <= self.target[1] <= 1.0,
                     f"pr.target alpha must be in [0, 1], got {self.target[1]}")
        if len(self.target) == 3:
            _require(0.0 <= self.target[1] <= 2.0 and -1.0 <= self.target[2] <= 1.0,
                     f"pr.target (1.0, a, b) needs a in [0, 2], b in [-1, 1], got {self.target!r}")
        if len(self.target) == 4:
            _require(0.0 <= self.target[1] <= 3.0 and -1.0 <= self.target[2] <= 3.0
                     and -1.0 <= self.target[3] <= 1.0,
                     f"pr.target (1.0, a, b, c) needs a in [0, 3], b in [-1, 3], c in [-1, 1], "
                     f"got {self.target!r}")
        _require_in(self.adapt, {"none", "mmse", "lms"}, "pr.adapt")
        _require(self.adapt == "none" or (len(self.target) >= 2 and self.at == "rx"),
                 f"pr.adapt={self.adapt!r} chooses a receive-side target: it needs "
                 f"pr.target=(1.0, a[, b[, c]]) and pr.at='rx', got {self.target!r}, at={self.at!r}")
        _bounded(self, "mu")

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
        return float(self.target[2]) if len(self.target) >= 3 else 0.0

    @property
    def gamma(self) -> float:
        return float(self.target[3]) if len(self.target) == 4 else 0.0


@dataclass(frozen=True)
class AggressorConfig:
    """One crosstalk aggressor lane, listed by itself in
    ``CrosstalkConfig.aggressors``.

    Its coupling -- from the aggressor's Tx voltage to the victim's receiver
    input -- is synthetic (a FEXT or NEXT shape whose UI-sampled peak is
    ``coupling_db``, ``f3db_hz`` its bandwidth, default 0.35 / UI) or, with
    ``file``, the path from ``aggressor_port`` to ``victim_port`` of a
    multi-port Touchstone (0-based single-ended ports;
    ``channel.crosstalk.import_xtalk``). ``skew_ui`` / ``ppm`` as for the
    bank in ``CrosstalkConfig``; ``seed`` draws the lane's data (default:
    the next seed after the bank's, so no two lanes share their data)."""

    kind: Literal["fext", "next"] = "fext"
    coupling_db: float = -30.0
    file: Optional[str] = None
    victim_port: int = 1
    aggressor_port: int = 2
    f3db_hz: Optional[float] = None
    skew_ui: float = 0.0
    ppm: float = 0.0
    seed: Optional[int] = None

    def __post_init__(self):
        _require_in(self.kind, {"fext", "next"}, "crosstalk aggressor kind")
        _bounded(self, "skew_ui", "f3db_hz")
        _require(abs(self.ppm) < 1e4, f"crosstalk aggressor ppm must be within +-1e4, got {self.ppm}")
        _require(self.victim_port >= 0 and self.aggressor_port >= 0
                 and self.victim_port != self.aggressor_port,
                 f"crosstalk aggressor ports must be distinct and >= 0, got "
                 f"victim {self.victim_port}, aggressor {self.aggressor_port}")


@dataclass(frozen=True)
class CrosstalkConfig:
    """The victim lane's crosstalk environment.

    A bank of synthetic lanes -- ``n_fext`` FEXT and ``n_next`` NEXT, each
    coupling drawn uniformly within +-``spread_db`` of ``coupling_db`` and
    each with its own data, all from ``seed`` (``aggressor_bank``) -- plus any
    listed one by one in ``aggressors``. Empty (the default) is no crosstalk.

    Where the lanes sit in time against the victim: synchronous lanes
    (``ppm`` 0, the victim's own clock) launch ``skew_ui`` UI later, so the
    victim samples every coupling at one fixed phase; quasi-synchronous lanes
    (their own reference clock, ``ppm`` away from the victim's) drift through
    every phase over a run -- the time engine draws their symbols at their
    own rate, the statistical engine averages over the phase. The engines
    build the aggressors from here; an explicit ``xtalk=`` argument replaces
    it (with a warning when both are given)."""

    n_fext: int = 0
    n_next: int = 0
    coupling_db: float = -30.0
    spread_db: float = 2.0
    seed: int = 0
    skew_ui: float = 0.0
    ppm: float = 0.0
    aggressors: tuple[AggressorConfig, ...] = ()

    def __post_init__(self):
        _require(self.n_fext >= 0 and self.n_next >= 0,
                 f"crosstalk.n_fext / n_next must be >= 0, got {self.n_fext} / {self.n_next}")
        _bounded(self, "spread_db", "skew_ui")
        _require(abs(self.ppm) < 1e4, f"crosstalk.ppm must be within +-1e4, got {self.ppm}")
        # a mapping per aggressor is accepted (a flat override, a form) and
        # built here, so the frozen config always holds AggressorConfig
        aggs = tuple(AggressorConfig(**a) if isinstance(a, dict) else a for a in self.aggressors)
        _require(all(isinstance(a, AggressorConfig) for a in aggs),
                 "crosstalk.aggressors must be AggressorConfig entries or mappings")
        object.__setattr__(self, "aggressors", aggs)

    @property
    def active(self) -> bool:
        """Any aggressor at all."""
        return self.n_fext + self.n_next + len(self.aggressors) > 0


@dataclass(frozen=True)
class LinkConfig:
    # defaults define the product mixed-signal anchor point: 16 GBd NRZ
    # (the canonical validated configuration, well inside the comfort zone)
    modulation: Literal["nrz", "pam4", "pam6", "pam8"] = "nrz"
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
    # The victim lane's crosstalk environment (none by default)
    crosstalk: CrosstalkConfig = field(default_factory=CrosstalkConfig)

    def __post_init__(self):
        _require_in(self.modulation, {"nrz", "pam4", "pam6", "pam8"}, "modulation")
        # A form needs an "off" state it can express as a field value, so a
        # topology whose optics are "none" is accepted and normalised here to
        # the one representation the engines test for: topology is None.
        if self.topology is not None and self.topology.optical.kind == "none":
            object.__setattr__(self, "topology", None)
        # a non-positive osr silently yields a negative/infinite dt
        _bounded(self, "symbol_rate", "osr")
        # a DAC that cannot even hold the symbol alphabet is a typo, not a design
        n_lv = self.n_levels
        _require(self.tx.dac_bits is None or 2 ** self.tx.dac_bits >= n_lv,
                 f"tx.dac_bits={self.tx.dac_bits} cannot represent {n_lv} {self.modulation} levels")
        # the mixed-signal slicer has no digital equaliser to shape a target
        # with; that receiver's own blocks are where the two may differ
        _require(not (self.pr.active and self.rx.arch == "mixed_signal"),
                 "pr.target with rx.arch='mixed_signal': partial-response "
                 "equalisation needs the ADC receiver's digital FFE")
        # the sliding detector (and its bit-true / RTL twins) models one
        # postcursor; under a three-cursor target it reads the second
        # controlled cursor as error and flips correct decisions (SER 0 ->
        # 2e-2 on the 106 GBd preset)
        _require(not (self.rx.mlsd.kind == "sliding" and len(self.pr.target) > 2),
                 "rx.mlsd.kind='sliding' with a three- or four-cursor pr.target: the sliding "
                 "detector models one postcursor; use rx.mlsd.kind='viterbi'")
        # a reduced trellis keeps K of the L = len(pr.target) - 1 + memory
        # trellis cursors in its states
        k_state = self.rx.mlsd.state_memory
        l_trellis = len(self.pr.target) - 1 + self.rx.mlsd.memory
        _require(k_state is None or k_state <= l_trellis,
                 f"rx.mlsd.state_memory={k_state} is more than the trellis's {l_trellis} cursors "
                 f"(len(pr.target) - 1 + mlsd.memory)")
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
        samples on a multi-level format, raw ADC samples on NRZ."""
        pd = self.rx.cdr.pd_input
        if pd != "auto":
            return pd
        return "ffe" if self.n_levels > 2 else "adc"

    @property
    def n_levels(self) -> int:
        """Levels of the alphabet: 2 / 4 / 6 / 8."""
        from ..core.mapping import n_levels_of

        return n_levels_of(self.modulation)

    @property
    def bits_per_symbol(self) -> int | float:
        """1 / 2 / 3 for NRZ / PAM4 / PAM8; 2.5 for PAM6 (five bits per pair
        of symbols). An int wherever it is one."""
        from ..core.mapping import bits_per_symbol_of

        return bits_per_symbol_of(self.modulation)

    @property
    def data_rate(self) -> float:
        """Data rate [bit/s] = symbol_rate * bits_per_symbol."""
        return self.symbol_rate * self.bits_per_symbol


def field_bound(path: str) -> tuple | None:
    """The ``FIELD_BOUNDS`` entry behind a dotted ``LinkConfig`` path
    (``"rx.adc.n_bits"`` is ``AdcConfig.n_bits``), or ``None``."""
    import dataclasses
    import typing

    cls = LinkConfig
    parts = path.split(".")
    for name in parts[:-1]:
        hints = typing.get_type_hints(cls)
        t = hints.get(name)
        args = [a for a in typing.get_args(t) if dataclasses.is_dataclass(a)]
        cls = args[0] if args else t
        if not dataclasses.is_dataclass(cls):
            return None
    return FIELD_BOUNDS.get(f"{cls.__name__}.{parts[-1]}")
