"""Bridge between the GUI form and the frozen ``LinkConfig`` dataclasses.

Single source of truth for configuration is ``halo_serdes.config`` — this
module only translates form values <-> ``LinkConfig`` and handles YAML I/O and
presets. It adds no simulation logic.

The form is described declaratively by ``SECTIONS`` (a spec derived from the
schema's field inventory). Each field carries a dotted ``path`` into
``LinkConfig`` so a flat ``{path: value}`` dict round-trips through the
library's ``apply_overrides`` (which does *not* coerce — coercion happens
here) and ``config_to_values`` (for populating the form from a preset).
"""

from __future__ import annotations

import dataclasses
import io
import re
from pathlib import Path
from typing import Any


from halo_serdes.config import LinkConfig, apply_overrides, load_config
from halo_serdes.config.schema import SCHEMA_VERSION

#: Environment override for the directory holding ``configs/`` (and any other
#: bundled data). Android is the reason it exists: Chaquopy's importer serves
#: Python modules out of an asset archive, so ``__file__``-relative lookups do
#: not reach the repo's ``configs/`` at all. The host app extracts the assets
#: into its private storage and points this at them before starting Python.
DATA_DIR_ENV = "HALO_SERDES_DATA_DIR"


def _env_data_dir() -> Path | None:
    import os

    raw = os.environ.get(DATA_DIR_ENV)
    return Path(raw) if raw else None


def _find_configs_dir() -> Path:
    """Locate the bundled ``configs/`` dir in source or a frozen build.

    Source layout keeps them at the repo root; a PyInstaller/Nuitka bundle ships
    them next to the executable (and PyInstaller also extracts to ``_MEIPASS``).
    Return the first candidate that exists (falling back to the source path).
    """
    import sys

    here = Path(__file__).resolve()
    cands = []
    env = _env_data_dir()
    if env:                                            # host-supplied (Android)
        cands += [env / "configs", env]
    meipass = getattr(sys, "_MEIPASS", None)           # PyInstaller extract dir
    if meipass:
        cands.append(Path(meipass) / "configs")
    # __file__-relative: source (repo/src/pkg -> repo/configs) and Nuitka
    # onefile, which extracts data into a temp dir beside the package tree
    # (sys.executable points at the launcher there, so anchor on __file__).
    for up in (1, 2, 3):
        if up < len(here.parents):
            cands.append(here.parents[up] / "configs")
    cands.append(here.parent / "configs")              # bundled beside the package
    try:
        exe = Path(sys.executable).resolve().parent    # Nuitka/PyInstaller standalone
        cands.append(exe / "configs")
        cands.append(exe / "_internal" / "configs")
    except Exception:
        pass
    for c in cands:
        if c.is_dir():
            return c
    return here.parents[2] / "configs"                 # source-layout fallback


CONFIGS_DIR = _find_configs_dir()


def _data_roots() -> list[Path]:
    import sys

    roots = [Path.cwd()]
    env = _env_data_dir()
    if env:
        roots.append(env)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    try:
        exe = Path(sys.executable).resolve().parent
        roots += [exe, exe / "_internal"]
    except Exception:
        pass
    roots.append(CONFIGS_DIR.parent)                    # resolved bundle/repo root
    # __file__-relative roots cover Nuitka onefile (temp extract) + source
    here = Path(__file__).resolve()
    roots += [here.parents[up] for up in (1, 2, 3) if up < len(here.parents)]
    return roots


def bundled_channels() -> list[dict]:
    """Touchstone files shipped with this build, as ``{name, path, bytes}``.

    Needed because a bundled file is not otherwise *findable*. On the desktop
    they sit in the repo and can simply be browsed to; inside an Android app
    they live in private storage, where the system document picker cannot see
    them — so a client with no list has no way to offer them, and two of the
    three shipped files are named by no preset at all.

    Searched over the same roots ``resolve_data_file`` uses, so whatever this
    lists is exactly what the engine would be able to open.
    """
    seen: dict[str, Path] = {}
    for root in _data_roots():
        d = root / "data" / "channels"
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            # .s2p/.s4p/.s8p/.s12p — the port count is part of the suffix.
            if re.fullmatch(r"\.s\d+p", f.suffix, re.IGNORECASE) and f.is_file():
                seen.setdefault(f.name, f)
    return [{"name": n, "path": str(p), "bytes": p.stat().st_size}
            for n, p in sorted(seen.items())]


def resolve_data_file(rel: str) -> str:
    """Resolve a (possibly relative) channel file against candidate roots.

    Preset touchstone paths like ``data/channels/foo.s4p`` are repo-relative;
    inside a frozen bundle they live next to the executable. Return the first
    existing absolute path, or the input unchanged (so the open fails with a
    clear message)."""
    p = Path(rel)
    if p.is_absolute() and p.exists():
        return str(p)
    for root in _data_roots():
        cand = root / rel
        if cand.exists():
            return str(cand.resolve())
    return rel

# --- field kinds -----------------------------------------------------------
# float / int / bool / enum / str / opt_float / opt_int / tuple_float /
# opt_tuple_float.  `scale` (optional) presents a Hz value in GHz/GBd etc.


def _f(path, label, kind, **kw):
    d = {"path": path, "label": label, "kind": kind}
    d.update(kw)
    return d


PATTERNS = ["prbs7", "prbs13", "prbs31", "prbs13q", "prbs31q", "prqs10"]

# Grouped field specification. Each group -> (id, title, [fields]).
SECTIONS: list[tuple[str, str, list[dict]]] = [
    ("link", "Link", [
        _f("modulation", "Modulation", "enum", options=["nrz", "pam4"]),
        _f("symbol_rate", "Symbol rate [GBd]", "float", scale=1e9),
        _f("osr", "Oversampling (OSR)", "int"),
        _f("precode", "1+D precoding", "bool"),
    ]),
    ("channel", "Channel", [
        _f("channel.kind", "Kind", "enum", options=["touchstone", "analytic"]),
        _f("channel.file", "Touchstone file", "opt_str"),
        _f("channel.lane", "Lane (8/12-port)", "int"),
        _f("channel.renumber", "Auto port renumber", "bool"),
        _f("channel.zs_diff", "Source term [ohm]", "float"),
        _f("channel.zl_diff", "Load term [ohm]", "float"),
        _f("channel.f_max", "f_max [GHz]", "opt_float", scale=1e9),
        _f("channel.n_freq", "Freq grid points", "int"),
        _f("channel.length_m", "Length [m] (analytic)", "float"),
        _f("channel.rdc", "R_dc [ohm/m]", "float"),
        _f("channel.r_skin", "R_skin [ohm/(m·√Hz)]", "float"),
        _f("channel.l_per_m", "L [H/m]", "float"),
        _f("channel.g_per_m", "G [S/m]", "float"),
        _f("channel.c_per_m", "C [F/m]", "float"),
        _f("channel.loss_tangent", "Loss tangent", "float"),
    ]),
    ("tx", "Transmitter", [
        _f("tx.fir_taps", "FIR taps [pre..,main,post..]", "tuple_float"),
        _f("tx.fir_n_pre", "FIR precursors", "int"),
        _f("tx.swing", "Swing [V pp]", "float"),
        _f("tx.rlm", "RLM (PAM4)", "float"),
        _f("tx.bw", "Driver BW [GHz]", "opt_float", scale=1e9),
        _f("tx.rj_ui", "RJ sigma [UI]", "float"),
        _f("tx.sj_ui", "SJ amplitude [UI]", "float"),
        _f("tx.sj_freq", "SJ freq [GHz]", "float", scale=1e9),
        _f("tx.dcd_ui", "DCD [UI]", "float"),
    ]),
    ("rx", "Receiver", [
        _f("rx.arch", "Architecture", "enum",
           options=["mixed_signal", "adc_dsp"]),
        _f("rx.vga_gain", "VGA gain", "float"),
        _f("rx.noise_rms", "Input noise RMS [V]", "float"),
    ]),
    ("ctle", "CTLE", [
        _f("rx.ctle.enable", "Enable", "bool"),
        _f("rx.ctle.gdc_db", "DC gain [dB]", "float"),
        _f("rx.ctle.peak_db", "Peaking [dB]", "float"),
        _f("rx.ctle.fz", "Zero [GHz]", "opt_float", scale=1e9),
        _f("rx.ctle.fp1", "Pole 1 [GHz]", "opt_float", scale=1e9),
        _f("rx.ctle.fp2", "Pole 2 [GHz]", "opt_float", scale=1e9),
    ]),
    ("adc", "ADC (adc_dsp)", [
        _f("rx.adc.n_bits", "Bits", "int"),
        _f("rx.adc.n_lanes", "TI lanes", "int"),
        _f("rx.adc.enob", "ENOB", "opt_float"),
        _f("rx.adc.fullscale", "Full scale [V]", "float"),
        _f("rx.adc.offset_sigma", "Offset mismatch sigma [V]", "float"),
        _f("rx.adc.gain_sigma", "Gain mismatch sigma", "float"),
        _f("rx.adc.skew_sigma_ui", "Skew sigma [UI]", "float"),
        _f("rx.adc.calibrated", "Calibrated", "bool"),
    ]),
    ("ffe", "FFE", [
        _f("rx.ffe.n_pre", "Precursor taps", "int"),
        _f("rx.ffe.n_post", "Postcursor taps", "int"),
        _f("rx.ffe.adapt", "Adapt", "enum", options=["none", "lms", "wiener"]),
        _f("rx.ffe.mu", "mu", "float"),
    ]),
    ("dfe", "DFE", [
        _f("rx.dfe.n_taps", "Taps", "int"),
        _f("rx.dfe.adapt", "Adapt", "enum",
           options=["none", "lms", "sign_sign"]),
        _f("rx.dfe.mu", "mu", "float"),
        _f("rx.dfe.tap_limits", "Tap limits", "opt_tuple_float"),
        _f("rx.dfe.sum_bw", "Summing-node BW [GHz]", "opt_float", scale=1e9),
        _f("rx.dfe.loop_delay_ui", "Loop delay [UI]", "float"),
        _f("rx.dfe.tap1_mode", "Tap-1 mode", "enum",
           options=["direct", "unrolled"]),
        _f("rx.dfe.comparator_offset_sigma", "Comparator offset sigma [V]",
           "float"),
        _f("rx.dfe.init", "Init", "enum", options=["cursor", "zero"]),
    ]),
    ("mlsd", "MLSD", [
        _f("rx.mlsd.kind", "Detector", "enum",
           options=["none", "sliding", "viterbi"]),
        _f("rx.mlsd.memory", "Trellis memory (postcursors)", "int"),
        _f("rx.mlsd.seq_len", "Sliding window", "int"),
        _f("rx.mlsd.margin", "Sliding margin", "float"),
    ]),
    ("cdr", "CDR", [
        _f("rx.cdr.kind", "Kind", "enum",
           options=["bang_bang", "mueller_muller"]),
        _f("rx.cdr.kp_shift", "Kp shift (2^-k)", "int"),
        _f("rx.cdr.ki_shift", "Ki shift (2^-k)", "int"),
        _f("rx.cdr.pd_offset", "PD offset", "float"),
        _f("rx.cdr.pd_input", "PD input", "enum", options=["adc", "ffe"]),
        _f("rx.cdr.clamp", "Phase clamp [UI]", "opt_float"),
        _f("rx.cdr.loop_latency_symbols", "Loop latency [sym]", "int"),
    ]),
    ("sim", "Simulation", [
        _f("sim.n_symbols", "Symbols", "int"),
        _f("sim.seed", "Seed", "int"),
        _f("sim.engine", "Engine", "enum", options=["time", "stat", "both"]),
        _f("sim.pattern", "Pattern", "enum", options=PATTERNS),
        _f("sim.chunk_symbols", "Chunk symbols", "int"),
        _f("sim.cdr_settle", "CDR settle [sym]", "int"),
        _f("sim.train_symbols", "Train [sym]", "int"),
        _f("sim.warmup_discard", "Warmup discard [sym]", "opt_int"),
    ]),
    ("numeric", "Numeric", [
        _f("numeric.mode", "Datapath", "enum", options=["float", "fixed"]),
    ]),
]

# flat lookup path -> field spec
FIELD_BY_PATH: dict[str, dict] = {
    fld["path"]: fld for _, _, flds in SECTIONS for fld in flds
}
ALL_PATHS: list[str] = list(FIELD_BY_PATH)


# --- coercion --------------------------------------------------------------

def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def coerce_in(field: dict, value: Any) -> Any:
    """Form value -> Python value for ``LinkConfig`` (applies scale)."""
    kind = field["kind"]
    scale = field.get("scale", 1.0)
    if kind == "bool":
        return bool(value)
    if kind == "enum":
        return str(value)
    if kind in ("str", "opt_str"):
        if _empty(value):
            return None if kind == "opt_str" else ""
        return str(value)
    if kind == "float":
        return float(value) * scale
    if kind == "int":
        return int(round(float(value)))
    if kind == "opt_float":
        return None if _empty(value) else float(value) * scale
    if kind == "opt_int":
        return None if _empty(value) else int(round(float(value)))
    if kind in ("tuple_float", "opt_tuple_float"):
        if _empty(value):
            return None if kind == "opt_tuple_float" else (1.0,)
        if isinstance(value, (list, tuple)):
            parts = value
        else:
            parts = [p for p in str(value).replace(";", ",").split(",")
                     if p.strip() != ""]
        return tuple(float(p) * scale for p in parts)
    raise ValueError(f"unknown field kind {kind!r}")


def coerce_out(field: dict, value: Any) -> Any:
    """Python value from ``LinkConfig`` -> form value (undoes scale)."""
    kind = field["kind"]
    scale = field.get("scale", 1.0)
    if value is None:
        return None
    if kind in ("float", "opt_float"):
        return value / scale
    if kind in ("tuple_float", "opt_tuple_float"):
        return ", ".join(_fmt_num(v / scale) for v in value)
    if kind in ("int", "opt_int"):
        return int(value)
    return value


def _fmt_num(x: float) -> str:
    if x == int(x):
        return str(int(x))
    return f"{x:g}"


# --- config <-> values -----------------------------------------------------

def get_by_path(cfg: LinkConfig, path: str) -> Any:
    obj: Any = cfg
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def build_config(values: dict[str, Any]) -> LinkConfig:
    """Flat ``{path: form_value}`` -> ``LinkConfig`` (coerced)."""
    overrides: dict[str, Any] = {}
    for path, field in FIELD_BY_PATH.items():
        if path in values:
            overrides[path] = coerce_in(field, values[path])
    cfg = apply_overrides(LinkConfig(), overrides)
    return _resolve_channel(cfg)


def _resolve_channel(cfg: LinkConfig) -> LinkConfig:
    """Rewrite a touchstone channel file to an absolute, existing path."""
    if cfg.channel.kind == "touchstone" and cfg.channel.file:
        resolved = resolve_data_file(cfg.channel.file)
        if resolved != cfg.channel.file:
            cfg = apply_overrides(cfg, {"channel.file": resolved})
    return cfg


def config_to_values(cfg: LinkConfig) -> dict[str, Any]:
    """``LinkConfig`` -> flat ``{path: form_value}`` for the form."""
    return {path: coerce_out(field, get_by_path(cfg, path))
            for path, field in FIELD_BY_PATH.items()}


# --- YAML ------------------------------------------------------------------

def config_to_yaml(cfg: LinkConfig) -> str:
    data = _tuples_to_lists(dataclasses.asdict(cfg))
    data["schema_version"] = SCHEMA_VERSION
    import yaml  # local: the Android build imports this module without PyYAML

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def yaml_to_config(text: str) -> LinkConfig:
    import yaml  # local: see config_to_yaml

    data = yaml.safe_load(io.StringIO(text)) or {}
    version = data.pop("schema_version", SCHEMA_VERSION)
    if version > SCHEMA_VERSION:
        raise ValueError(f"schema_version {version} newer than {SCHEMA_VERSION}")
    from halo_serdes.config.loader import _build  # reuse strict builder

    return _build(LinkConfig, data, "link")


def _tuples_to_lists(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _tuples_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [_tuples_to_lists(v) for v in obj]
    return obj


# --- presets ---------------------------------------------------------------

def _preset_paths() -> dict[str, Path]:
    return {
        "NRZ 16G mixed-signal": CONFIGS_DIR / "nrz_16g_ms.yaml",
        "NRZ 32G static": CONFIGS_DIR / "nrz_32g.yaml",
        "NRZ 28G analytic (COM/xtalk)": CONFIGS_DIR / "nrz_28g_analytic.yaml",
        "PAM4 32G mixed-signal": CONFIGS_DIR / "pam4_32g_ms.yaml",
        "PAM4 224G ADC (106 GBd)": CONFIGS_DIR / "pam4_224g_adc.yaml",
        "PAM4 224G ADC (112 GBd stress)": CONFIGS_DIR / "pam4_224g_112g_adc.yaml",
        "PAM4 deep-LR ADC (FFE+DFE8)": CONFIGS_DIR / "pam4_deep_lr_adc.yaml",
        # 112 GBd PAM4 = 224 Gb/s; "224G" is the data rate everywhere else
        "PAM4 224G ADC (TI mismatch)": CONFIGS_DIR / "pam4_112g_adc_mismatch.yaml",
    }


def preset_names() -> list[str]:
    names = ["Library defaults"]
    names += [n for n, p in _preset_paths().items() if p.exists()]
    return names


def load_preset(name: str) -> LinkConfig:
    """Load a named preset. Raises rather than falling back to defaults.

    The silent ``LinkConfig()`` fallback this used to have was actively
    harmful: ``LinkConfig()`` defaults to ``channel.kind='touchstone'`` with no
    file, so a missing preset file surfaced far downstream as "channel.file is
    unset" from the engine — which is exactly how a packaging bug (``configs/``
    not shipped in an APK) got mistaken for a config bug. Fail where the cause
    is.
    """
    if name == "Library defaults":
        return LinkConfig()
    path = _preset_paths().get(name)
    if path is None:
        raise KeyError(
            f"unknown preset {name!r}; available: {preset_names()}")
    if not path.exists():
        raise FileNotFoundError(
            f"preset {name!r} maps to {path}, which does not exist — the "
            f"configs/ directory was not found (looked under {CONFIGS_DIR}). "
            f"Set ${DATA_DIR_ENV} to the directory containing configs/.")
    return _resolve_channel(load_config(path))


# --- derived read-only quantities ------------------------------------------

def derived(cfg: LinkConfig) -> dict[str, str]:
    return {
        "UI": f"{cfg.ui * 1e12:.3f} ps",
        "dt": f"{cfg.dt * 1e12:.4f} ps",
        "Nyquist": f"{cfg.f_nyquist / 1e9:.3f} GHz",
        "Data rate": f"{cfg.data_rate / 1e9:.2f} Gb/s",
        "bits/sym": str(cfg.bits_per_symbol),
    }


def envelope_status(cfg: LinkConfig) -> tuple[str, str]:
    """(level, message) for the mixed-signal envelope banner. level in
    {'ok','warn','crit'}; empty message when not mixed-signal or comfortable."""
    if cfg.rx.arch != "mixed_signal":
        return "ok", ""
    from halo_serdes.config.schema import (
        MS_COMFORT_DATA_RATE,
        MS_HARD_MAX_BAUD,
        MS_LIMIT_DATA_RATE,
    )
    mod = cfg.modulation
    comfort = MS_COMFORT_DATA_RATE.get(mod, 16e9)
    limit = MS_LIMIT_DATA_RATE.get(mod, 16e9)
    dr = cfg.data_rate / 1e9
    if cfg.data_rate > limit * (1 + 1e-9) or cfg.symbol_rate > MS_HARD_MAX_BAUD * (1 + 1e-9):
        return "crit", (f"{dr:.3g} Gb/s {mod.upper()} exceeds the mixed-signal "
                        f"envelope (limit {limit/1e9:.0f} Gb/s, hard ceiling "
                        f"{MS_HARD_MAX_BAUD/1e9:.0f} GBd) — use adc_dsp.")
    if cfg.data_rate > comfort * (1 + 1e-9):
        return "warn", (f"{dr:.3g} Gb/s {mod.upper()} is in the marginal zone "
                        f"(comfort {comfort/1e9:.0f} Gb/s) — FEC-dependent.")
    return "ok", ""
