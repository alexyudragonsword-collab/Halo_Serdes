"""tools/external_check.py without serdespy: its links are valid configs, and
the whole-impulse channel it compares against is the same channel uncut.

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


def test_the_whole_impulse_is_the_same_channel_uncut():
    cfg = ec._config(ec.CASES[2], 1000)
    model = ChannelModel.from_config(cfg)
    trimmed = model.response_set(cfg.dt).h.y
    whole = ec._untrimmed(cfg).response_set(cfg.dt).h.y
    # the ISI-tail trim keeps 505 UI of Whisper at 16 GBd (the difference
    # criterion alone kept 13.9, ROADMAP P1 #42); uncut is the whole record
    assert 300 < trimmed.size / cfg.osr < 1000 < 2000 < whole.size / cfg.osr
    assert np.allclose(ec._untrimmed(cfg).H, model.H)
    assert ec._untrimmed(cfg).ui == model.ui == 1.0 / cfg.symbol_rate
