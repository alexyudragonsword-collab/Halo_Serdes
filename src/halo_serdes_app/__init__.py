"""Halo_Serdes application layer — presentation-agnostic orchestration.

Sits between the physics core (``halo_serdes``) and any view layer:

    halo_serdes/          physics + engines (no UI metadata)
            |
    halo_serdes_app/      this package — config<->form codec, studies, run registry
            |                    |
    halo_serdes_gui/      android/  (Dash)            (Compose + Chaquopy)

It adds **no simulation logic** — it edits ``LinkConfig``, calls the existing
engines, and hands back plain Python/numpy data. Nothing here imports a UI
toolkit, which is what lets the Android build reuse it: the modules were
previously inside ``halo_serdes_gui``, whose package ``__init__`` pulls in Dash
and therefore made them unimportable on a phone.

``config_bridge`` carries display metadata (field labels, unit scaling) on
purpose — that belongs to an application layer, not to the physics core.
"""

__all__ = ["config_bridge", "runner", "studies"]
