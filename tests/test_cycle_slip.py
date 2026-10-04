"""Whole-UI CDR slips are scored around, not read as BER 0.5 (ROADMAP 4c).

A loop that slips one UI keeps deciding correctly, one symbol off; scored at a
fixed alignment every later decision is at chance. ``score()`` now finds the
offset per segment (``find_slips``) and reports how many slips it crossed.
"""

import dataclasses

import numpy as np
import pytest

import halo_serdes.engine.timedomain as td
from halo_serdes.cdr.adc_kernel import _adc_rx_py, adc_rx
from halo_serdes.engine import run_time_link
from halo_serdes.engine.scoring import SLIP_WINDOW, find_slips
from halo_serdes_app.config_bridge import load_preset

needs_jit = pytest.mark.skipif(adc_rx is _adc_rx_py,
                               reason="engine-level ADC run; minutes through the pure-Python kernel")


def _stream(n, m, p_err, seed=0):
    rng = np.random.default_rng(seed)
    ref = rng.integers(0, m, n)
    dec = ref.copy()
    flip = rng.random(n) < p_err
    dec[flip] = (dec[flip] + 1) % m
    return ref, dec


@pytest.mark.parametrize("m", [2, 4])
@pytest.mark.parametrize("step", [+1, -1, +2])
def test_one_slip_is_found_where_it_happened(m, step):
    ref, dec = _stream(60_000, m, 1e-3)
    at = 25_000
    # +k: the receiver skipped k symbols; -k: it decided k of them twice
    dec = np.concatenate([dec[:at], dec[at + step:]] if step > 0
                         else [dec[:at], dec[at + step:at], dec[at:]])[:50_000]
    off = find_slips(dec, ref, 0)
    assert off is not None
    assert np.all(off[:at - 2] == 0) and np.all(off[at + 2:] == step)
    assert np.count_nonzero(np.diff(off)) == 1


def test_two_slips_accumulate():
    ref, dec = _stream(60_000, 2, 1e-3)
    dec = np.concatenate([dec[:10_000], dec[10_001:30_000], dec[30_001:]])
    off = find_slips(dec, ref, 0)
    assert off[5_000] == 0 and off[20_000] == 1 and off[40_000] == 2


def test_offset_present_from_the_start():
    ref, dec = _stream(60_000, 4, 1e-3)
    off = find_slips(dec[1:50_001], ref, 0)
    assert np.all(off == 1)


@pytest.mark.parametrize("p_err", [0.0, 1e-2, 0.2])
def test_an_aligned_link_never_switches(p_err):
    """A bad but aligned link sits at its own error rate under offset 0 and
    at chance under every other: nothing to take over."""
    ref, dec = _stream(60_000, 4, p_err)
    assert find_slips(dec, ref, 0) is None


def test_garbage_does_not_find_a_slip():
    rng = np.random.default_rng(3)
    ref, dec = rng.integers(0, 2, 60_000), rng.integers(0, 2, 60_000)
    assert find_slips(dec, ref, 0) is None


def test_short_runs_are_left_alone():
    ref, dec = _stream(SLIP_WINDOW, 2, 0.0)
    assert find_slips(dec[1:], ref, 0) is None


def _inject(monkeypatch, at, step):
    """+step UI on the receiver's sampling clock from symbol ``at`` on."""
    orig = td.rx_clock_offsets_samples

    def stepped(n, cfg, rng):
        o = orig(n, cfg, rng).copy()
        o[at:] += step * cfg.osr
        return o

    monkeypatch.setattr(td, "rx_clock_offsets_samples", stepped)


@pytest.mark.parametrize("preset", [
    "NRZ 28G analytic (COM/xtalk)",
    pytest.param("PAM4 224G ADC (TI mismatch)", marks=needs_jit),
])
@pytest.mark.parametrize("step", [+1, -1])
def test_injected_sampler_step_is_one_slip(monkeypatch, preset, step):
    """The ROADMAP 4c acceptance: one +-1 UI phase jump, BER back to the
    no-jump level, ``extras["cycle_slips"] == 1`` at the jump."""
    cfg = load_preset(preset)
    cfg = dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, n_symbols=60_000))
    base = run_time_link(cfg)
    assert base.extras["cycle_slips"] == 0
    at = 40_000
    _inject(monkeypatch, at, step)
    r = run_time_link(cfg)
    assert r.extras["cycle_slips"] == 1
    assert abs(r.extras["slip_at"][0] - (at - r.extras["warmup"])) <= 4
    # a few symbols around the jump may go wrong; nothing like chance
    assert r.ber.n_errors <= 2 * base.ber.n_errors + 20, (r.ber.ber, base.ber.ber)
    assert abs(r.slicer_snr_db - base.slicer_snr_db) < 0.2
