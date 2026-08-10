"""Overlap-save chunked FFT filtering (engine/lti.py).

The chunked branch only engages for multi-million-sample waveforms — exactly
the 224G long runs the test suite never simulates — so it was previously
executed by nothing. These tests force the branch with a small ``chunk`` and
pin it to full-array convolution, which is the contract the whole time engine
rests on.
"""

import numpy as np
import pytest

from halo_serdes.engine.lti import fft_filter


@pytest.mark.parametrize("nx,nh,chunk", [
    (5_000, 64, 1024),          # many blocks, short kernel
    (50_000, 257, 4096),
    (200_000, 1000, 8192),
    (300_000, 4001, 32768),     # kernel a large fraction of the block
])
def test_chunked_matches_full_convolution(nx, nh, chunk):
    rng = np.random.default_rng(0)
    x = rng.standard_normal(nx)
    h = rng.standard_normal(nh)
    ref = np.convolve(x, h)[:nx]
    got = fft_filter(x, h, chunk=chunk)
    assert got.shape == (nx,)
    assert np.max(np.abs(got - ref)) < 1e-9 * max(np.max(np.abs(ref)), 1.0)


def test_chunk_size_does_not_change_the_result():
    """The block size is an implementation detail, not a model parameter."""
    rng = np.random.default_rng(1)
    x = rng.standard_normal(120_000)
    h = rng.standard_normal(513)
    base = fft_filter(x, h, chunk=1 << 20)          # single-FFT fast path
    for chunk in (2048, 8192, 65536):
        got = fft_filter(x, h, chunk=chunk)         # chunked path
        assert np.max(np.abs(got - base)) < 1e-9


def test_impulse_kernel_is_identity_across_blocks():
    rng = np.random.default_rng(2)
    x = rng.standard_normal(40_000)
    h = np.zeros(128)
    h[0] = 1.0
    assert np.allclose(fft_filter(x, h, chunk=1024), x, atol=1e-12)


def test_delay_kernel_shifts_across_block_boundaries():
    """A pure delay exercises the overlap carry between consecutive blocks."""
    rng = np.random.default_rng(3)
    x = rng.standard_normal(20_000)
    d = 700                                          # delay crosses blocks
    h = np.zeros(d + 1)
    h[d] = 1.0
    got = fft_filter(x, h, chunk=512)
    assert np.allclose(got[d:], x[: x.size - d], atol=1e-12)
    assert np.allclose(got[:d], 0.0, atol=1e-12)
