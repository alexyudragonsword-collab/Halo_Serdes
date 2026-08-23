from .crosstalk import (
    XtalkAggressor,
    aggressor_bank,
    icn_rms,
    import_xtalk,
    inject_crosstalk,
    synthetic_aggressor,
)
from .model import ChannelModel
from .response import freq2impulse, impulse2freq, pulse_from_impulse, trim_impulse, zero_pad_to_dt

# The touchstone helpers are resolved lazily (PEP 562) because importing them
# pulls in scikit-rf, which in turn pulls scipy. An analytic-channel run needs
# neither, and the Android build ships only what it must — see
# tests/test_import_hygiene.py. `from halo_serdes.channel import se2mm` and
# `import *` both still work, the latter because __all__ is explicit below.
_LAZY_TOUCHSTONE = frozenset({
    "import_diff_network", "interp_s2p", "sdd_2port", "se2mm",
    "terminate_gamma", "terminate_renormalize",
})


def __getattr__(name):
    if name in _LAZY_TOUCHSTONE:
        from . import touchstone

        return getattr(touchstone, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ChannelModel",
    "freq2impulse", "impulse2freq", "zero_pad_to_dt", "trim_impulse", "pulse_from_impulse",
    "import_diff_network", "interp_s2p", "se2mm", "sdd_2port",
    "terminate_gamma", "terminate_renormalize",
    "XtalkAggressor", "synthetic_aggressor", "inject_crosstalk", "import_xtalk",
    "aggressor_bank", "icn_rms",
]
