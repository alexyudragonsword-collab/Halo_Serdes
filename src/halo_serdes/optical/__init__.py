"""Optical interconnect blocks: E/O, fibre, O/E and the noise the O/E adds.

Pure numpy, so the phone compute path can import it. Each ``response`` is a
dimensionless small-signal transfer function on a frequency grid with unity
DC gain; the channel layer cascades them with the electrical segments
(``channel/model.py``). ``OpticalNoise`` is the one object both engines take.
"""

from . import eo, fiber, noise, oe
from .eo import StaticCurve, optical_rlm, rlm, static_curve
from .noise import OpticalNoise

__all__ = ["eo", "fiber", "noise", "oe", "OpticalNoise", "StaticCurve", "optical_rlm",
           "rlm", "static_curve"]
