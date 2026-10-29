"""What a form says about a field beyond its label, for every client.

The desktop GUI and the phone app both build their parameter forms from
``config_bridge.SECTIONS``; this module is the rest of what a form needs, kept
here so the two do not each carry a copy:

- ``HELP``: a sentence on what the field does to the link.
- ``COMMON``: the fields shown before "Advanced" -- what one changes in an
  ordinary study.
- ``STAGES``: the link as a chain of stages, each holding config sections.
- Show-when rules (``SHOW_IF``, ``SECTION_SHOW_IF``, ``STAGE_SHOW_IF``): what
  the current configuration does not use is hidden -- the ADC group on a
  mixed-signal receiver, the RLGC line on a Touchstone channel, the 47 optical
  fields on an electrical link. Hidden fields keep their values and are still
  read back into the config; hiding only stops them being in the way.

The rules are data, not code, so they cross the JSON boundary: a rule is
``{"path": p, "in": [...]}``, ``{"path": p, "not_in": [...]}``,
``{"path": p, "min_items": n}`` (a comma-separated or list value with at least
``n`` entries), ``{"all": [...]}`` or ``{"any": [...]}``. A client need not
evaluate them itself: ``api`` ``derive`` returns what is hidden for the values
it is given, from :func:`hidden` here, so there is one evaluator.

Values are form values as clients send them: the desktop's widget values or
the phone's strings (booleans as booleans, blank as ""). The rules only compare
enum strings and count list items, which both spell the same way.
"""

from __future__ import annotations

from typing import Any

Values = dict[str, Any]
Rule = dict[str, Any]

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


#: The fields a stage shows before "Advanced".
COMMON: set[str] = {
    "modulation", "symbol_rate", "sim.n_symbols", "sim.seed", "sim.engine", "sim.pattern",
    "tx.fir_taps", "tx.fir_n_pre", "tx.swing", "tx.rlm", "tx.noise_rms",
    "tx.clock.kind", "tx.clock.file", "tx.clock.rj_ui",
    "channel.kind", "channel.file", "channel.length_m", "channel.r_skin",
    "channel.loss_tangent", "crosstalk.n_fext", "crosstalk.n_next",
    "crosstalk.coupling_db", "topology.optical.kind", "topology.optical.oma_dbm",
    "topology.optical.er_db", "topology.optical.rin_db_hz", "topology.optical.length_m",
    "topology.retimer",
    "rx.noise_rms", "rx.clock.rj_ui", "rx.ctle.enable", "rx.ctle.peak_db", "rx.ctle.gdc_db",
    "rx.adc.n_bits", "rx.adc.enob", "rx.adc.n_lanes", "rx.adc.fullscale", "rx.adc.cal.mode",
    "rx.ffe.n_pre", "rx.ffe.n_post", "rx.ffe.adapt", "rx.dfe.n_taps", "rx.dfe.adapt",
    "rx.mlsd.kind", "pr.target",
    "rx.cdr.kind", "rx.cdr.kp_shift", "rx.cdr.ki_shift",
}


def _in(path: str, *allowed: Any) -> Rule:
    return {"path": path, "in": list(allowed)}


def _not_in(path: str, *excluded: Any) -> Rule:
    return {"path": path, "not_in": list(excluded)}


def _all(*rules: Rule) -> Rule:
    return {"all": list(rules)}


#: Blank spellings of an unset enum: None from Python, "" from a form.
_UNSET = ("none", "", None)

ADC = _in("rx.arch", "adc_dsp")
OPTICAL = _not_in("topology.optical.kind", *_UNSET)

#: (stage id, title, the config sections it holds)
STAGES: list[tuple[str, str, list[str]]] = [
    ("link", "Link & run", ["link", "sim", "numeric"]),
    ("tx", "Transmitter", ["tx"]),
    ("channel", "Channel", ["channel", "crosstalk", "topology"]),
    ("fe", "Receiver front end", ["rx", "ctle"]),
    ("adc", "ADC", ["adc"]),
    ("eq", "Equaliser", ["ffe", "dfe", "mlsd", "pr"]),
    ("cdr", "CDR", ["cdr"]),
]
STAGE_SHOW_IF: dict[str, Rule] = {"adc": ADC}

SECTION_SHOW_IF: dict[str, Rule] = {
    "adc": ADC,
    # a partial-response target other than the plain (1.0,) keeps the group
    # on a mixed-signal receiver, so the config error it causes there can be
    # fixed where it is reported
    "pr": {"any": [ADC, {"path": "pr.target", "min_items": 2}]},
}

SHOW_IF: dict[str, Rule] = {
    "tx.rlm": _not_in("modulation", "nrz"),
    "tx.pam6_map": _in("modulation", "pam6"),
    "tx.drv_compression": _in("tx.drv_nl", "curve"),
    "tx.drv_p1db_v": _in("tx.drv_nl", "tanh"),
    "tx.drv_oip3_v": _in("tx.drv_nl", "cubic"),
    "tx.clock.file": _in("tx.clock.kind", "profile"),
    "tx.clock.f0_hz": _in("tx.clock.kind", "profile"),
    "rx.clock.file": _in("rx.clock.kind", "profile"),
    "rx.clock.f0_hz": _in("rx.clock.kind", "profile"),
    "rx.mlsd.memory": _not_in("rx.mlsd.kind", *_UNSET),
    "rx.mlsd.state_memory": _in("rx.mlsd.kind", "viterbi"),
    "rx.mlsd.seq_len": _in("rx.mlsd.kind", "sliding"),
    "rx.mlsd.margin": _in("rx.mlsd.kind", "sliding"),
}
for _p in ("rx.adc.cal.mu_offset", "rx.adc.cal.mu_gain", "rx.adc.cal.mu_skew"):
    SHOW_IF[_p] = _in("rx.adc.cal.mode", "background")
for _p in ("rx.adc.cal.fg_samples", "rx.adc.cal.fg_ref"):
    SHOW_IF[_p] = _in("rx.adc.cal.mode", "foreground")
# the channel: a file or a line, never both
for _p in ("channel.file", "channel.lane", "channel.renumber"):
    SHOW_IF[_p] = _in("channel.kind", "touchstone")
for _p in ("channel.length_m", "channel.rdc", "channel.r_skin", "channel.l_per_m",
           "channel.g_per_m", "channel.c_per_m", "channel.loss_tangent"):
    SHOW_IF[_p] = _in("channel.kind", "analytic")


def _topology_rule(path: str) -> Rule | None:
    """Every optical field but the switch needs optics on; the segments
    their kind's half; the retimer's ends the retimer on."""
    if not path.startswith("topology.") or path == "topology.optical.kind":
        return None
    for seg in ("seg_a", "seg_b"):
        pre = f"topology.{seg}."
        if path.startswith(pre) and path != pre + "kind":
            kind = "touchstone" if path == pre + "file" else "analytic"
            return _all(OPTICAL, _in(pre + "kind", kind))
    if path.startswith(("topology.retimer_rx.", "topology.retimer_tx.")):
        return _all(OPTICAL, _not_in("topology.retimer", *_UNSET))
    return OPTICAL


def _items(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        return len(value)
    return len([t for t in str(value or "").split(",") if t.strip()])


def holds(rule: Rule | None, values: Values) -> bool:
    """Whether ``rule`` holds for these form values (no rule: always)."""
    if not rule:
        return True
    if "all" in rule:
        return all(holds(r, values) for r in rule["all"])
    if "any" in rule:
        return any(holds(r, values) for r in rule["any"])
    v = values.get(rule["path"])
    if "in" in rule:
        return v in rule["in"]
    if "not_in" in rule:
        return v not in rule["not_in"]
    if "min_items" in rule:
        return _items(v) >= rule["min_items"]
    raise ValueError(f"unknown rule {rule!r}")


def field_rule(path: str) -> Rule | None:
    """The show-when rule of the field at ``path``, if it has one."""
    return SHOW_IF.get(path) or _topology_rule(path)


def field_visible(path: str, values: Values) -> bool:
    return holds(field_rule(path), values)


def section_visible(sid: str, values: Values) -> bool:
    return holds(SECTION_SHOW_IF.get(sid), values)


def stage_visible(sid: str, values: Values) -> bool:
    return holds(STAGE_SHOW_IF.get(sid), values)


def hidden(values: Values, sections) -> dict[str, list[str]]:
    """What a form hides for ``values``: field paths, section ids, stage ids.
    ``sections`` is ``config_bridge.SECTIONS``."""
    return {
        "fields": [f["path"] for _, _, fs in sections for f in fs
                   if not field_visible(f["path"], values)],
        "sections": [sid for sid, _, _ in sections if not section_visible(sid, values)],
        "stages": [sid for sid, _, _ in STAGES if not stage_visible(sid, values)],
    }


def annotate(sections) -> None:
    """Add ``help``, ``common``, ``stage`` and ``show_if`` to every field spec
    of ``config_bridge.SECTIONS`` in place, so the schema carries them."""
    stage_of = {sec: sid for sid, _, secs in STAGES for sec in secs}
    for sid, _, fields in sections:
        for f in fields:
            f["help"] = HELP.get(f["path"], "")
            f["common"] = f["path"] in COMMON
            f["stage"] = stage_of.get(sid)
            rule = field_rule(f["path"])
            if rule is not None:
                f["show_if"] = rule
