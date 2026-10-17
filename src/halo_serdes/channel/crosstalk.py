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

from ..core.mapping import (bits_needed, bits_to_symbols, levels_for, nrz_levels, pam4_levels,
                            symbol_variance)
from ..core.sampler import baud_samples, hold
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
        modulation: ``'nrz'``, ``'pam4'``, ``'pam6'`` (the corner32 pair
            stream, as the victim sends it) or ``'pam8'`` for the aggressor data.
        seed: RNG seed for the aggressor's independent random data.
        label: display name.
        skew_s: how much later than the victim the aggressor launches its
            symbols [s], >= 0 -- the lane-to-lane skew of a synchronous lane
            (same clock, fixed offset). It decides where on the coupling
            pulse the victim's sampling instants fall, i.e. which of the
            aggressor's cursors it sees; applied to the nearest sample.
        ppm: the aggressor's symbol-rate offset from the victim's [ppm] -- a
            quasi-synchronous lane on its own reference clock. Its symbols
            are held at its own rate on the victim's sample grid
            (:func:`symbol_index`), so the phase the victim sees walks
            ``ppm`` 1e-6 UI per UI; the statistical engine averages over that
            phase. 0 is the synchronous lane, sample for sample as before.
    """

    coupling: np.ndarray
    dt: float
    kind: str = "fext"
    swing: float = 1.0
    modulation: str = "nrz"
    seed: int = 0
    label: str = ""
    skew_s: float = 0.0
    ppm: float = 0.0

    def __post_init__(self):
        self.coupling = np.asarray(self.coupling, dtype=np.float64)
        if not self.skew_s >= 0.0:
            raise ValueError(f"skew_s must be >= 0, got {self.skew_s}")

    @property
    def launched_coupling(self) -> np.ndarray:
        """The coupling as the victim sees it: delayed by ``skew_s`` to the
        nearest sample. Both engines use this one."""
        k = int(round(self.skew_s / self.dt))
        return np.concatenate([np.zeros(k), self.coupling]) if k else self.coupling

    def pulse(self, osr: int) -> Waveform:
        """Coupling pulse response (single aggressor symbol, ``skew_s``
        included) — feeds the statistical engine's ``xtalk_pulses`` at the
        same node, on the same time origin as the victim's pulse."""
        return pulse_from_impulse(Waveform(self.launched_coupling, self.dt), osr)

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
        v = self.symbol_volts(n_samples, osr, modulation)
        agg_tx = hold(v, osr) if self.ppm == 0.0 else v[symbol_index(np.arange(n_samples), osr, self.ppm)]
        y = np.convolve(agg_tx, self.launched_coupling)[:n_samples]
        if y.size < n_samples:
            y = np.pad(y, (0, n_samples - y.size))
        return y

    def symbol_volts(self, n_samples: int, osr: int,
                     modulation: str | None = None) -> np.ndarray:
        """The aggressor's per-UI Tx volts behind :meth:`time_contribution`
        (the streamed engine holds and filters them block by block)."""
        mod = modulation if modulation is not None else self.modulation
        rng = np.random.default_rng(self.seed)
        n_sym = n_samples // osr + self.coupling.size // osr + 2
        if self.ppm > 0.0:                      # a faster lane sends more in the run
            n_sym = max(n_sym, int(symbol_index(np.array([n_samples]), osr, self.ppm)[0])
                        + self.coupling.size // osr + 2)
        if mod == "pam4":
            lv = pam4_levels(self.swing)
            sym = rng.integers(0, 4, size=n_sym)
        elif mod == "nrz":
            lv = nrz_levels(self.swing)
            sym = rng.integers(0, 2, size=n_sym)
        elif mod == "pam6":
            # the corner32 pair stream the victim lane sends too
            # (core/mapping.py): the outer levels 1/8 of the time, the inner
            # 3/16, never two outer ones in a pair. Drawn uniform over the
            # six until 2026-10-17 (second moment 0.467 against 0.4)
            lv = levels_for(mod, self.swing)
            bits = rng.integers(0, 2, size=bits_needed(n_sym, mod))
            sym = bits_to_symbols(bits, mod)[:n_sym]
        else:
            lv = levels_for(mod, self.swing)      # PAM8: uniform over the eight
            sym = rng.integers(0, lv.size, size=n_sym)
        return lv[sym]


def symbol_index(samples: np.ndarray, osr: int, ppm: float) -> np.ndarray:
    """Which of an aggressor's symbols is on its line at each victim sample.

    Its UI is the victim's / (1 + ``ppm`` 1e-6): symbol ``floor(n (1 + ppm
    1e-6) / osr)`` at sample n, the plain ``n // osr`` at ``ppm`` 0. One
    function, so the default and the streamed engine hold the same way."""
    samples = np.asarray(samples, dtype=np.int64)
    if ppm == 0.0:
        return samples // osr
    return np.floor(samples * (1.0 + ppm * 1e-6) / osr).astype(np.int64)


def synthetic_aggressor(kind: str, coupling_db: float, ui: float, dt: float,
                        *, modulation: str = "nrz", seed: int = 0,
                        f3db: float | None = None, swing: float = 1.0,
                        label: str = "", skew_s: float = 0.0,
                        ppm: float = 0.0) -> XtalkAggressor:
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
                          label=label or f"{kind.upper()} {coupling_db:.0f}dB",
                          skew_s=skew_s, ppm=ppm)


def aggressor_bank(n_fext: int, n_next: int, coupling_db: float, ui: float,
                   dt: float, *, modulation: str = "nrz", swing: float = 1.0,
                   spread_db: float = 2.0, base_seed: int = 0,
                   f3db: float | None = None, skew_s: float = 0.0,
                   ppm: float = 0.0) -> list[XtalkAggressor]:
    """A multi-lane crosstalk environment: ``n_fext`` FEXT + ``n_next`` NEXT
    aggressors, each an independent lane.

    Each lane's coupling peak is drawn around ``coupling_db`` with a ±``spread_db``
    uniform spread (lane-to-lane variation), and each gets a distinct data seed
    so the aggressors are mutually independent. NEXT is typically the stronger,
    broadband coupler; here both share ``coupling_db`` so the caller controls the
    mix explicitly through the counts. ``skew_s`` / ``ppm``: every lane's (see
    :class:`XtalkAggressor`).
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
                label=f"{kind.upper()}{i + 1} {lvl:.0f}dB", skew_s=skew_s, ppm=ppm))
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
    lv = levels_for(modulation, 1.0)
    # normalized level variance, as often as each level is sent (PAM6's
    # corner32 map: 0.4, not the uniform 0.467)
    sym_var = symbol_variance(lv, modulation)
    power = 0.0
    for agg in aggressors:
        p = agg.pulse(osr).y
        pk = int(np.argmax(np.abs(p)))
        # baud-spaced cursors: samples of a response, not a symbol
        # sequence, so the array primitive rather than sample_baud
        cursors = baud_samples(p, osr, pk % osr)
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


def aggressors_from_config(cfg) -> list[XtalkAggressor]:
    """The aggressors ``cfg.crosstalk`` describes, on the victim's grid.

    The bank (``aggressor_bank``) first, then the listed ones; every lane
    sends the victim's modulation at the victim's Tx swing. A listed lane
    without a ``seed`` takes the next one after the bank's."""
    xc = cfg.crosstalk
    if not xc.active:
        return []
    swing = cfg.tx.swing
    out = aggressor_bank(xc.n_fext, xc.n_next, xc.coupling_db, cfg.ui, cfg.dt,
                         modulation=cfg.modulation, swing=swing, spread_db=xc.spread_db,
                         base_seed=xc.seed, skew_s=xc.skew_ui * cfg.ui, ppm=xc.ppm)
    next_seed = xc.seed + xc.n_fext + xc.n_next + 1
    for i, a in enumerate(xc.aggressors):
        seed = a.seed if a.seed is not None else next_seed + i
        if a.file:
            f_max = cfg.channel.f_max or 2.0 / cfg.ui
            agg = import_xtalk(a.file, a.victim_port, a.aggressor_port,
                               cfg.dt, f_max, n_freq=cfg.channel.n_freq, kind=a.kind,
                               swing=swing, seed=seed)
            agg.modulation, agg.skew_s, agg.ppm = cfg.modulation, a.skew_ui * cfg.ui, a.ppm
            agg.__post_init__()
        else:
            agg = synthetic_aggressor(a.kind, a.coupling_db, cfg.ui, cfg.dt,
                                      modulation=cfg.modulation, seed=seed, f3db=a.f3db_hz,
                                      swing=swing, skew_s=a.skew_ui * cfg.ui, ppm=a.ppm)
        out.append(agg)
    return out


def engine_aggressors(cfg, xtalk=None) -> list[XtalkAggressor]:
    """The aggressors an engine injects: ``cfg.crosstalk`` unless an explicit
    ``xtalk`` (a list, possibly empty) replaces it -- with a warning when the
    config describes aggressors too, since then one of the two is ignored."""
    if xtalk is None:
        return aggressors_from_config(cfg)
    if cfg.crosstalk.active:
        import warnings

        warnings.warn("crosstalk passed explicitly (xtalk=...) replaces cfg.crosstalk; "
                      "the config's aggressors are ignored", stacklevel=3)
    return list(xtalk)
