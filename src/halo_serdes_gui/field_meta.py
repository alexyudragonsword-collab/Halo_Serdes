"""What the form says about a field beyond its label, and when it shows it.

``HELP`` is the hover text on a field's label: what the number does to the
link, in a sentence. ``SECTION_SHOWN`` and ``FIELD_SHOWN`` hide what the
current configuration does not use -- the ADC and partial-response groups on a
mixed-signal receiver, the RLGC line on a Touchstone channel, the 47 optical
fields on an electrical link. Hidden fields keep their values and are still
read back into the config; hiding only stops them being in the way.

Desktop only for now. The Android client reads ``halo_serdes_app.config_bridge``
and has none of this; moving it into that schema, so both clients share it, is
the GUI plan's phase 3 (ROADMAP).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Values = dict[str, Any]

HELP: dict[str, str] = {
    # link
    "modulation": "Line code. PAM4 carries 2 bits per symbol at a third of NRZ's eye height.",
    "symbol_rate": "Symbols per second. Data rate = symbol rate × bits per symbol.",
    "osr": "Waveform samples per UI. Higher resolves edges better and runs slower.",
    "precode": "1+D precoding: turns a DFE error burst into two isolated errors.",
    # channel
    "channel.kind": "touchstone reads an S-parameter file; analytic builds an RLGC line.",
    "channel.file": "S-parameter file (.s4p, .s8p, ...). Relative paths resolve against the bundle.",
    "channel.lane": "Which differential pair of a multi-lane file is the victim.",
    "channel.f_max": "Upper edge of the analysis frequency grid; empty is 2/UI.",
    "channel.length_m": "Length of the analytic line. Loss grows roughly linearly with it.",
    "channel.rdc": "DC resistance per metre.",
    "channel.r_skin": "Skin-effect resistance: the part of the loss that grows with √f.",
    "channel.loss_tangent": "Dielectric loss: the part of the loss that grows with f.",
    # tx
    "tx.fir_taps": "Transmit FIR taps, comma-separated, precursors first.",
    "tx.fir_n_pre": "How many of the FIR taps come before the main cursor.",
    "tx.swing": "Differential peak-to-peak output before the FIR.",
    "tx.rlm": "Level-mismatch ratio R_LM in (0, 1]: the inner PAM levels are compressed by it; 1 is even spacing.",
    "tx.bw": "Driver bandwidth; empty is an ideal driver.",
    "tx.dac_bits": "DAC resolution; empty is an ideal (unquantised) DAC.",
    "tx.noise_rms": "White noise added at the transmitter, per UI.",
    "tx.drv_nl": "Driver compression model; each one has its own parameter below.",
    "tx.clock.kind": "white: random jitter only. profile: a measured phase-noise profile.",
    "tx.clock.rj_ui": "Random jitter on every transmit edge, RMS in UI.",
    # rx
    "rx.arch": "mixed_signal: analog CTLE + DFE + bang-bang CDR. adc_dsp: ADC, then FFE/DFE/MLSD in DSP.",
    "rx.vga_gain": "Gain ahead of the slicer or ADC.",
    "rx.noise_rms": "Input-referred white noise at the receiver.",
    "rx.clock.rj_ui": "Random jitter on the sampling clock, RMS in UI.",
    # ctle
    "rx.ctle.peak_db": "Gain at Nyquist relative to DC: how much high-frequency loss the CTLE undoes.",
    "rx.ctle.gdc_db": "CTLE gain at DC.",
    "rx.ctle.fz": "CTLE zero; empty places it from the peaking.",
    # adc
    "rx.adc.n_bits": "ADC resolution.",
    "rx.adc.n_lanes": "Time-interleaved sub-ADCs. Their mismatches below add spurs at fs/N.",
    "rx.adc.enob": "Effective bits: sets the ADC's noise; empty is an ideal quantiser.",
    "rx.adc.fullscale": "Input range the codes span.",
    "rx.adc.skew_sigma_ui": "Sampling-time mismatch between interleaved lanes, RMS in UI.",
    "rx.adc.cal.mode": "Mismatch calibration: background tracks while running, foreground trains first.",
    # ffe / dfe / mlsd
    "rx.ffe.n_pre": "FFE taps ahead of the main cursor (cancel precursor ISI).",
    "rx.ffe.n_post": "FFE taps after the main cursor.",
    "rx.ffe.adapt": "How the FFE taps are found: LMS while running, or a one-shot Wiener solve.",
    "rx.dfe.n_taps": "Postcursors the DFE cancels from past decisions.",
    "rx.dfe.adapt": "DFE adaptation: LMS, or sign-sign as analog DFEs do.",
    "rx.dfe.tap1_mode": "unrolled precomputes both tap-1 outcomes to break the timing loop.",
    "rx.dfe.loop_delay_ui": "Delay of the mixed-signal DFE feedback; past 1 UI the first tap is no longer cancelled in time.",
    "rx.mlsd.kind": "Sequence detector after the equaliser; none is symbol-by-symbol.",
    "rx.mlsd.memory": "Postcursors the trellis models.",
    # cdr
    "rx.cdr.kind": "bang_bang: early/late votes, the usual mixed-signal choice. mueller_muller: baud-rate, from the ADC samples.",
    "rx.cdr.kp_shift": "Proportional gain 2^-k: smaller k tracks faster and dithers more.",
    "rx.cdr.ki_shift": "Integral gain 2^-k: follows frequency offset.",
    "rx.cdr.loop_latency_symbols": "Delay from phase decision to the clock moving.",
    # sim
    "sim.n_symbols": "Symbols simulated in the time domain. Zero errors only bounds the BER at about 1/(bits).",
    "sim.seed": "Random seed for data and noise. Same seed, same result.",
    "sim.engine": "time: Monte-Carlo waveform. stat: StatEye PDF (extrapolates). both: run and compare.",
    "sim.pattern": "Data pattern. PRBS7 repeats often (good for jitter decomposition); PRBS31 is closest to random.",
    "sim.cdr_settle": "Symbols the CDR gets to lock before counting.",
    "sim.train_symbols": "Symbols used to adapt the equalisers before counting.",
    # crosstalk
    "crosstalk.n_fext": "Far-end aggressor lanes (same direction as the victim).",
    "crosstalk.n_next": "Near-end aggressor lanes (opposite direction).",
    "crosstalk.coupling_db": "Peak coupling of each aggressor into the victim.",
    "crosstalk.ppm": "Aggressor clock offset; 0 is synchronous, so the skew below applies.",
    # optical
    "topology.optical.kind": "none: electrical link. vcsel_mmf / eml_smf replace the channel with segment A, optics, segment B.",
    "topology.optical.oma_dbm": "Optical modulation amplitude of the outer levels.",
    "topology.optical.er_db": "Extinction ratio: outer high over outer low power.",
    "topology.optical.rin_db_hz": "Laser relative intensity noise; it scales with the level's power.",
    "topology.optical.length_m": "Fibre length.",
    "topology.optical.tia_noise_pa_sqrthz": "Input-referred TIA current noise density.",
}


def _eq(path: str, *allowed: Any) -> Callable[[Values], bool]:
    """Shown when the field at ``path`` holds one of ``allowed``."""
    return lambda v: v.get(path) in allowed


def _ne(path: str, *excluded: Any) -> Callable[[Values], bool]:
    """Shown unless the field at ``path`` holds one of ``excluded``."""
    return lambda v: v.get(path) not in excluded


_ADC = _eq("rx.arch", "adc_dsp")
_OPTICAL = _ne("topology.optical.kind", "none", None, "")


def _pr_set(v: Values) -> bool:
    """A partial-response target other than the plain (1.0,) is in the form.
    Kept visible on a mixed-signal receiver so the config error it causes
    there can be fixed where it is reported."""
    return len([t for t in str(v.get("pr.target") or "1").split(",") if t.strip()]) > 1


SECTION_SHOWN: dict[str, Callable[[Values], bool]] = {
    "adc": _ADC,
    "pr": lambda v: _ADC(v) or _pr_set(v),
}

FIELD_SHOWN: dict[str, Callable[[Values], bool]] = {
    "tx.rlm": _ne("modulation", "nrz"),
    "tx.pam6_map": _eq("modulation", "pam6"),
    "tx.drv_compression": _eq("tx.drv_nl", "curve"),
    "tx.drv_p1db_v": _eq("tx.drv_nl", "tanh"),
    "tx.drv_oip3_v": _eq("tx.drv_nl", "cubic"),
    "tx.clock.file": _eq("tx.clock.kind", "profile"),
    "tx.clock.f0_hz": _eq("tx.clock.kind", "profile"),
    "rx.clock.file": _eq("rx.clock.kind", "profile"),
    "rx.clock.f0_hz": _eq("rx.clock.kind", "profile"),
    "rx.mlsd.memory": _ne("rx.mlsd.kind", "none"),
    "rx.mlsd.state_memory": _eq("rx.mlsd.kind", "viterbi"),
    "rx.mlsd.seq_len": _eq("rx.mlsd.kind", "sliding"),
    "rx.mlsd.margin": _eq("rx.mlsd.kind", "sliding"),
}
for _p in ("rx.adc.cal.mu_offset", "rx.adc.cal.mu_gain", "rx.adc.cal.mu_skew"):
    FIELD_SHOWN[_p] = _eq("rx.adc.cal.mode", "background")
for _p in ("rx.adc.cal.fg_samples", "rx.adc.cal.fg_ref"):
    FIELD_SHOWN[_p] = _eq("rx.adc.cal.mode", "foreground")
# the channel: a file or a line, never both
for _p in ("channel.file", "channel.lane", "channel.renumber"):
    FIELD_SHOWN[_p] = _eq("channel.kind", "touchstone")
for _p in ("channel.length_m", "channel.rdc", "channel.r_skin", "channel.l_per_m",
           "channel.g_per_m", "channel.c_per_m", "channel.loss_tangent"):
    FIELD_SHOWN[_p] = _eq("channel.kind", "analytic")


def field_shown(path: str, values: Values) -> bool:
    """Whether the form shows the field at ``path`` for these values."""
    if path.startswith("topology.") and path != "topology.optical.kind" \
            and not _OPTICAL(values):
        return False
    if path.startswith(("topology.retimer_rx.", "topology.retimer_tx.")):
        return values.get("topology.retimer") not in ("none", None, "")
    for seg in ("seg_a", "seg_b"):
        pre = f"topology.{seg}."
        if path.startswith(pre) and path != pre + "kind":
            kind = values.get(pre + "kind")
            if path == pre + "file":
                return kind == "touchstone"
            return kind == "analytic"
    rule = FIELD_SHOWN.get(path)
    return rule is None or bool(rule(values))


def section_shown(sid: str, values: Values) -> bool:
    """Whether the form shows the section ``sid`` for these values."""
    rule = SECTION_SHOWN.get(sid)
    return rule is None or bool(rule(values))
