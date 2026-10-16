"""analysis/format_compare: the arithmetic of a format comparison (examples/46)."""

import pytest

from halo_serdes.analysis.format_compare import (
    FEC_OVERHEAD, baud_for, dac_bits_needed, half_gap_penalty_db, noise_tolerance, pick_receiver,
)


def test_baud_for_448g_under_kp4_and_the_concatenated_code():
    assert baud_for(448e9, "pam4") == pytest.approx(237.07e9, rel=1e-4)
    assert baud_for(448e9, "pam6") == pytest.approx(189.66e9, rel=1e-4)
    assert baud_for(448e9, "pam8") == pytest.approx(158.05e9, rel=1e-4)
    assert baud_for(448e9, "pam4", "kp4+bch") == pytest.approx(281.18e9, rel=1e-4)
    assert FEC_OVERHEAD["kp4+bch"] / FEC_OVERHEAD["kp4"] == pytest.approx(255 / 215)
    assert baud_for(224e9, "pam4") == pytest.approx(118.5e9, rel=1e-3)   # the 224G presets' line rate


def test_half_gap_penalty_against_pam4():
    assert half_gap_penalty_db("pam4") == 0.0
    assert half_gap_penalty_db("pam6") == pytest.approx(4.44, abs=0.01)
    assert half_gap_penalty_db("pam8") == pytest.approx(7.36, abs=0.01)
    assert half_gap_penalty_db("nrz") == pytest.approx(-9.54, abs=0.01)


def test_pick_receiver_prefers_reach_then_power():
    cells = [("a", 20.0, 3.0), ("b", 20.05, 4.0), ("c", 19.5, 1.0), ("d", None, 0.5)]
    pick = pick_receiver(cells, reach=lambda c: c[1], pj=lambda c: c[2], tol_db=0.1)
    assert pick[0] == "a"                      # within 0.1 dB of b, cheaper
    pick = pick_receiver(cells, reach=lambda c: c[1], pj=lambda c: c[2], tol_db=0.0)
    assert pick[0] == "b"
    assert pick_receiver([("d", None, 0.5)], reach=lambda c: c[1], pj=lambda c: c[2]) is None


def test_dac_bits_needed_is_the_fewest_within_tolerance():
    assert dac_bits_needed({None: 20.0, 8: 19.95, 7: 19.92, 6: 19.6}) == 7
    assert dac_bits_needed({None: 20.0, 8: 19.95, 7: 19.92, 6: 19.6}, tol_db=0.5) == 6
    assert dac_bits_needed({None: 20.0, 8: 19.0, 7: None}) is None
    assert dac_bits_needed({None: None, 8: 19.0}) is None


def test_noise_tolerance_is_the_largest_within_the_drop():
    assert noise_tolerance({0.0: 20.0, 0.01: 19.8, 0.02: 19.4, 0.03: None}) == 0.01
    assert noise_tolerance({0.0: 20.0, 0.01: 19.0}) == 0.0        # none tolerated
    assert noise_tolerance({0.0: None}) is None
