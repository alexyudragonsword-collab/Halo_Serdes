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

# Fields that moved one level down. ``tx.rj_ui`` and its three siblings became
# ``tx.clock.*`` when the Tx clock grew a second kind (a PLL phase-noise
# profile); every YAML written before that still carries them flat under
# ``tx``, and those files must keep loading to the same LinkConfig they
# always did -- which the invariant-#1 promise ("the config is the single
# source of truth") makes a correctness requirement, not a convenience.
# {(owner dataclass name, old flat key): new sub-key}
_NESTED_MIGRATIONS: dict[tuple[str, str], str] = {
    ("TxConfig", k): "clock" for k in ("rj_ui", "sj_ui", "sj_freq", "dcd_ui")
}


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
    data = _apply_nested_migrations(cls, data)
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


def _apply_nested_migrations(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Move flat keys that now live one level down, without clobbering a
    value the file already states at the new location (the new one wins:
    it is the one the author wrote knowing the current schema)."""
    moves = {k: sub for (owner, k), sub in _NESTED_MIGRATIONS.items()
             if owner == cls.__name__ and k in data}
    if not moves:
        return data
    out = dict(data)
    for key, sub in moves.items():
        value = out.pop(key)
        child = out.get(sub)
        if child is None:
            child = {}
        elif not isinstance(child, dict):
            raise TypeError(f"{cls.__name__}.{sub}: expected mapping to migrate "
                            f"{key!r} into, got {type(child).__name__}")
        else:
            child = dict(child)
        child.setdefault(key, value)
        out[sub] = child
    return out


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
    """Return a copy of ``cfg`` with dotted-path overrides applied.

    All overrides land in one ``dataclasses.replace`` per dataclass, not one
    at a time. The difference is visible only when two fields are validated
    together: ``tx.clock.kind="profile"`` is illegal without a
    ``tx.clock.file``, so applying the pair sequentially raised on the first
    key whenever it happened to come first -- which it does in the form's
    field order, so the GUI and the Android app could not build a profile
    clock at all. Grouping makes the result independent of dict order.
    """
    tree: dict[str, Any] = {}
    for dotted, value in overrides.items():
        parts = dotted.split(".")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise KeyError(f"override {dotted!r} descends into a field that "
                               f"another override already sets whole")
        node[parts[-1]] = value
    return _replace_tree(cfg, tree)


def _replace_tree(obj: Any, tree: dict[str, Any]) -> Any:
    names = {f.name for f in dataclasses.fields(obj)}
    kwargs: dict[str, Any] = {}
    for key, value in tree.items():
        if key not in names:
            raise KeyError(f"unknown config field {key!r} on {type(obj).__name__}")
        child = getattr(obj, key)
        if isinstance(value, dict) and child is None:
            # an optional subtree that is unset (``topology``): overrides
            # descend into a default instance of its type
            tp = _unwrap_optional(typing.get_type_hints(type(obj))[key])
            if dataclasses.is_dataclass(tp):
                child = tp()
        if isinstance(value, dict) and dataclasses.is_dataclass(child):
            kwargs[key] = _replace_tree(child, value)
        else:
            kwargs[key] = value
    return dataclasses.replace(obj, **kwargs)


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
