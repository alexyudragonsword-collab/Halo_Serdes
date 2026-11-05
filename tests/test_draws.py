"""One link, several draws (analysis.draws, the "seeds" study)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from halo_serdes.analysis import seed_spread
from halo_serdes_app import config_bridge as cb


@pytest.fixture(scope="module")
def mismatch():
    return cb.load_preset("PAM4 224G ADC (TI mismatch)")


def test_lane_mismatch_spreads_the_seeds_and_calibration_closes_it(mismatch):
    """Each seed draws its own lane offsets, gains and skews: on the mismatch
    preset eight seeds spread over one and a half decades or more, the
    typical one well below the pooled count (a few bad chips carry the mean;
    2.7x on these eight, 8x over 128 seeds). Zero the mismatch and the same
    eight seeds count nothing."""
    calls = []
    sp = seed_spread(mismatch, range(1, 9), n_symbols=30_000, checkpoint=lambda: calls.append(1))
    assert len(calls) == 9                      # one per seed and the statistical run
    assert sp.seeds.tolist() == list(range(1, 9))
    assert sp.pooled_ber == pytest.approx(sp.n_errors.sum() / sp.n_checked.sum())
    assert sp.spread_decades is not None and sp.spread_decades > 1.5
    assert sp.median_ber < sp.pooled_ber / 2
    assert sp.stat_ber is not None and sp.stat_ber > 0

    ideal = dataclasses.replace(mismatch, rx=dataclasses.replace(
        mismatch.rx, adc=dataclasses.replace(mismatch.rx.adc, calibrated=True)))
    flat = seed_spread(ideal, range(1, 9), n_symbols=30_000, with_stat=False)
    assert flat.n_errors.sum() == 0 and flat.stat_ber is None and flat.spread_decades is None


def test_the_seeds_study_draws_pooled_and_statistical_lines_across_the_seeds(mismatch):
    from halo_serdes_app import studies

    class Rec:                                  # the study reads only cfg and id
        cfg = dataclasses.replace(mismatch, sim=dataclasses.replace(mismatch.sim,
                                                                    n_symbols=20_000))
        id = "test-seeds"

    d = studies.seeds_study(Rec)
    assert d["seed"].size == studies.SEEDS_N
    assert np.all(d["pooled"] == d["pooled"][0]) and np.all(d["stat"] == d["stat"][0])
    assert d["symbols"][0] == 20_000            # the run's own count when below the cap
    for spec in studies.STUDY_PLOTS["seeds"]:
        assert all(k in d for k in [spec["x"], *spec["y"]])


def test_seed_spread_needs_a_seed(mismatch):
    with pytest.raises(ValueError, match="at least one seed"):
        seed_spread(mismatch, [], n_symbols=1000)
