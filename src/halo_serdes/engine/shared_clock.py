"""N lanes sampled by one CDR's clock (ROADMAP P3 #9c / #9d, the dynamic part).

``analysis/skew.py`` reads a lane's BER at a static offset from its own
lock point. What it cannot see is what the shared loop does over time: with
one lane's loop driving all of them (``"master"``) every other lane is
sampled where *that* lane's hunting puts the clock, which its own data has
no part in; with one loop listening to all of them (``"average"``) each
lane's data is one vote in N. This runs the lanes together, symbol by
symbol, to measure both.

Each lane is the time engine's own run with its kernel's loop gains at
zero. Mixed-signal (``timedomain._ms_prepare``): after every symbol the
shared loop takes the lanes' bang-bang votes -- the master's, or their mean
-- through the same proportional-integral filter and clamp the kernel uses
and moves every lane's sampler by the result. ADC (``timedomain.
_adc_link_steps``, ROADMAP P3 #9d): the Mueller-Muller detector is read back
from each lane's samples as the kernel forms it, summed over the
interleaver's block, and the block's correction goes through the kernel's
filter, clamp and latency queue, spread over the block's symbols. On the
PAM4 ADC presets the detector reads the equalised samples, which each lane's
FFE re-adapts wherever it samples: an averaging loop then has no pull towards
the lanes' mean skew and the clock stays where it starts (cairn/多lane建模.md
§9.4). The receiver clock (its drawn wander,
SSC) is the master's, the same for every lane. Each lane carries its own
data, independent of the others' (the default is iid symbols per lane: a
PRBS would be the same stream on every lane), its own noise and its own
transmitter jitter.

A lane whose data arrives ``s`` UI later starts ``s`` UI early against its
pulse peak, its slicer levels, DFE taps (and FFE) from the cursors there; the loop
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
    """Run ``len(skews_ui)`` lanes on one sampling clock.

    ``cfg``: one config for every lane or a list (one per lane, one
    receiver architecture; the master's -- lane 0's for ``"average"`` -- sets
    the receiver clock and the loop gains). Lane i runs from seed
    ``sim.seed + i``. ``skews_ui[i]``: how much later lane i's data arrives
    [UI], each within half a UI of the others' frame (beyond that the lane
    samples another symbol). ``policy``: ``"master"`` (lane ``master``'s
    detector drives the loop) or ``"average"`` (the mean of every lane's).
    ``channel``: one for every lane or a list. ``symbols``: per lane, the
    user symbol streams to send (default :func:`lane_symbols`).
    """
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
    archs = {c.rx.arch for c in cfgs}
    if len(archs) > 1:
        raise ValueError("shared-clock lanes are one receiver architecture")
    for c in cfgs:
        if c.sim.stream:
            raise ValueError("shared-clock lanes run on whole waveforms (sim.stream off)")
        if c.numeric.mode == "fixed":
            raise NotImplementedError("shared-clock lanes run the float receiver "
                                      "(numeric.mode 'fixed' replays its own loop)")
    if np.ptp(s) >= 1.0:
        raise ValueError("skews_ui: lanes more than a UI apart sample other symbols")
    lead = master if policy == "master" else 0
    cfgs = [dataclasses.replace(c, sim=dataclasses.replace(c.sim, seed=c.sim.seed + i))
            for i, c in enumerate(cfgs)]
    syms = ([lane_symbols(c, i) for i, c in enumerate(cfgs)] if symbols is None
            else list(symbols))
    shifts = [-(s[i] - s[lead]) * cfgs[i].osr for i in range(n)]
    run = _run_adc if archs == {"adc_dsp"} else _run_ms
    lanes, disp = run(cfgs, chans, syms, shifts, policy, master, lead)
    return SharedClockRun(policy, s, tuple(lanes), disp)


def _run_ms(cfgs, chans, syms, shifts, policy, master, lead):
    """Mixed-signal lanes: the bang-bang votes, one per symbol."""
    from .timedomain import _ms_finish, _ms_prepare

    n = len(cfgs)

    def prep(i, rx_clk=None):
        return _ms_prepare(cfgs[i], chans[i], False, False, None, None, None, syms[i],
                           start_shift=shifts[i], rx_clk=rx_clk, external_loop=True)

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
    return out, disp[:k]


def _run_adc(cfgs, chans, syms, shifts, policy, master, lead):
    """ADC lanes: the kernel's Mueller-Muller detector, read back from each
    lane's own samples, summed over the interleaver's block and taken through
    the loop filter, clamp and latency queue the kernel uses
    (``cdr/adc_kernel.py``); the correction is spread over the block's
    symbols as the kernel spreads it."""
    from .timedomain import _adc_finish, _adc_link_steps

    n = len(cfgs)

    def prep(i, rx_clk=None):
        g = _adc_link_steps(cfgs[i], chans[i], False, False, None, None, None, syms[i],
                            start_shift=shifts[i], rx_clk=rx_clk, external_loop=True)
        return g, next(g)

    steps, loops = [None] * n, [None] * n
    steps[lead], loops[lead] = prep(lead)
    clk = loops[lead].run.params[22]
    for i in range(n):
        if i != lead:
            steps[i], loops[i] = prep(i, rx_clk=np.resize(clk, cfgs[i].sim.n_symbols))
    lp = loops[lead]
    if any(lo.n_ti != lp.n_ti or lo.n_pre != lp.n_pre or lo.pd_src != lp.pd_src for lo in loops):
        raise ValueError("ADC lanes on one clock need one interleave, FFE precursor "
                         "count and detector input")
    kp, ki, clamp, n_ti, n_pre = lp.kp, lp.ki, lp.clamp, lp.n_ti, lp.n_pre
    src = {0: 2, 1: 3, 2: 6}[lp.pd_src]            # y_sl, r_sl, q_hist in run.out
    runs = [lo.run for lo in loops]
    n_sym = min(r.n_symbols for r in runs)
    disp = np.zeros(n_sym)
    acc = np.zeros(n)
    queue = np.zeros(lp.latency_blocks + 1)
    qi = 0
    n_acc = 0
    integ = corr_now = moved = 0.0
    k = 0
    while k < n_sym:
        for r in runs:
            r.advance(k + 1)
        if any(r.done <= k for r in runs):
            break
        sk = k - n_pre
        if sk >= 2:
            for j, r in enumerate(runs):
                x = r.out[src]
                a0 = 1.0 if x[sk - 2] > 0 else -1.0
                a2 = 1.0 if x[sk] > 0 else -1.0
                acc[j] += x[sk - 1] * (a2 - a0)
            n_acc += 1
            if n_acc >= n_ti:
                pd = -(acc / n_ti) - lp.pd_offset
                vote = float(pd[master] if policy == "master" else np.mean(pd))
                integ += ki * vote
                c = kp * vote + integ
                if clamp > 0.0:
                    c = min(max(c, -clamp), clamp)
                queue[qi] = c
                qi = (qi + 1) % queue.size
                corr_now = float(queue[qi])
                acc[:] = 0.0
                n_acc = 0
        step = corr_now / n_ti
        for r in runs:
            r.fs[0] += step
        moved += step
        disp[k] = moved
        k += 1
    out = []
    for g, r in zip(steps, runs):
        r.done = min(r.done, k)
        out.append(_adc_finish(g, r.result()))
    return out, disp[:k]
