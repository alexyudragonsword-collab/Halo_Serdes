"""analysis/reconstruct: front-end waveform/eye rebuild for scopes."""

import numpy as np

from halo_serdes.analysis.reconstruct import front_end_eye, front_end_waveform
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import ChannelConfig, CtleConfig, RxConfig, SimConfig


def _cfg(**over):
    base = dict(
        modulation="nrz", symbol_rate=16e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.25, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=4096),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0)),
        sim=SimConfig(n_symbols=3000, seed=1, pattern="prbs7"))
    base.update(over)
    return LinkConfig(**base)


def test_front_end_eye_shape_and_phase():
    cfg = _cfg()
    wave, phase = front_end_waveform(cfg)
    assert wave.y.size > 0 and 0 <= phase < cfg.osr
    eye = front_end_eye(cfg, n_traces=800)
    assert eye.shape == (800, 2 * cfg.osr)
    assert np.ptp(eye) > 0


def test_ctle_toggle_changes_eye():
    cfg = _cfg()
    with_ctle = front_end_eye(cfg, include_ctle=True, with_noise=False)
    no_ctle = front_end_eye(cfg, include_ctle=False, with_noise=False)
    # CTLE peaking reshapes the analog eye
    assert not np.allclose(with_ctle, no_ctle)
