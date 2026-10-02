"""Level-dependent receiver noise at the photodiode node.

Three white current-noise terms referred to the TIA input (Agrawal ch. 4.4):

    shot   S_shot = 2 q R P              [A^2/Hz]
    RIN    S_rin  = RIN * (R P)^2        [A^2/Hz], RIN linear from dB/Hz
    TIA    S_tia  = i_n^2                [A^2/Hz], input-referred density

Shot and RIN scale with the optical power ``P`` of the level being received,
so a PAM4 upper level is noisier than the lower one -- the asymmetry the
electrical model cannot express and the reason this exists. The noise is
injected as white samples at the photodiode current node, *before* the O/E
response: that is where all three terms physically are (two are
photocurrent, one is input-referred), and it lets the TIA bandwidth set the
noise bandwidth instead of the simulation's sampling rate.

Scale: the simulation keeps its electrical volts. ``volts_per_amp`` maps a
photocurrent to those volts such that the steady outer-level separation at
the photodiode (``signal_swing_v``) corresponds to R * OMA. Shot and RIN
read the instantaneous power off the waveform itself (so ISI is in them);
``sigma_per_level`` evaluates the same densities at the nominal level
powers, which is the statistical engine's approximation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Q_C = 1.602176634e-19


@dataclass(frozen=True)
class OpticalNoise:
    level_powers_w: np.ndarray     # ascending, one per transmitted level
    responsivity_a_w: float
    rin_lin: float                 # 10 ** (rin_db_hz / 10)
    tia_noise_a2_hz: float         # i_n^2
    signal_swing_v: float          # simulation volts for the outer-level separation at the PD node
    sample_rate_hz: float          # 1 / dt: white-noise bandwidth of one sample

    @classmethod
    def from_config(cls, opt, modulation: str, signal_swing_v: float,
                    dt: float, drive_levels=None) -> "OpticalNoise":
        from .oe import level_powers

        return cls(level_powers_w=level_powers(opt, modulation, drive_levels),
                   responsivity_a_w=opt.responsivity_a_w,
                   rin_lin=10.0 ** (opt.rin_db_hz / 10.0),
                   tia_noise_a2_hz=(opt.tia_noise_pa_sqrthz * 1e-12) ** 2,
                   signal_swing_v=signal_swing_v,
                   sample_rate_hz=1.0 / dt)

    # -- scale ------------------------------------------------------------

    @property
    def oma_w(self) -> float:
        return float(self.level_powers_w[-1] - self.level_powers_w[0])

    @property
    def volts_per_amp(self) -> float:
        return self.signal_swing_v / (self.responsivity_a_w * self.oma_w)

    @property
    def p_mid_w(self) -> float:
        return float(0.5 * (self.level_powers_w[0] + self.level_powers_w[-1]))

    def power_w(self, y_v: np.ndarray) -> np.ndarray:
        """Instantaneous optical power behind a PD-node waveform [W], >= 0."""
        return np.maximum(self.p_mid_w + y_v / (self.volts_per_amp * self.responsivity_a_w), 0.0)

    # -- densities --------------------------------------------------------

    def current_psd_a2_hz(self, p_w, p_var_w2=0.0) -> np.ndarray:
        """One-sided input-referred current noise density at optical power ``p_w``.

        ``p_var_w2`` is the variance of the power when ``p_w`` is only its
        expectation (a level seen through ISI): shot noise is linear in P
        and needs only the mean, RIN goes as P^2 and picks up the variance.
        """
        r = self.responsivity_a_w
        p = np.asarray(p_w, dtype=np.float64)
        return (2.0 * Q_C * r * p + self.rin_lin * r * r * (p * p + p_var_w2)
                + self.tia_noise_a2_hz)

    def sigma_v(self, p_w, p_var_w2=0.0) -> np.ndarray:
        """Per-sample white-noise sigma in simulation volts at power ``p_w``.

        A sample of white noise spans the one-sided band f_s / 2, so its
        variance is S * f_s / 2; after the O/E response the variance that
        survives is S * B_noise(TIA), independent of the oversampling.
        """
        var_a2 = self.current_psd_a2_hz(p_w, p_var_w2) * (0.5 * self.sample_rate_hz)
        return np.sqrt(var_a2) * self.volts_per_amp

    def sample_var_v2(self, p_mean_w, p_var_w2=0.0) -> np.ndarray:
        """Per-sample noise variance [V^2] for a power of given mean and variance."""
        return (self.current_psd_a2_hz(p_mean_w, p_var_w2) * (0.5 * self.sample_rate_hz)
                * self.volts_per_amp ** 2)

    # -- the two engines ----------------------------------------------------

    def inject(self, y_pd: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Time engine: add noise whose sigma follows the waveform's own power."""
        sig = self.sigma_v(self.power_w(y_pd))
        return y_pd + rng.normal(size=y_pd.size) * sig

    def sigma_per_level(self) -> np.ndarray:
        """Statistical engine: per-sample sigma at each nominal level power."""
        return self.sigma_v(self.level_powers_w)
