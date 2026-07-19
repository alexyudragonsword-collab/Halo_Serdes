"""Halo_Serdes GUI — a Plotly Dash studio over the behavioral SerDes engines.

The GUI adds no simulation logic; it edits ``halo_serdes.config.LinkConfig``,
calls the existing engines, and renders ``SimResult``/``StatResult`` with
Plotly. Launch via the ``halo-serdes-gui`` console script or
``python -m halo_serdes_gui``.
"""

from .app import build_app, main

__all__ = ["build_app", "main"]
