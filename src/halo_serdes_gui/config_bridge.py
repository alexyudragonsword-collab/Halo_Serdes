"""Backwards-compatible re-export — this module now lives in ``halo_serdes_app``.

It moved so the Android build can import it without dragging in Dash (this
package's ``__init__`` imports the Dash app). Import from
``halo_serdes_app.config_bridge`` in new code; this shim keeps the existing GUI
panels and tests working unchanged.
"""

from halo_serdes_app.config_bridge import (  # noqa: F401
    ALL_PATHS,
    CONFIGS_DIR,
    FIELD_BY_PATH,
    PATTERNS,
    SECTIONS,
    build_config,
    coerce_in,
    coerce_out,
    config_to_values,
    config_to_yaml,
    derived,
    envelope_status,
    get_by_path,
    load_preset,
    preset_names,
    resolve_data_file,
    yaml_to_config,
)

__all__ = [
    "ALL_PATHS", "CONFIGS_DIR", "FIELD_BY_PATH", "PATTERNS", "SECTIONS",
    "build_config", "coerce_in", "coerce_out", "config_to_values",
    "config_to_yaml", "derived", "envelope_status", "get_by_path",
    "load_preset", "preset_names", "resolve_data_file", "yaml_to_config",
]
