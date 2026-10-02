"""Retimed links: one link cut into segments that each decide and re-transmit.

A DSP-retimed optical module puts a receiver and a transmitter at its
ingress and egress. Host TX -> segment A -> retimer is one link; retimer ->
E/O -> fibre -> O/E -> retimer is a second; retimer -> segment B -> host RX a
third. Each segment's receiver decides symbols and the next segment's
transmitter sends *those* symbols, so an error made anywhere propagates to the
host untouched (FEC is end to end, the module does not terminate it) and the
end-to-end BER is the host's decisions scored against the host's own stream.

The hand-off is symbol-domain on purpose: a segment hands out the user-domain
decisions it scored (``SimResult.extras["decisions"]``), the next segment is
run with ``symbols=`` and re-enters the waveform domain only through
``tx/builder.py`` (invariant #5). Each segment keeps its own warm-up, so the
host's stream is compared from the sum of the segments' warm-ups on.

Without a retimer the cascade is one segment: ``run_time_link`` on the
whole topology, unchanged.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np

from ..channel import ChannelModel
from ..config.schema import ChannelConfig, LinkConfig, TopologyConfig
from ..core.prbs import BerResult, symbol_checker
from .result import SimResult
from .static_link import make_pattern
from .timedomain import run_time_link


@dataclass
class Segment:
    name: str
    cfg: LinkConfig
    result: SimResult
    offset: int                 # first host symbol this segment's decisions line up with
    stat_ber: float | None = None
    channel: ChannelModel | None = None


@dataclass
class CascadeResult:
    segments: list[Segment]
    ber: BerResult              # end to end: host decisions vs host symbols
    ser: float
    n_symbols: int
    ber_product: float          # 1 - prod(1 - p_i) from the segments' own BERs
    stat_ber: float | None = None   # same product on the statistical engine's p_i
    extras: dict = field(default_factory=dict)

    @property
    def segment_bers(self) -> list[float]:
        return [s.result.ber.ber for s in self.segments]

    def summary(self) -> str:
        segs = " | ".join(f"{s.name} {s.result.ber.ber:.2e}" for s in self.segments)
        return (f"end-to-end BER={self.ber.ber:.3e} ({self.ber.n_errors}/{self.ber.n_checked})"
                f"  product={self.ber_product:.3e}  [{segs}]")


def _ideal_segment(like: ChannelConfig) -> ChannelConfig:
    # A zero-length analytic trace on the same grid: H = 0.5, the matched
    # divider every segment in this library is referenced to.
    return ChannelConfig(kind="analytic", length_m=0.0, n_freq=like.n_freq, f_max=like.f_max)


def segment_configs(cfg: LinkConfig) -> list[tuple[str, LinkConfig]]:
    """The LinkConfigs of the links a topology is cut into, in signal order.

    Segment k > 1 gets seed + k so its noise is independent of the others';
    the retimer's receiver and transmitter come from the topology; the host
    keeps its own TX on segment 1 and RX on the last.
    """
    top = cfg.topology
    if top is None or top.retimer == "none":
        return [("link", cfg)]
    rep = dataclasses.replace
    seg_a = rep(cfg, channel=top.seg_a, topology=None, rx=top.retimer_rx)
    optics = rep(cfg, topology=TopologyConfig(
        seg_a=_ideal_segment(top.seg_a), optical=top.optical, seg_b=_ideal_segment(top.seg_b)),
        tx=top.retimer_tx, rx=top.retimer_rx,
        sim=rep(cfg.sim, seed=cfg.sim.seed + 1))
    seg_b = rep(cfg, channel=top.seg_b, topology=None, tx=top.retimer_tx,
                sim=rep(cfg.sim, seed=cfg.sim.seed + 2))
    return [("host->retimer (seg A)", seg_a), ("optics", optics), ("retimer->host (seg B)", seg_b)]


def _ber_product(bers) -> float:
    q = 1.0
    for p in bers:
        q *= 1.0 - float(p)
    return 1.0 - q


def end_to_end_ber(cfg: LinkConfig, sent: np.ndarray, decided: np.ndarray) -> BerResult:
    """Score the host's decisions against what the host sent (bits, Gray for PAM4)."""
    sent = np.asarray(sent, dtype=np.int64)
    decided = np.asarray(decided, dtype=np.int64)
    n = min(sent.size, decided.size)
    if cfg.modulation == "pam4":
        return symbol_checker(sent[:n], decided[:n], gray=True)
    idx = np.nonzero(sent[:n] != decided[:n])[0]
    return BerResult(n_checked=n, n_errors=int(idx.size), error_idx=idx)


def run_cascade(cfg: LinkConfig, statistical: bool = False,
                symbols: np.ndarray | None = None) -> CascadeResult:
    """Run the segments in series on the time engine.

    ``statistical=True`` also runs the statistical engine on every segment,
    with the FFE taps the time engine converged to, and reports the product
    of its per-segment BERs beside the measured end-to-end figure (the
    cascade's own invariant-#3 check).
    """
    sent = make_pattern(cfg) if symbols is None else np.asarray(symbols, dtype=np.int64)
    stream = sent
    offset = 0
    segments: list[Segment] = []
    for name, seg_cfg in segment_configs(cfg):
        seg_cfg = dataclasses.replace(seg_cfg, sim=dataclasses.replace(
            seg_cfg.sim, n_symbols=int(stream.size)))
        cm = ChannelModel.from_config(seg_cfg)
        res = run_time_link(seg_cfg, channel=cm, symbols=stream)
        stream = np.asarray(res.extras["decisions"], dtype=np.int64)
        offset += int(res.extras["warmup"])
        segments.append(Segment(name, seg_cfg, res, offset, channel=cm))
    ber = end_to_end_ber(cfg, sent[offset:], stream)
    ser = float(np.mean(sent[offset: offset + stream.size] != stream[: sent.size - offset]))
    out = CascadeResult(segments=segments, ber=ber, ser=ser, n_symbols=int(ber.n_checked),
                        ber_product=_ber_product(s.result.ber.ber for s in segments))
    if statistical:
        from .statistical import run_statistical

        for s in segments:
            st = run_statistical(s.cfg, channel=s.channel, ffe_taps=s.result.ffe_taps,
                                 ffe_pre=s.cfg.rx.ffe.n_pre)
            s.stat_ber = float(st.ber)
        out.stat_ber = _ber_product(s.stat_ber for s in segments)
    return out


def initial_ffe_taps(cfg: LinkConfig, channel: ChannelModel | None = None) -> np.ndarray | None:
    """The MMSE FFE the ADC time engine starts from, for a statistical-only run.

    The statistical engine takes the equaliser as given; on an ADC receiver
    the time engine's own starting point (MMSE on the front-end pulse) is a
    fair stand-in when no time run is available, which is how a sweep can
    stay on the fast engine. None for a receiver without an FFE.
    """
    from ..channel.response import pulse_from_impulse
    from ..core.waveform import Waveform
    from ..dsp import channel_cursors, mmse_ffe
    from .optical_stage import apply_front_end, split_impulses

    fcfg = cfg.rx.ffe
    if cfg.rx.arch != "adc_dsp" or fcfg.n_pre + fcfg.n_post == 0:
        return None
    if channel is None:
        channel = ChannelModel.from_config(cfg)
    if getattr(channel, "optical", None) is not None:
        h1, h2 = split_impulses(cfg, channel)
        h = np.convolve(h1, h2)
    else:
        h = apply_front_end(cfg, channel.response_set(cfg.dt).h.y)
    osr = cfg.osr
    pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
    peak = int(np.argmax(np.abs(pulse.y)))
    n_pre_c, n_post_c = fcfg.n_pre + 4, fcfg.n_post + 12
    cursors = channel_cursors(pulse, osr, n_pre_c, n_post_c, peak_idx=peak)
    n_taps = fcfg.n_pre + 1 + fcfg.n_post
    return mmse_ffe(cursors, n_pre_c, n_taps, fcfg.n_pre, noise_var=cfg.rx.noise_rms ** 2)


def run_cascade_statistical(cfg: LinkConfig) -> CascadeResult | None:
    """Statistical-engine-only cascade: per-segment BER with the MMSE starting
    FFE, combined as 1 - prod(1 - p_i). No time engine, so no measured
    end-to-end count: ``ber`` carries the product with zero checked bits."""
    from .statistical import run_statistical

    segments: list[Segment] = []
    for name, seg_cfg in segment_configs(cfg):
        cm = ChannelModel.from_config(seg_cfg)
        taps = initial_ffe_taps(seg_cfg, cm)
        st = run_statistical(seg_cfg, channel=cm, ffe_taps=taps, ffe_pre=seg_cfg.rx.ffe.n_pre)
        placeholder = SimResult(ber=BerResult(0, 0), ser=float("nan"), slicer_snr_db=float("nan"),
                                n_symbols=0, ffe_taps=taps)
        segments.append(Segment(name, seg_cfg, placeholder, 0, stat_ber=float(st.ber), channel=cm))
    prod = _ber_product(s.stat_ber for s in segments)
    return CascadeResult(segments=segments, ber=BerResult(0, 0), ser=float("nan"), n_symbols=0,
                         ber_product=prod, stat_ber=prod)
