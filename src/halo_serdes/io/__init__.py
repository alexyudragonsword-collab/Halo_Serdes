"""External-ecosystem adapters: IBIS-AMI models and COM.

The framework's signal chain is self-authored and backend-free by default.
These adapters are the *interface seam* to the industry ecosystem:

* ``ami`` — a uniform :class:`AmiModel` interface (Init flow / GetWave flow)
  that a real IBIS-AMI shared library plugs into through ``pyibisami``, with a
  native FIR reference model so the seam is exercised without any external
  dependency.
* ``ami.ComAdapter`` — the seam for Channel Operating Margin. A simplified
  behavioral COM ships natively; the official IEEE 802.3 COM tool plugs in
  through the same interface.
"""

from .ami import (
    AmiModel,
    Com93a,
    ComAdapter,
    ComResult,
    IbisAmiModel,
    NativeCom,
    NativeFirAmi,
    load_ami_model,
)

__all__ = [
    "AmiModel", "NativeFirAmi", "IbisAmiModel", "load_ami_model",
    "ComAdapter", "NativeCom", "Com93a", "ComResult",
]
