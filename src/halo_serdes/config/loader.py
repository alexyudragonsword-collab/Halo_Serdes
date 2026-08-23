"""YAML <-> LinkConfig loading with strict key checking.

The YAML mirrors the nested dataclass structure of ``schema.LinkConfig``.
Unknown keys are an error (catches typos early); missing keys fall back to
dataclass defaults. A ``schema_version`` field enables future migrations
(PyBERT ``configuration.py`` lesson: build migration in from day one).
"""

from __future__ import annotations

import dataclasses
import typing
from pathlib import Path
from typing import Any, Union


from . import schema
from .schema import SCHEMA_VERSION, LinkConfig

# Field-name migrations from older schema versions: {old_name: new_name}.
_MIGRATIONS: dict[str, str] = {}


def _unwrap_optional(tp: Any) -> Any:
    origin = typing.get_origin(tp)
    if origin is Union:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def _build(cls: type, data: dict[str, Any], path: str) -> Any:
    """Recursively construct dataclass ``cls`` from a plain dict."""
    if not isinstance(data, dict):
        raise TypeError(f"{path}: expected mapping, got {type(data).__name__}")
    hints = typing.get_type_hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        key = _MIGRATIONS.get(key, key)
        if key not in fields:
            raise KeyError(f"{path}: unknown config key {key!r} (valid: {sorted(fields)})")
        tp = _unwrap_optional(hints[key])
        if dataclasses.is_dataclass(tp) and isinstance(value, dict):
            kwargs[key] = _build(tp, value, f"{path}.{key}")
        elif typing.get_origin(tp) is tuple and isinstance(value, (list, tuple)):
            kwargs[key] = tuple(value)
        else:
            kwargs[key] = _coerce(tp, value, f"{path}.{key}")
    return cls(**kwargs)


def _coerce(tp: Any, value: Any, path: str) -> Any:
    """Coerce YAML scalars to the annotated type.

    Notably, PyYAML 1.1 parses exponent floats without a sign ("32.0e9") as
    strings; coerce them here so configs stay human-friendly.
    """
    if value is None:
        return None
    try:
        if tp is float and not isinstance(value, float):
            return float(value)
        if tp is int and not isinstance(value, int):
            as_float = float(value)
            if not as_float.is_integer():
                raise ValueError(f"{path}: expected integer, got {value!r}")
            return int(as_float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: cannot convert {value!r} to {tp.__name__}") from exc
    return value


def load_config(path: str | Path, overrides: dict[str, Any] | None = None) -> LinkConfig:
    """Load a LinkConfig from a YAML file.

    ``overrides`` is a flat dict of dotted paths, e.g. ``{"rx.arch": "adc_dsp"}``,
    applied after loading (for parameter sweeps from scripts).
    """
    import yaml  # local: keeps halo_serdes.config importable without PyYAML

    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    version = data.pop("schema_version", SCHEMA_VERSION)
    if version > SCHEMA_VERSION:
        raise ValueError(f"config schema_version {version} is newer than supported {SCHEMA_VERSION}")
    cfg = _build(LinkConfig, data, "link")
    if overrides:
        cfg = apply_overrides(cfg, overrides)
    return cfg


def apply_overrides(cfg: LinkConfig, overrides: dict[str, Any]) -> LinkConfig:
    """Return a copy of ``cfg`` with dotted-path overrides applied."""
    for dotted, value in overrides.items():
        parts = dotted.split(".")
        cfg = _replace_path(cfg, parts, value)
    return cfg


def _replace_path(obj: Any, parts: list[str], value: Any) -> Any:
    if len(parts) == 1:
        if not any(f.name == parts[0] for f in dataclasses.fields(obj)):
            raise KeyError(f"unknown config field {parts[0]!r} on {type(obj).__name__}")
        return dataclasses.replace(obj, **{parts[0]: value})
    child = getattr(obj, parts[0])
    return dataclasses.replace(obj, **{parts[0]: _replace_path(child, parts[1:], value)})


def dump_config(cfg: LinkConfig, path: str | Path) -> None:
    """Write ``cfg`` to YAML (round-trips through ``load_config``)."""
    data = dataclasses.asdict(cfg)
    data = _tuples_to_lists(data)
    data["schema_version"] = SCHEMA_VERSION
    import yaml  # local: see load_config

    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def _tuples_to_lists(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _tuples_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_tuples_to_lists(v) for v in obj]
    if isinstance(obj, list):
        return [_tuples_to_lists(v) for v in obj]
    return obj


__all__ = ["load_config", "dump_config", "apply_overrides", "schema"]
