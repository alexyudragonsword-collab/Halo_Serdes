"""Backwards-compatible re-export — this module now lives in ``halo_serdes_app``.

See ``halo_serdes_gui/config_bridge.py`` for why it moved. Import from
``halo_serdes_app.runner`` in new code.
"""

from halo_serdes_app.runner import (  # noqa: F401
    RunRecord,
    engines_for,
    get,
    run_from_values,
    run_link,
)

__all__ = ["RunRecord", "engines_for", "get", "run_from_values", "run_link"]
