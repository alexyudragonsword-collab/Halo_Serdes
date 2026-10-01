"""Unified channel model: frequency-domain H(f) in, time-domain responses out."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config.schema import ChannelConfig, LinkConfig, OpticalConfig, TopologyConfig
from ..core.waveform import ResponseSet, Waveform
from . import analytic
from .response import freq2impulse, pulse_from_impulse, trim_impulse, zero_pad_to_dt


@dataclass(frozen=True)
class OpticalStages:
    """What an optical topology adds to a channel: where to cut the cascade
    for noise injection, and the noise itself.

    ``pre_pd`` is segment A x E/O x fibre (the photodiode current node),
    ``post_pd`` is O/E x segment B. Both engines take the split from here so
    they cut at the same place (``engine/optical_stage.py``).
    """

    pre_pd: "ChannelModel"
    post_pd: "ChannelModel"
    noise: object                   # optical.OpticalNoise
    config: TopologyConfig


class ChannelModel:
    """A differential channel captured as H(f) on a uniform DC-inclusive grid.

    ``H`` is the terminated voltage transfer function (Vout / Vsource, i.e.
    including the source divider: a lossless matched channel has H = 0.5).
    ``ref_gain`` is that ideal value, the reference ``insertion_loss_db`` is
    measured against: 0.5 for an electrical segment, 1.0 for a unity-DC
    optical block, and the product for a cascade -- so the loss of a cascade
    is the sum of its parts' losses, not that plus a divider per stage.
    """

    def __init__(self, H: np.ndarray, f: np.ndarray, name: str = "",
                 ref_gain: float = 0.5) -> None:
        self.H = np.asarray(H, dtype=complex)
        self.f = np.asarray(f, dtype=np.float64)
        self.name = name
        self.ref_gain = float(ref_gain)
        self.optical: OpticalStages | None = None
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
    def from_channel_config(cls, cfg: ChannelConfig, symbol_rate: float,
                            label: str = "channel") -> "ChannelModel":
        """One electrical segment from its ``ChannelConfig``."""
        f_max = cfg.f_max if cfg.f_max is not None else 2.0 * symbol_rate
        if cfg.kind == "touchstone":
            if not cfg.file:
                raise ValueError(f"{label}.kind is 'touchstone' but {label}.file is unset")
            return cls.from_touchstone(cfg.file, f_max, cfg.n_freq,
                                       cfg.zs_diff, cfg.zl_diff, cfg.renumber, cfg.lane)
        return cls.from_rlgc(cfg, f_max, cfg.n_freq)

    @classmethod
    def from_optical(cls, opt: OpticalConfig, f: np.ndarray, block: str) -> "ChannelModel":
        """One optical block ("eo", "fiber", "oe") as a unity-DC-gain model on ``f``."""
        from .. import optical

        mod = {"eo": optical.eo, "fiber": optical.fiber, "oe": optical.oe}[block]
        return cls(mod.response(opt, f), f, name=f"{opt.kind}:{block}", ref_gain=1.0)

    @classmethod
    def from_config(cls, link: LinkConfig) -> "ChannelModel":
        if link.topology is None:
            return cls.from_channel_config(link.channel, link.symbol_rate)
        return cls.from_topology(link)

    @classmethod
    def from_topology(cls, link: LinkConfig) -> "ChannelModel":
        """segment A x E/O x fibre x O/E x segment B, carrying ``optical``.

        The noise scale is fixed here, from segment A's DC gain and the Tx
        swing: the steady outer-level separation that reaches the photodiode
        is what the config's OMA means.
        """
        from ..optical import OpticalNoise

        top = link.topology
        seg_a = cls.from_channel_config(top.seg_a, link.symbol_rate, "topology.seg_a")
        seg_b = cls.from_channel_config(top.seg_b, link.symbol_rate, "topology.seg_b")
        f = seg_a.f
        eo = cls.from_optical(top.optical, f, "eo")
        fib = cls.from_optical(top.optical, f, "fiber")
        oe = cls.from_optical(top.optical, f, "oe")
        pre_pd = cls.cascade(seg_a, eo, fib)
        post_pd = cls.cascade(oe, seg_b)
        full = cls.cascade(pre_pd, post_pd)
        swing_pd = link.tx.swing * abs(seg_a.H[0]) * float(np.sum(link.tx.fir_taps))
        noise = OpticalNoise.from_config(top.optical, link.modulation, swing_pd, link.dt)
        full.optical = OpticalStages(pre_pd=pre_pd, post_pd=post_pd, noise=noise, config=top)
        return full

    @classmethod
    def cascade(cls, *models: "ChannelModel") -> "ChannelModel":
        """Point-wise product of models that share one frequency grid."""
        if not models:
            raise ValueError("cascade needs at least one model")
        f = models[0].f
        for m in models[1:]:
            if m.f.shape != f.shape or not np.allclose(m.f, f, rtol=1e-9, atol=0.0):
                raise ValueError(f"cannot cascade {models[0].name!r} with {m.name!r}: "
                                 "frequency grids differ")
        H = np.ones_like(f, dtype=complex)
        gain = 1.0
        for m in models:
            H = H * m.H
            gain *= m.ref_gain
        return cls(H, f, name=" x ".join(m.name or "?" for m in models), ref_gain=gain)

    # -- derived quantities -------------------------------------------------

    def insertion_loss_db(self) -> np.ndarray:
        """|H| in dB normalized to the ideal (lossless, matched) model, ``ref_gain``."""
        with np.errstate(divide="ignore"):
            return 20.0 * np.log10(np.abs(self.H) / self.ref_gain)

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
