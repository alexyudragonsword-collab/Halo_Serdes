"""Backwards-compatible re-export — this module now lives in ``halo_serdes_app``.

See ``halo_serdes_gui/config_bridge.py`` for why it moved. Import from
``halo_serdes_app.studies`` in new code.
"""

from halo_serdes_app.studies import (  # noqa: F401
    com_study,
    crosstalk_study,
    fec_projection,
    fixedpoint_study,
    jtol_study,
    multilane_study,
    reach_study,
)

__all__ = [
    "com_study", "crosstalk_study", "fec_projection", "fixedpoint_study",
    "jtol_study", "multilane_study", "reach_study",
]
