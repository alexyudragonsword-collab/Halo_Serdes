"""Live bridge to pll_simulator: a clock profile straight from a PLL model, no file.

The file (``docs/clock_profile.md``) stays the contract between the two
libraries, and this module produces exactly what the file would carry. It
exists for the loop a designer runs on a desktop -- change a loop filter in
pllsim, re-run the link -- where writing and reading YAML in between is
friction. Nothing in ``halo_serdes`` imports this module, and it imports
``pllsim`` only inside the one function that needs it, so the phone build
(numpy + scipy and nothing else) never sees the dependency.

Install the producer with ``pip install "halo-serdes[pll]"``; without it,
:func:`profile_from_preset` raises an ``ImportError`` that says so, while
:func:`profile_from_analysis` needs no import at all -- it reads the result
object the way the file format does.
"""

from __future__ import annotations

import math
import re

import numpy as np

from ..tx.clock import ClockProfile

#: The format's grid: 100 Hz to f0/2, log-uniform, 60 points per decade --
#: the same the producer's own exporter writes.
F_LO_HZ = 100.0
POINTS_PER_DECADE = 60

_SPUR_AT = re.compile(r"^.+_spur@(?P<f>[0-9.eE+-]+)Hz$")

_INSTALL_HINT = ("profile_from_preset needs pll_simulator (package 'pllsim'), which is not a "
                 "dependency of halo_serdes: pip install \"halo-serdes[pll]\" or "
                 "pip install git+https://github.com/alexyudragonsword-collab/pll_simulator "
                 "-- or export a profile file from pllsim and use tx.clock.file instead")


def profile_grid(f0_hz: float) -> np.ndarray:
    """The offset grid a profile is analysed on: 100 Hz .. f0/2, log-uniform."""
    f_hi = 0.5 * float(f0_hz)
    if not f_hi > F_LO_HZ:
        raise ValueError(f"f0/2 = {f_hi:g} Hz is not above {F_LO_HZ:g} Hz")
    n = int(round(POINTS_PER_DECADE * math.log10(f_hi / F_LO_HZ))) + 1
    return np.geomspace(F_LO_HZ, f_hi, n)


def profile_from_analysis(ar, *, source: str = "", fref_hz: float | None = None) -> ClockProfile:
    """A :class:`ClockProfile` from a pllsim ``AnalysisResult`` (duck-typed).

    Reads ``ar.f`` [Hz], ``ar.f0`` [Hz], ``ar.pn_breakdown["total"]``
    (S_phi, rad^2/Hz, one-sided) and ``ar.spurs_analytic`` (name -> dBc),
    i.e. what the producer's own exporter reads. L(f) = S_phi/2 in dBc/Hz.

    The grid must reach f0/2: the consumer treats the profile as silent above
    its last point, so a grid that stops short would silently drop clock
    noise. Analyse on :func:`profile_grid` (``pll.analyze(f=profile_grid(f0))``)
    and the profile is the model's curve point for point. A grid starting
    above 100 Hz is accepted (the consumer holds the first value flat below
    it, and a link run resolves nothing down there anyway).

    Spur keys carry their offset (``frac_spur@9696000Hz``); a bare
    ``ref_spur`` sits at the reference frequency, which the result does not
    carry -- pass ``fref_hz``.
    """
    f = np.asarray(ar.f, dtype=np.float64)
    f0 = float(ar.f0)
    s_phi = np.asarray(ar.pn_breakdown["total"], dtype=np.float64)
    if f.ndim != 1 or f.size != s_phi.size:
        raise ValueError("AnalysisResult: f and pn_breakdown['total'] must be 1-D and the same length")
    f_hi = 0.5 * f0
    if f[-1] < f_hi * (1.0 - 1e-9):
        raise ValueError(
            f"the analysis grid ends at {f[-1]:.4g} Hz but the profile needs it to reach "
            f"f0/2 = {f_hi:.4g} Hz; the bridge does not extrapolate -- analyse on it: "
            "pll.analyze(f=halo_serdes.io.pll_bridge.profile_grid(f0))")
    keep = (f > 0) & (f <= f_hi * (1.0 + 1e-9))
    f, s_phi = f[keep], s_phi[keep]
    if np.any(s_phi <= 0) or not np.all(np.isfinite(s_phi)):
        raise ValueError("AnalysisResult: S_phi must be finite and > 0 on the kept grid")
    l_dbc = 10.0 * np.log10(s_phi / 2.0)

    spurs: list[tuple[float, float]] = []
    for key, level in (getattr(ar, "spurs_analytic", None) or {}).items():
        if "_spur" not in key:
            continue
        dbc = float(level)
        if not math.isfinite(dbc):
            continue
        m = _SPUR_AT.match(key)
        if m:
            fs = float(m.group("f"))
        elif key == "ref_spur":
            if fref_hz is None:
                raise ValueError("spurs_analytic has a ref_spur at the reference frequency, "
                                 "which the result does not carry: pass fref_hz=pll.cfg.fref")
            fs = float(fref_hz)
        else:
            raise ValueError(f"spur {key!r}: no offset in the key and no rule for it")
        spurs.append((fs, dbc))
    return ClockProfile(f0_hz=f0, f_hz=f, l_dbc_hz=l_dbc, spurs=tuple(sorted(spurs)),
                        source=str(source))


def profile_from_preset(name: str, *, source: str | None = None) -> ClockProfile:
    """Build the pllsim preset ``name``, analyse it on the profile grid, return the profile.

    This is the one place ``pllsim`` is imported, and only when called.
    """
    try:
        import pllsim  # noqa: F401
        from pllsim import presets
    except ImportError as exc:  # pragma: no cover - exercised with the import blocked
        raise ImportError(_INSTALL_HINT) from exc
    table = getattr(presets, "ALL_PRESETS", None) or {}
    factory = table.get(name) if isinstance(table, dict) else None
    if factory is None:
        factory = getattr(presets, name, None)
    if factory is None:
        known = sorted(table) if isinstance(table, dict) else []
        raise KeyError(f"unknown pllsim preset {name!r}; known: {known}")
    pll = factory()
    ar = pll.analyze(f=profile_grid(float(ar_f0(pll))))
    fref = getattr(getattr(pll, "cfg", None), "fref", None)
    if source is None:
        version = getattr(pllsim, "__version__", "?")
        source = f"pllsim preset {name}, linear model (analyze), pllsim {version}, live bridge"
    return profile_from_analysis(ar, source=source, fref_hz=fref)


def ar_f0(pll) -> float:
    """The output carrier of a pllsim PLL object, from its config."""
    cfg = getattr(pll, "cfg", None)
    for attr in ("fout", "f0", "fvco"):
        v = getattr(cfg, attr, None)
        if v:
            return float(v)
    raise AttributeError("cannot find the output carrier (cfg.fout) on this pllsim object")
