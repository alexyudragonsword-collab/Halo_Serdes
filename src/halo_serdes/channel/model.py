"""Unified channel model: frequency-domain H(f) in, time-domain responses out."""

from __future__ import annotations

import numpy as np

from ..config.schema import ChannelConfig, LinkConfig
from ..core.waveform import ResponseSet, Waveform
from . import analytic
from .response import freq2impulse, pulse_from_impulse, trim_impulse, zero_pad_to_dt


class ChannelModel:
    """A differential channel captured as H(f) on a uniform DC-inclusive grid.

    ``H`` is the terminated voltage transfer function (Vout / Vsource, i.e.
    including the source divider: a lossless matched channel has H = 0.5).
    """

    def __init__(self, H: np.ndarray, f: np.ndarray, name: str = "") -> None:
        self.H = np.asarray(H, dtype=complex)
        self.f = np.asarray(f, dtype=np.float64)
        self.name = name
        if self.H.shape != self.f.shape:
            raise ValueError("H and f must have the same shape")

    # -- constructors -------------------------------------------------------

    @classmethod
    def from_touchstone(cls, path: str, f_max: float, n_freq: int = 4096,
                        zs_diff: float = 100.0, zl_diff: float = 100.0,
                        renumber: bool = True, lane: int = 0) -> "ChannelModel":
        """Build from a Touchstone file, terminated into zs/zl (differential ohms)."""
        # local import: only real files need scikit-rf, and it pulls scipy with
        # it — the analytic path below must stay free of both
        from .touchstone import (
            import_diff_network, interp_s2p, terminate_renormalize,
        )

        sdd = import_diff_network(path, renumber=renumber, lane=lane)
        f = np.linspace(0.0, f_max, n_freq)
        sdd_i = interp_s2p(sdd, f)
        H = terminate_renormalize(sdd_i, zs_diff, zl_diff)
        return cls(H, f, name=path)

    @classmethod
    def from_rlgc(cls, cfg: ChannelConfig, f_max: float, n_freq: int = 4096,
                  zs: float = 50.0, zl: float = 50.0) -> "ChannelModel":
        """Analytic single-ended RLGC trace terminated into zs/zl."""
        f = np.linspace(0.0, f_max, n_freq)
        r, l, g, c = analytic.skin_effect_rlgc(
            f, cfg.rdc, cfg.r_skin, cfg.l_per_m, cfg.c_per_m,
            loss_tangent=cfg.loss_tangent, g_per_m=cfg.g_per_m)
        abcd = analytic.tline_abcd(r, l, g, c, cfg.length_m, f)
        H = analytic.abcd_to_transfer(abcd, zs, zl)
        return cls(H, f, name=f"rlgc_{cfg.length_m}m")

    @classmethod
    def from_config(cls, link: LinkConfig) -> "ChannelModel":
        cfg = link.channel
        f_max = cfg.f_max if cfg.f_max is not None else 2.0 * link.symbol_rate
        if cfg.kind == "touchstone":
            if not cfg.file:
                raise ValueError("channel.kind is 'touchstone' but channel.file is unset")
            return cls.from_touchstone(cfg.file, f_max, cfg.n_freq,
                                       cfg.zs_diff, cfg.zl_diff, cfg.renumber, cfg.lane)
        return cls.from_rlgc(cfg, f_max, cfg.n_freq)

    # -- derived quantities -------------------------------------------------

    def insertion_loss_db(self) -> np.ndarray:
        """|H| in dB normalized to the ideal matched divider (0.5)."""
        with np.errstate(divide="ignore"):
            return 20.0 * np.log10(np.abs(self.H) / 0.5)

    def loss_at(self, freq: float) -> float:
        """Insertion loss [dB, negative] at a given frequency (e.g. Nyquist)."""
        return float(np.interp(freq, self.f, self.insertion_loss_db()))

    def impulse(self, dt: float) -> Waveform:
        """Impulse response at time step <= dt (zero-padding sets the grid)."""
        H_zp, f_zp = zero_pad_to_dt(self.H, self.f, dt)
        return freq2impulse(H_zp, f_zp)

    def response_set(self, dt: float, trim: bool = True,
                     keep_energy: float = 0.9999) -> ResponseSet:
        h = self.impulse(dt)
        if trim:
            h, _ = trim_impulse(h, keep_energy=keep_energy)
        return ResponseSet(h, name=self.name or "channel")

    def pulse(self, dt: float, osr: int, trim: bool = True) -> Waveform:
        rs = self.response_set(dt, trim=trim)
        return pulse_from_impulse(rs.h, osr)
