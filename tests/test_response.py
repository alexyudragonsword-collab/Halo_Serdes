"""Frequency<->time conversion identities and analytic references."""

import numpy as np
import pytest

from halo_serdes.channel.response import (
    freq2impulse,
    impulse2freq,
    pulse_from_impulse,
    trim_impulse,
    zero_pad_to_dt,
)
from halo_serdes.core.waveform import ResponseSet, Waveform, cascade


def test_freq2impulse_roundtrip():
    rng = np.random.default_rng(1)
    n = 513
    f = np.linspace(0, 50e9, n)
    H = rng.normal(size=n) + 1j * rng.normal(size=n)
    H[0] = H[0].real  # DC must be real
    H[-1] = H[-1].real  # Nyquist bin must be real for exact round-trip
    h = freq2impulse(H, f)
    f2, H2 = impulse2freq(h)
    assert np.allclose(f2, f, rtol=1e-9)
    assert np.allclose(H2, H, rtol=1e-9, atol=1e-12)


def test_freq2impulse_dc_gain():
    # sum of per-sample-weight impulse equals DC gain
    f = np.linspace(0, 40e9, 401)
    H = np.exp(-f / 20e9) * np.exp(-1j * 2 * np.pi * f * 50e-12)
    h = freq2impulse(H, f)
    assert np.isclose(h.y.sum(), H[0].real, rtol=1e-6)


def test_single_pole_impulse_matches_analytic():
    """H(f) = 1/(1+jf/fp) -> h(t) = (1/tau) exp(-t/tau) (sampled, per-sample weight)."""
    fp = 5e9
    tau = 1 / (2 * np.pi * fp)
    f = np.linspace(0, 400e9, 8001)  # wide grid so truncation error is small
    H = 1.0 / (1.0 + 1j * f / fp)
    h = freq2impulse(H, f)
    t = h.t
    analytic = (1.0 / tau) * np.exp(-t / tau) * h.dt  # per-sample weight
    # skip the first samples: band truncation at 80*fp smears the t=0 edge
    # (Gibbs); away from the edge the residual ripple is ~1%.
    n = int(5 * tau / h.dt)
    err = np.abs(h.y[20:n] - analytic[20:n]).max() / analytic[20]
    assert err < 0.02
    # DC gain identity is exact regardless of truncation
    assert np.isclose(h.y.sum(), 1.0, rtol=1e-9)


def test_zero_pad_hits_requested_dt():
    f = np.linspace(0, 40e9, 401)
    H = np.ones(401, dtype=complex)
    dt_req = 1e-12
    H_zp, f_zp = zero_pad_to_dt(H, f, dt_req)
    h = freq2impulse(H_zp, f_zp)
    assert h.dt <= dt_req * (1 + 1e-9)
    assert np.isclose(f_zp[1], f[1])  # df preserved


def test_zero_pad_rejects_decimation():
    f = np.linspace(0, 100e9, 1001)
    with pytest.raises(ValueError):
        zero_pad_to_dt(np.ones(1001, dtype=complex), f, 1e-9)


def test_trim_impulse_keeps_peak():
    dt = 1e-12
    y = np.zeros(4000)
    center = 2000
    y[center - 50:center + 200] = np.exp(-np.arange(-50, 200) ** 2 / 800.0)
    h = Waveform(y, dt)
    trimmed, start = trim_impulse(h, keep_energy=0.999)
    assert trimmed.y.size < 1000
    assert start < center < start + trimmed.y.size
    assert np.isclose(trimmed.y.max(), y.max())


def _slow_tail_impulse(osr=16, n_ui=4096, tail=0.2, tau_ui=150.0):
    """A sharp main lobe and a slow exponential tail -- the shape of a lossy
    backplane's dielectric / skin tail, small in slope and not in ISI."""
    t = np.arange(n_ui * osr)
    y = np.exp(-0.5 * ((t - 100 * osr) / (0.4 * osr)) ** 2) / (0.4 * osr * np.sqrt(2 * np.pi))
    late = t > 100 * osr
    y[late] += tail / (tau_ui * osr) * np.exp(-(t[late] - 100 * osr) / (tau_ui * osr))
    return Waveform(y, 1e-12)


def _cursor_tail_rms(y, osr, start, stop):
    """RMS of the postcursors the cut at ``stop`` leaves out, relative to
    the main cursor (pulse sampled from the peak)."""
    s = np.cumsum(y)
    p = int(np.argmax(np.abs(y)))
    idx = np.arange(p + osr // 2, y.size // 2 + p, osr)
    c = np.diff(s[idx])
    main = s[p + osr // 2] - s[p + osr // 2 - osr]
    first_out = np.searchsorted(idx, stop)          # cursor k spans idx[k]..idx[k+1]
    return float(np.sqrt(np.sum(c[first_out:] ** 2)) / main)


def test_trim_keeps_a_slow_isi_tail_when_it_knows_the_ui():
    """ROADMAP P1 #42: the difference-energy criterion cuts a slow tail at
    a few UI; with the UI the cut moves out until what it leaves out is
    under the tolerance."""
    osr = 16
    h = _slow_tail_impulse(osr)
    old, start = trim_impulse(h, keep_energy=0.9999)
    new, start_new = trim_impulse(h, keep_energy=0.9999, osr=osr)
    assert start_new == start                                  # the front is not moved
    assert old.y.size / osr < 20 < 200 < new.y.size / osr
    assert _cursor_tail_rms(h.y, osr, start, start + old.y.size) > 1e-2
    assert _cursor_tail_rms(h.y, osr, start, start + new.y.size) <= 1e-3


def test_the_isi_trim_ignores_the_wrapped_far_end():
    """The second half of a periodic record is the pre-peak side wrapped
    round: ringing there must not hold the cut open to the end."""
    osr = 16
    h = _slow_tail_impulse(osr, tail=0.0)
    y = h.y.copy()
    # a slow pre-peak tail, wrapped to the far end: too gentle for the
    # difference criterion, ISI-sized if it were a postcursor tail
    ramp = 1e-5 * np.linspace(0.0, 1.0, 3008)
    y[-3008:] += ramp
    per_ui = ramp.reshape(-1, osr).sum(axis=1)               # its "cursors"
    assert np.sqrt(np.sum(per_ui ** 2)) > 1e-3 * 0.79        # main cursor 0.79
    cut, _ = trim_impulse(Waveform(y, h.dt), keep_energy=0.9999, osr=osr)
    assert cut.y.size < y.size // 4


def test_pulse_response_of_ideal_channel():
    """Identity channel: pulse response is a 1-UI rectangle."""
    osr = 8
    h = Waveform(np.concatenate([[1.0], np.zeros(63)]), 1e-12)
    p = pulse_from_impulse(h, osr)
    assert np.allclose(p.y[:osr], 1.0)
    assert np.allclose(p.y[osr:], 0.0)


def test_response_set_step_pulse_consistency():
    rng = np.random.default_rng(2)
    h = Waveform(rng.normal(size=256) * np.exp(-np.arange(256) / 40.0), 1e-12)
    rs = ResponseSet(h)
    osr = 16
    # p[k] = s[k] - s[k-osr]
    s = rs.s.y
    p = rs.p(osr).y
    assert np.allclose(p[osr:], s[osr:] - s[:-osr])
    # pulse via convolution with ones == step difference
    p2 = pulse_from_impulse(h, osr)
    assert np.allclose(p2.y, p, atol=1e-12)


def test_cascade_matches_manual_convolution():
    rng = np.random.default_rng(3)
    a = Waveform(rng.normal(size=64), 1e-12)
    b = Waveform(rng.normal(size=32), 1e-12)
    c = cascade(ResponseSet(a), ResponseSet(b))
    assert np.allclose(c.h.y, np.convolve(a.y, b.y))
