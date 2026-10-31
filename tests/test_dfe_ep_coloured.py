"""DFE error propagation behind an FFE: the coloured noise (cairn/DSP发端与PR.md §38).

The receiver's and the ADC's noise is white per UI at the FFE input; the
FFE colours it, so the noise that made a first error pulls its neighbour's
along the DFE's push and a first error is followed more often than one the
DFE made. On example 31's link frozen at its lock (CDR gains ~0, no
transmit jitter, FFE / DFE not adapting) the time engine made 1.77-1.84
errors per first one at 2.5-4 mV; the two-state chain read 1.50-1.71, the
three-state one 1.74-1.81. Deeper in a chain the noises before an error
are both a chain error's, small, and it is followed more often again (p3):
at 4 mV the time engine's p1 / p2 / p3 0.57 / 0.25 / 0.43, the model's
0.58 / 0.23 / 0.38 (ROADMAP P2 #37, 2026-10-31).
"""

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from halo_serdes.config.schema import ClockConfig
from halo_serdes.engine import run_time_link
from halo_serdes.engine.statistical import run_statistical

_EX31 = Path(__file__).parent.parent / "examples" / "31_pll_clock_profile.py"


def _frozen(noise, n_symbols, seed=1):
    src = _EX31.read_text()
    ns = {"__file__": str(_EX31), "__name__": "ex31_head"}
    exec(compile(src[: src.index("cm = ChannelModel.from_config(BASE)")], "ex31", "exec"), ns)
    b = ns["BASE"]
    rx = dataclasses.replace(b.rx, noise_rms=noise,
                             cdr=dataclasses.replace(b.rx.cdr, kp_shift=40, ki_shift=50),
                             ffe=dataclasses.replace(b.rx.ffe, adapt="none"),
                             dfe=dataclasses.replace(b.rx.dfe, adapt="none"))
    return dataclasses.replace(b, tx=dataclasses.replace(b.tx, clock=ClockConfig()), rx=rx,
                               sim=dataclasses.replace(b.sim, n_symbols=n_symbols, seed=seed))


@pytest.mark.needs_jit
def test_errors_per_first_error_behind_an_ffe():
    """Errors per first error (one after a correct symbol) in the time engine
    against ``dfe_ep_factor``: 4 mV, four seeds of 500k symbols (~480 first
    errors, 1.84 per first one). The chain without colour read 1.71; without
    an FFE that colours the noise the factor is that chain's, unchanged (the
    fingerprint's mixed-signal and FFE-less entries stay bit-identical).

    2026-10-30, the impulse trimmed by its ISI tail (ROADMAP P1 #42): 1.82
    per first one (971 / 533), this chain 1.75, the chain without colour
    1.56 on the same taps -- the bound sits between the two chains.
    2026-10-31, with p3 (ROADMAP P2 #37): this chain 1.79."""
    first = total = 0
    for seed in (1, 2, 3, 4):
        cfg = _frozen(0.004, 500_000, seed)
        r = run_time_link(cfg)
        dec, ref = np.asarray(r.extras["decisions"]), np.asarray(r.extras["reference"])
        m = min(dec.size, ref.size)
        bad = np.flatnonzero((ref[:m] >= 0) & (dec[:m] != ref[:m]))
        total += bad.size
        first += int((~np.isin(bad - 1, bad)).sum())
    ep = run_statistical(cfg, ffe_taps=r.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre).extras["dfe_ep_factor"]
    assert first > 400
    assert ep == pytest.approx(total / first, rel=0.06), (ep, total / first, first)
    assert ep > 1.65

