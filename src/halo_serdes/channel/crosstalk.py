"""FEXT / NEXT crosstalk aggressors for the time and statistical engines.

An aggressor is an independent transmitter whose data couples onto the victim
line. It is fully described by a *coupling impulse response* — the transfer
from the aggressor's Tx voltage to the victim's slicer-input node (the same
node the statistical engine samples its ``xtalk_pulses`` at, so the two
engines stay consistent).

* **FEXT** (far-end): the aggressor travels the length of the coupled line
  alongside the victim; coupling emphasizes high frequency (a derivative-like
  shape) and rolls off with the through-channel loss.
* **NEXT** (near-end): the aggressor couples back at the victim's own end;
  coupling is broadband and does not see the far-end loss.

For a behavioral summing model both simply add a filtered, independent data
stream at the victim node — the physics lives entirely in the coupling
response, so ``kind`` is metadata/label. Real couplings come from a multi-port
Touchstone via :func:`import_xtalk` (PyBERT's ``import_fext`` analog);
:func:`synthetic_aggressor` builds a parametric one for tests and examples.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.mapping import nrz_levels, pam4_levels
from ..core.waveform import Waveform
from .response import pulse_from_impulse


@dataclass
class XtalkAggressor:
    """One crosstalk aggressor.

    Args:
        coupling: coupling impulse response (per-sample weight, at ``dt``) from
            the aggressor Tx voltage to the victim slicer-input node.
        dt: sample step of ``coupling`` [s].
        kind: ``'fext'`` or ``'next'`` (label/metadata).
        swing: aggressor Tx peak-to-peak swing [V] (defaults to the victim's).
        modulation: ``'nrz'`` or ``'pam4'`` for the aggressor data.
        seed: RNG seed for the aggressor's independent random data.
        label: display name.
    """

    coupling: np.ndarray
    dt: float
    kind: str = "fext"
    swing: float = 1.0
    modulation: str = "nrz"
    seed: int = 0
    label: str = ""

    def __post_init__(self):
        self.coupling = np.asarray(self.coupling, dtype=np.float64)

    def pulse(self, osr: int) -> Waveform:
        """Coupling pulse response (single aggressor symbol) — feeds the
        statistical engine's ``xtalk_pulses`` at the same node."""
        return pulse_from_impulse(Waveform(self.coupling, self.dt), osr)

    def coupling_peak_db(self, osr: int) -> float:
        """UI-sampled coupling peak relative to unit Tx swing [dB]."""
        pk = float(np.max(np.abs(self.pulse(osr).y)))
        return 20.0 * np.log10(max(pk, 1e-30))

    def time_contribution(self, n_samples: int, osr: int,
                          modulation: str | None = None) -> np.ndarray:
        """Victim-node crosstalk waveform for ``n_samples`` samples.

        Generates independent random aggressor symbols, oversamples (ZOH),
        convolves with the coupling response, and returns exactly
        ``n_samples`` samples aligned to the victim time grid.
        """
        mod = modulation if modulation is not None else self.modulation
        rng = np.random.default_rng(self.seed)
        n_sym = n_samples // osr + self.coupling.size // osr + 2
        if mod == "pam4":
            lv = pam4_levels(self.swing)
            sym = rng.integers(0, 4, size=n_sym)
        else:
            lv = nrz_levels(self.swing)
            sym = rng.integers(0, 2, size=n_sym)
        v = lv[sym]
        agg_tx = np.repeat(v, osr)
        y = np.convolve(agg_tx, self.coupling)[:n_samples]
        if y.size < n_samples:
            y = np.pad(y, (0, n_samples - y.size))
        return y


def synthetic_aggressor(kind: str, coupling_db: float, ui: float, dt: float,
                        *, modulation: str = "nrz", seed: int = 0,
                        f3db: float | None = None, swing: float = 1.0,
                        label: str = "") -> XtalkAggressor:
    """Build a parametric FEXT/NEXT aggressor with a target coupling level.

    ``coupling_db`` sets the UI-sampled coupling-pulse peak (e.g. -26 dB for a
    typical worst-case FEXT). FEXT gets a derivative (high-pass) emphasis; NEXT
    stays low-pass and broader. The coupling is scaled to hit the target peak.
    """
    osr = int(round(ui / dt))
    if f3db is None:
        f3db = 0.35 / ui
    n = 24 * osr
    t = (np.arange(n) - n // 2) * dt
    tau = 1.0 / (2.0 * np.pi * f3db)
    g = np.where(t >= 0, np.exp(-t / tau), 0.0)
    if kind == "fext":
        g = np.gradient(g, dt) * tau           # far-end derivative emphasis
    elif kind == "next":
        g = np.convolve(g, g, mode="same")     # broaden (near-end, low-pass)
    else:
        raise ValueError(f"kind must be 'fext' or 'next', got {kind!r}")
    coupling = np.asarray(g, dtype=np.float64)
    pk = float(np.max(np.abs(pulse_from_impulse(Waveform(coupling, dt), osr).y)))
    coupling = coupling * (10.0 ** (coupling_db / 20.0) / max(pk, 1e-30))
    return XtalkAggressor(coupling=coupling, dt=dt, kind=kind, swing=swing,
                          modulation=modulation, seed=seed,
                          label=label or f"{kind.upper()} {coupling_db:.0f}dB")


def aggressor_bank(n_fext: int, n_next: int, coupling_db: float, ui: float,
                   dt: float, *, modulation: str = "nrz", swing: float = 1.0,
                   spread_db: float = 2.0, base_seed: int = 0,
                   f3db: float | None = None) -> list[XtalkAggressor]:
    """A multi-lane crosstalk environment: ``n_fext`` FEXT + ``n_next`` NEXT
    aggressors, each an independent lane.

    Each lane's coupling peak is drawn around ``coupling_db`` with a ±``spread_db``
    uniform spread (lane-to-lane variation), and each gets a distinct data seed
    so the aggressors are mutually independent. NEXT is typically the stronger,
    broadband coupler; here both share ``coupling_db`` so the caller controls the
    mix explicitly through the counts.
    """
    rng = np.random.default_rng(base_seed)
    bank: list[XtalkAggressor] = []
    seed = base_seed + 1
    for kind, count in (("fext", n_fext), ("next", n_next)):
        for i in range(count):
            lvl = coupling_db + float(rng.uniform(-spread_db, spread_db))
            bank.append(synthetic_aggressor(
                kind, lvl, ui, dt, modulation=modulation, seed=seed,
                f3db=f3db, swing=swing,
                label=f"{kind.upper()}{i + 1} {lvl:.0f}dB"))
            seed += 1
    return bank


def icn_rms(aggressors, osr: int, *, modulation: str = "nrz") -> float:
    """Integrated crosstalk noise (behavioral MDFEXT/MDNEXT power sum) [V rms].

    The aggregate crosstalk is the RSS over aggressors of each coupling pulse's
    baud-cursor power times the per-symbol variance — the behavioral analog of
    802.3's ICN (integrated over the aggressors instead of the coupled PSD). It
    is the same quantity the COM engine folds in as ``σ_XT``.
    """
    if not aggressors:
        return 0.0
    if modulation == "pam4":
        lv = pam4_levels(1.0)
    else:
        lv = nrz_levels(1.0)
    sym_var = float(np.mean(lv ** 2))          # normalized level variance
    power = 0.0
    for agg in aggressors:
        p = agg.pulse(osr).y
        pk = int(np.argmax(np.abs(p)))
        cursors = p[pk % osr::osr]             # baud-spaced cursors
        # each aggressor carries its own Tx swing — scale per lane, not once
        # over the whole sum (mixed-swing banks would otherwise depend on order)
        swing = float(getattr(agg, "swing", 1.0))
        power += float(np.sum(cursors ** 2)) * sym_var * swing ** 2
    return float(np.sqrt(power))


def inject_crosstalk(rx_y: np.ndarray, aggressors, osr: int,
                     modulation: str) -> np.ndarray:
    """Add all aggressors' victim-node contributions to ``rx_y`` in place-safe
    fashion; returns the summed waveform."""
    if not aggressors:
        return rx_y
    out = rx_y.copy()
    for agg in aggressors:
        out += agg.time_contribution(rx_y.size, osr, modulation)
    return out


def import_xtalk(network, victim_out: int, aggressor_in: int, dt: float,
                 f_max: float, n_freq: int = 4096, kind: str = "fext",
                 swing: float = 1.0, seed: int = 0) -> XtalkAggressor:
    """Extract a crosstalk coupling from a multi-port Touchstone network.

    PyBERT's ``import_fext`` analog: take the S-parameter from ``aggressor_in``
    to ``victim_out``, interpolate to a uniform DC-inclusive grid, and IFFT to
    a coupling impulse response at step ``dt``. Port indices are 0-based
    single-ended (single-ended-to-mixed-mode conversion is the caller's job for
    differential pairs).
    """
    import skrf as rf  # local import; only needed for real files

    from .response import freq2impulse, zero_pad_to_dt

    if not isinstance(network, rf.Network):
        network = rf.Network(network)
    f = np.linspace(0.0, f_max, n_freq)
    # extract the coupling path as a 1-port and interpolate like a transmission
    # term: fill 0 beyond the measured band (a coupling doesn't return at high
    # f), which also supplies 0 below the lowest measured point down to DC.
    coup = rf.Network(frequency=network.frequency,
                      s=network.s[:, victim_out, aggressor_in][:, None, None],
                      z0=50.0)
    interp = coup.interpolate(rf.Frequency.from_f(f / 1e9, unit="GHz"),
                              fill_value=0.0, bounds_error=False,
                              coords="polar", assume_sorted=True)
    s = interp.s.flatten()
    s[0] = np.abs(s[0])  # force real DC for a real impulse
    H_zp, f_zp = zero_pad_to_dt(s, f, dt)
    coupling = freq2impulse(H_zp, f_zp).y
    return XtalkAggressor(coupling=coupling, dt=dt, kind=kind, swing=swing,
                          seed=seed, label=f"{kind.upper()} imported")
