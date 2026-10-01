"""The transmit clock as a PLL makes it: a phase-noise profile turned into edges.

Why a file, and why here
------------------------
``tx/jitter.py`` models clock jitter the way a jitter spec sheet states it:
independent Gaussian edge noise, one sinusoidal tone, duty-cycle distortion.
A PLL does not make that clock. It makes 1/f^3 and 1/f^2 close to the carrier,
a flat in-band floor where the reference and the detector dominate, and spurs
at the reference and fractional offsets. A CDR is a high-pass to clock jitter,
so two clocks with the same 200 fs RMS leave very different residues behind it
-- the question "does this PLL, with this CDR bandwidth, close the link?"
cannot be asked of one ``rj_ui`` number.

The answer arrives as a *file*: the clock phase-noise profile a PLL simulator
writes (``docs/clock_profile.md`` defines it; pll_simulator's
``export/clock_profile.py`` obeys it). It is the only thing shared between the
two libraries. A profile is data a frozen ``LinkConfig`` can name (invariant
#1); a PLL object is not, and importing the producer would drag matplotlib
into a phone build that carries numpy and scipy alone. This module is the one
place in Halo_Serdes that knows what the file looks like; downstream of
:meth:`ClockProfile.edge_offsets_s` the engines see the same per-edge offset
array in seconds they always saw.

The unit convention that is easiest to lose
--------------------------------------------
The profile's L(f) is phase noise against the PLL output carrier ``f0_hz``,
and timing is **phase / (2 pi f0_hz)**. A half-rate clock at f_baud/2 with the
same dBc/Hz carries twice the seconds. The file states f0 for exactly this
reason, and nothing here derives it from the baud rate. ``l_dbc_hz`` is
single-sideband L(f) = S_phi/2; the synthesiser wants S_phi, the (one-sided)
PSD of the phase in rad^2/Hz, whose integral is the phase variance -- the 3 dB
between them is a sqrt(2) on every jitter number if it is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..vendor.pllsim.colored import synth_from_psd
from ..vendor.pllsim.jitter import integrate_pn, sphi_from_ldbc

TWOPI = 2.0 * np.pi

_REQUIRED = ("f0_hz", "f_hz", "l_dbc_hz")
_OPTIONAL = ("spurs", "source")


@dataclass(frozen=True)
class ClockProfile:
    """A clock's phase-noise profile, as read from a profile file.

    ``f_hz`` is the offset grid (ascending, > 0), ``l_dbc_hz`` the total
    single-sideband L(f) on it, ``spurs`` the ``(offset_hz, dbc)`` table and
    ``source`` the producer's free-text provenance line.
    """

    f0_hz: float
    f_hz: np.ndarray
    l_dbc_hz: np.ndarray
    spurs: tuple[tuple[float, float], ...] = ()
    source: str = ""

    def __post_init__(self):
        f = np.asarray(self.f_hz, dtype=np.float64)
        l_dbc = np.asarray(self.l_dbc_hz, dtype=np.float64)
        if not (np.isfinite(self.f0_hz) and self.f0_hz > 0):
            raise ValueError(f"clock profile: f0_hz must be > 0, got {self.f0_hz!r}")
        if f.ndim != 1 or f.size < 2:
            raise ValueError("clock profile: f_hz needs at least two points")
        if l_dbc.shape != f.shape:
            raise ValueError(f"clock profile: l_dbc_hz has {l_dbc.size} points, "
                             f"f_hz has {f.size}")
        if np.any(f <= 0) or np.any(np.diff(f) <= 0):
            raise ValueError("clock profile: f_hz must be positive and strictly ascending")
        if not np.all(np.isfinite(l_dbc)):
            raise ValueError("clock profile: l_dbc_hz must be finite")
        for fs, dbc in self.spurs:
            if not (fs > 0 and np.isfinite(dbc)):
                raise ValueError(f"clock profile: bad spur ({fs!r}, {dbc!r})")
        object.__setattr__(self, "f_hz", f)
        object.__setattr__(self, "l_dbc_hz", l_dbc)
        object.__setattr__(self, "spurs", tuple((float(a), float(b)) for a, b in self.spurs))

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, path: str | Path, *, f0_hz: float | None = None) -> "ClockProfile":
        """Read a profile file.

        ``path`` is opened as given. Repo-relative paths (``data/clock_profiles/
        x.yaml``) are resolved to absolute ones by the application layer before
        the config reaches an engine -- the same arrangement ``channel.file``
        has -- so this module stays free of any knowledge of where data lives.

        ``f0_hz`` overrides the carrier the file states; it changes only the
        phase-to-seconds scale, not the curve.
        """
        # Local import, like config/loader.py: the phone compute path must
        # import without PyYAML, and it does -- this function only runs for
        # kind="profile", never at import time.
        import yaml

        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(
                f"clock profile not found: {path} (a repo-relative path is "
                f"resolved by halo_serdes_app.config_bridge.resolve_data_file; "
                f"a library caller passes an absolute path)")
        with p.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: a clock profile is a mapping, got {type(data).__name__}")
        unknown = sorted(set(data) - set(_REQUIRED) - set(_OPTIONAL))
        if unknown:
            raise ValueError(f"{path}: unknown clock-profile key(s) {unknown}; "
                             f"the format is docs/clock_profile.md")
        missing = [k for k in _REQUIRED if k not in data]
        if missing:
            raise ValueError(f"{path}: clock profile is missing {missing}")
        spurs = []
        for row in data.get("spurs") or []:
            if not (isinstance(row, dict) and {"f_hz", "dbc"} <= set(row)):
                raise ValueError(f"{path}: each spur is {{f_hz, dbc}}, got {row!r}")
            spurs.append((float(row["f_hz"]), float(row["dbc"])))
        return cls(
            f0_hz=float(f0_hz if f0_hz is not None else data["f0_hz"]),
            f_hz=np.asarray(data["f_hz"], dtype=np.float64),
            l_dbc_hz=np.asarray(data["l_dbc_hz"], dtype=np.float64),
            spurs=tuple(spurs),
            source=str(data.get("source") or ""),
        )

    # ------------------------------------------------------------ spectrum
    def s_phi(self, f: np.ndarray) -> np.ndarray:
        """S_phi(f) [rad^2/Hz] on an arbitrary grid: the PSD the synthesiser wants.

        Interpolated linearly in (log f, dB), which is how the producer
        resamples too, so the two agree on the curve between grid points.
        Outside the grid the profile says nothing: *below* the first point
        the first value is held (a link run of N UI cannot resolve offsets
        under 1/(N UI) anyway, and in practice that floor sits far above
        100 Hz); *above* f0/2 the PSD is zero -- phase noise of a clock at f0
        is defined up to its own Nyquist and no further, and holding the
        floor flat out to f_baud/2 would invent noise the profile never
        stated. This is what makes the synthesised edge variance equal the
        profile's integral over [1/(N UI), f0/2], which the tests check.
        """
        f = np.asarray(f, dtype=np.float64)
        l_dbc = np.interp(np.log10(np.maximum(f, 1e-300)),
                          np.log10(self.f_hz), self.l_dbc_hz)
        s = sphi_from_ldbc(l_dbc)
        return np.where(f > self.f_hz[-1], 0.0, s)

    def rms_jitter_s(self, f1: float, f2: float) -> float:
        """RMS jitter [s] from the continuous part over [f1, f2] (spurs excluded)."""
        return float(np.sqrt(integrate_pn(self.f_hz, sphi_from_ldbc(self.l_dbc_hz), f1, f2))
                     / (TWOPI * self.f0_hz))

    def spur_amplitudes_s(self) -> list[tuple[float, float]]:
        """``(offset_hz, peak_amplitude_s)`` per spur.

        ``dbc`` is the single-sideband spur level the producer measures:
        for a sinusoidal phase modulation of peak ``A`` rad, each sideband
        sits ``20 log10(A/2)`` dB below the carrier (small-angle Bessel,
        J1(A)/J0(A) ~ A/2), and that is exactly how pll_simulator defines
        it (``core/dtcspurs.py``: ``20*log10(dphi*mag/2)``). Hence
        ``A = 2 * 10**(dbc/20)``; the sinusoid's RMS phase is A/sqrt(2) =
        sqrt(2 * 10**(dbc/10)), which is the double-sideband power figure the
        same number reads as in L(f) terms. Divided by 2 pi f0 it is seconds.
        A test synthesises one spur and reads its sideband back off the
        phase spectrum, so the convention is pinned, not asserted.
        """
        return [(fs, 2.0 * 10.0 ** (dbc / 20.0) / (TWOPI * self.f0_hz))
                for fs, dbc in self.spurs]

    # ---------------------------------------------------------------- edges
    def edge_offsets_s(self, n_edges: int, ui: float,
                       rng: np.random.Generator) -> np.ndarray:
        """Per-edge timing offsets [s] for ``n_edges`` consecutive symbol boundaries.

        The phase is synthesised at the edge rate 1/UI: ``synth_from_psd``
        shapes white Gaussian noise in the FFT domain to ``s_phi``, so the
        sequence's one-sided PSD is the profile's (DC bin zeroed -- nothing
        below 1/(n_edges UI) is representable). Each spur adds one sinusoid
        at its offset, with a uniformly random starting phase so that a
        10 MHz spur does not land on the same edge in every run. Then
        phase / (2 pi f0) is seconds.

        Random numbers are drawn only here and only for a profile clock; the
        white-kind terms added afterwards by ``tx/jitter.py`` draw theirs in
        the order they always did, which is what keeps ``kind="white"``
        results byte-identical to before this module existed.
        """
        if n_edges < 2:
            return np.zeros(n_edges)
        fs = 1.0 / ui
        phi = synth_from_psd(self.s_phi, fs, n_edges, rng)
        if self.spurs:
            k = np.arange(n_edges, dtype=np.float64)
            for (f_spur, _dbc), (_f, amp_s) in zip(self.spurs, self.spur_amplitudes_s()):
                theta = rng.uniform(0.0, TWOPI)
                phi += amp_s * TWOPI * self.f0_hz * np.sin(TWOPI * f_spur * k * ui + theta)
        return phi / (TWOPI * self.f0_hz)
