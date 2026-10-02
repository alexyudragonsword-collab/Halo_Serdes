"""Fibre transfer: multimode modal bandwidth or single-mode chromatic dispersion.

Sources:

- OM4 effective modal bandwidth 4700 MHz.km at 850 nm (TIA-492AAAD; the
  figure 802.3db's SR1 reach is budgeted on). The IEEE link model treats the
  modal response as Gaussian and the EMB as its -3 dB *optical* point, i.e.
  the modulation transfer is 0.5 there (Pepeljugoski et al., 10GbE link
  model, JLT 2003). Electrically that is -6 dB; the electrical 3 dB point is
  0.71x lower. ``modal_response`` follows that convention.
- G.652 at 1310 nm: zero-dispersion wavelength 1300-1324 nm, slope
  <= 0.092 ps/(nm^2.km), which bounds D to about -1.9 .. +1.6 ps/(nm.km)
  over the 802.3dj DR lane window (1304.5-1317.5 nm); the 802.3 DR
  dispersion tables are that range times the reach. The small-signal IM
  response of a dispersive fibre to a chirped source is
  H = cos(theta) - alpha sin(theta), theta = pi lambda^2 D L f^2 / c
  (Devaux, Sorel, Kerdiles, JLT 11(12) 1993). First fade at theta = pi / 2
  for alpha = 0.
"""

from __future__ import annotations

import numpy as np

C_M_S = 299_792_458.0


def _delay(f: np.ndarray, tau_s: float) -> np.ndarray:
    # A pure delay so the block is causal. A zero-phase Gaussian (or the
    # real-valued dispersion response) puts half its impulse at negative
    # time, which the DC-inclusive grid wraps to the end of the array; an
    # impulse trimmer then keeps the whole 32k-sample record, and a link
    # whose only other block is a delay-free ideal segment (the retimed
    # optics) pays for it on every convolution. The delay carries no model
    # content: magnitudes, cursors and BER do not see it.
    return np.exp(-2j * np.pi * f * tau_s)


def modal_sigma_s(modal_bw_mhz_km: float, length_m: float) -> float:
    """Time-domain sigma of the Gaussian modal impulse, sqrt(2 ln 2) / (2 pi f_3dBo)."""
    f0 = modal_bandwidth_hz(modal_bw_mhz_km, length_m)
    return float(np.sqrt(2.0 * np.log(2.0)) / (2.0 * np.pi * f0)) if np.isfinite(f0) else 0.0


def modal_response(f: np.ndarray, modal_bw_mhz_km: float, length_m: float) -> np.ndarray:
    """Gaussian modal low-pass; |H| = 0.5 (-3 dBo) at EMB / L, delayed by 5 sigma."""
    f = np.asarray(f, dtype=np.float64)
    if length_m <= 0.0:
        return np.ones_like(f, dtype=complex)
    f_3dbo = modal_bw_mhz_km * 1e6 / (length_m / 1e3)
    mag = np.exp(-np.log(2.0) * (f / f_3dbo) ** 2)
    return mag * _delay(f, 5.0 * modal_sigma_s(modal_bw_mhz_km, length_m))


def modal_bandwidth_hz(modal_bw_mhz_km: float, length_m: float) -> float:
    """The -3 dBo point of ``modal_response`` [Hz] (inf for zero length)."""
    if length_m <= 0.0:
        return float("inf")
    return modal_bw_mhz_km * 1e6 / (length_m / 1e3)


def dispersion_response(f: np.ndarray, dispersion_ps_nm_km: float, length_m: float,
                        wavelength_nm: float, chirp_alpha: float = 0.0) -> np.ndarray:
    """Chromatic-dispersion IM response of a chirped source, unity at DC."""
    f = np.asarray(f, dtype=np.float64)
    d_si = dispersion_ps_nm_km * 1e-12 / (1e-9 * 1e3)        # s / m^2
    lam = wavelength_nm * 1e-9
    theta = np.pi * lam ** 2 * d_si * length_m * f ** 2 / C_M_S
    mag = np.cos(theta) - chirp_alpha * np.sin(theta)
    f_fade = first_fade_hz(dispersion_ps_nm_km, length_m, wavelength_nm)
    tau = 0.25 / f_fade if np.isfinite(f_fade) else 0.0
    return mag * _delay(f, tau)


def first_fade_hz(dispersion_ps_nm_km: float, length_m: float, wavelength_nm: float) -> float:
    """Frequency of the first dispersion null (theta = pi / 2, alpha = 0)."""
    d_si = abs(dispersion_ps_nm_km) * 1e-12 / (1e-9 * 1e3)
    lam = wavelength_nm * 1e-9
    if d_si == 0.0 or length_m <= 0.0:
        return float("inf")
    return float(np.sqrt(C_M_S / (2.0 * lam ** 2 * d_si * length_m)))


def response(cfg, f: np.ndarray) -> np.ndarray:
    """Fibre transfer for an ``OpticalConfig`` on grid ``f`` [Hz]."""
    if cfg.kind == "vcsel_mmf":
        return modal_response(f, cfg.modal_bw_mhz_km, cfg.length_m)
    if cfg.kind == "eml_smf":
        return dispersion_response(f, cfg.dispersion_ps_nm_km, cfg.length_m,
                                   cfg.wavelength_nm, cfg.chirp_alpha)
    raise ValueError(f"no fibre response for optical.kind {cfg.kind!r}")
