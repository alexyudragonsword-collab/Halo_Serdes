"""N mixed-signal lanes sampled by one CDR's clock (ROADMAP P3 #9c, dynamic part).

``analysis/skew.py`` reads a lane's BER at a static offset from its own
lock point. What it cannot see is what the shared loop does over time: with
one lane's loop driving all of them (``"master"``) every other lane is
sampled where *that* lane's hunting puts the clock, which its own data has
no part in; with one loop listening to all of them (``"average"``) each
lane's data is one vote in N. This runs the lanes together, symbol by
symbol, to measure both.

Each lane is the time engine's mixed-signal run (``timedomain._ms_prepare``)
with its kernel's loop gains at zero; after every symbol the shared loop
takes the lanes' bang-bang votes -- the master's, or their mean -- through
the same proportional-integral filter and clamp the kernel uses and moves
every lane's sampler by the result. The receiver clock (its drawn wander,
SSC) is the master's, the same for every lane. Each lane carries its own
data, independent of the others' (the default is iid symbols per lane: a
PRBS would be the same stream on every lane), its own noise and its own
transmitter jitter.

A lane whose data arrives ``s`` UI later starts ``s`` UI early against its
pulse peak, its slicer levels and DFE taps from the cursors there; the loop
then moves all lanes together, so the offsets between them stay as set.
With one lane this is ``run_time_link`` on iid symbols to rounding (the
kernel adds the loop's step to the sample period first, this adds it after).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from ..config.schema import LinkConfig
from .result import SimResult

POLICIES = ("master", "average")


@dataclass(frozen=True)
class SharedClockRun:
    """One result per lane, and the shared clock's displacement from where
    lane 0 started (samples, one per symbol)."""

    policy: str
    skews_ui: np.ndarray
    lanes: tuple[SimResult, ...]
    displacement: np.ndarray

    @property
    def ber(self) -> np.ndarray:
        return np.array([r.ber.ber for r in self.lanes])

    @property
    def n_errors(self) -> np.ndarray:
        return np.array([r.ber.n_errors for r in self.lanes])


def lane_symbols(cfg: LinkConfig, lane: int) -> np.ndarray:
    """Lane ``lane``'s default stream: iid symbols, from the lane's own seed."""
    from .static_link import _levels

    rng = np.random.default_rng((int(cfg.sim.seed), 7919, int(lane)))
    return rng.integers(0, _levels(cfg).size, cfg.sim.n_symbols)


def run_shared_clock(cfg: LinkConfig | list, skews_ui, policy: str = "master",
                     master: int = 0, channel=None, symbols=None) -> SharedClockRun:
    """Run ``len(skews_ui)`` mixed-signal lanes on one sampling clock.

    ``cfg``: one config for every lane or a list (one per lane; the master's
    -- lane 0's for ``"average"`` -- sets the receiver clock and the loop
    gains). Lane i runs from seed ``sim.seed + i``. ``skews_ui[i]``: how
    much later lane i's data arrives [UI], each within half a UI of the
    others' frame (beyond that the lane samples another symbol). ``policy``:
    ``"master"`` (lane ``master``'s votes drive the loop) or ``"average"``
    (the mean of every lane's). ``channel``: one for every lane or a list.
    ``symbols``: per lane, the user symbol streams to send (default
    :func:`lane_symbols`).
    """
    from .timedomain import _ms_finish, _ms_prepare

    s = np.asarray(skews_ui, dtype=np.float64)
    n = s.size
    if s.ndim != 1 or n == 0:
        raise ValueError("skews_ui: one skew per lane")
    if policy not in POLICIES:
        raise ValueError(f"policy {policy!r}: one of {POLICIES}")
    if not 0 <= master < n:
        raise ValueError(f"master {master}: lanes are 0..{n - 1}")
    cfgs = list(cfg) if isinstance(cfg, (list, tuple)) else [cfg] * n
    chans = list(channel) if isinstance(channel, (list, tuple)) else [channel] * n
    if len(cfgs) != n or len(chans) != n:
        raise ValueError(f"{len(cfgs)} configs / {len(chans)} channels for {n} lanes")
    for c in cfgs:
        if c.rx.arch != "mixed_signal":
            raise NotImplementedError("shared-clock lanes are mixed-signal receivers "
                                      "(the ADC receiver's loop is another kernel)")
        if c.sim.stream:
            raise ValueError("shared-clock lanes run on whole waveforms (sim.stream off)")
    if np.ptp(s) >= 1.0:
        raise ValueError("skews_ui: lanes more than a UI apart sample other symbols")
    lead = master if policy == "master" else 0
    cfgs = [dataclasses.replace(c, sim=dataclasses.replace(c.sim, seed=c.sim.seed + i))
            for i, c in enumerate(cfgs)]
    syms = ([lane_symbols(c, i) for i, c in enumerate(cfgs)] if symbols is None
            else list(symbols))

    def prep(i, rx_clk=None):
        return _ms_prepare(cfgs[i], chans[i], False, False, None, None, None, syms[i],
                           start_shift=-(s[i] - s[lead]) * cfgs[i].osr, rx_clk=rx_clk,
                           external_loop=True)

    lanes = [None] * n
    lanes[lead] = prep(lead)
    clk = lanes[lead].run.params[14]
    for i in range(n):
        if i != lead:
            lanes[i] = prep(i, rx_clk=np.resize(clk, cfgs[i].sim.n_symbols))
    kp, ki, clamp = lanes[lead].kp, lanes[lead].ki, lanes[lead].clamp
    runs = [ln.run for ln in lanes]
    n_sym = min(r.n_symbols for r in runs)
    disp = np.zeros(n_sym)
    integ = 0.0
    moved = 0.0
    k = 0
    while k < n_sym:
        for r in runs:
            r.advance(k + 1)
        if any(r.done <= k for r in runs):
            break
        if policy == "master":
            vote = float(runs[master].pd_hist[k])
        else:
            vote = float(np.mean([r.pd_hist[k] for r in runs]))
        integ += ki * vote
        step = kp * vote + integ
        if clamp > 0.0:
            step = min(max(step, -clamp), clamp)
        for r in runs:
            r.fs[0] += step
        moved += step
        disp[k] = moved
        k += 1
    out = []
    for ln in lanes:
        ln.run.done = min(ln.run.done, k)
        out.append(_ms_finish(ln, ln.run.result()))
    return SharedClockRun(policy, s, tuple(out), disp[:k])
