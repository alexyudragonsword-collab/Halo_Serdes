"""tools/pr_target_length.py, the study behind ROADMAP #8's "longer PR
target" boundary: its target solve is the library's for the lengths the
library has, its chunked Viterbi is the library's Viterbi, and on example
38's link a three-cursor target beats the delta target as example 38 says."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from halo_serdes.dsp.ffe import mmse_pr_target
from halo_serdes.dsp.mlsd import viterbi_mlsd

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("pr_target_length",
                                                  REPO / "tools" / "pr_target_length.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def link(tool):
    return tool.slicer_cursors(0.24)               # ~36 dB, inside example 38's sweep


def test_target_matches_library_up_to_three_cursors(tool, link):
    c, c_pre, noise, _ = link
    s2 = (1.75 * noise) ** 2
    assert tuple(tool.monic_target(c, c_pre, s2, 1)) == (1.0,)
    for k in (2, 3):
        lib = mmse_pr_target(c, c_pre, tool.N_PRE + 1 + tool.N_POST, tool.N_PRE,
                             noise_var=s2, symbol_power=tool.SYMBOL_POWER, n_target=k)
        np.testing.assert_allclose(tool.monic_target(c, c_pre, s2, k), lib, rtol=1e-9)
    # four cursors: the library clips only up to three; the tail keeps decaying
    t4 = tool.monic_target(c, c_pre, s2, 4)
    assert t4.size == 4 and t4[1] > t4[2] > t4[3] > 0.0


def test_chunked_viterbi_is_the_library_viterbi(tool):
    rng = np.random.default_rng(1)
    taps = np.array([1.0, 1.1, 0.45])             # 16 states: cheap under HALO_NO_JIT
    sym = rng.integers(0, 4, size=4000)
    z = np.convolve(tool.LEVELS[sym], taps)[:sym.size] + 0.08 * rng.normal(size=sym.size)
    whole = viterbi_mlsd(z, tool.LEVELS, taps)
    np.testing.assert_array_equal(tool.viterbi_chunked(z, taps, chunk=700, pad=150), whole)


def test_three_cursor_target_beats_delta(tool, link):
    # memory 0 behind the target keeps it to 16 states (HALO_NO_JIT runs this too)
    ber = {k: tool.link_ber(link, k, 0, 1.75, 20_000, seed=7) for k in (1, 3)}
    assert ber[3] < ber[1] / 10, ber
