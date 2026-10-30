"""tools/external_check.py without serdespy: its links are valid configs, and
the whole-impulse channel it compares against keeps what the trim drops.

The comparison itself needs an unpacked serdespy 1.0 and runs by hand
(``cairn/external-validation.md``); this keeps the tool from rotting between
runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("skrf")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import external_check as ec  # noqa: E402

from halo_serdes.channel import ChannelModel  # noqa: E402


@pytest.mark.parametrize("case", ec.CASES, ids=lambda c: c.name)
def test_every_case_is_a_valid_link(case):
    cfg = ec._config(case, 1000)
    assert cfg.modulation == case.modulation and cfg.rx.noise_rms == case.noise
    assert cfg.rx.ctle.enable is (case.ctle_db is not None)
    assert Path(cfg.channel.file).exists()


def test_the_whole_impulse_keeps_the_tail_the_trim_drops():
    cfg = ec._config(ec.CASES[2], 1000)
    trimmed = ChannelModel.from_config(cfg).response_set(cfg.dt).h.y
    whole = ec._untrimmed(cfg).response_set(cfg.dt).h.y
    assert trimmed.size / cfg.osr < 20 < 1000 < whole.size / cfg.osr   # 13.9 / 2047.5 UI
    assert np.allclose(ec._untrimmed(cfg).H, ChannelModel.from_config(cfg).H)
