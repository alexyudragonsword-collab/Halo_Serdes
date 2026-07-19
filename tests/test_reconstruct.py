"""analysis/reconstruct: front-end waveform/eye rebuild for scopes."""

import numpy as np

from halo_serdes.analysis.reconstruct import (
    front_end_eye, front_end_waveform, post_ffe_eye,
)
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    AdcConfig, ChannelConfig, CtleConfig, FfeConfig, RxConfig, SimConfig,
)


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


def test_post_ffe_eye_reconstruction():
    # ADC/DSP config; reconstruct the digital-FFE-output eye from baud taps
    cfg = _cfg(modulation="pam4", symbol_rate=53.125e9, osr=16,
               rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                           adc=AdcConfig(n_bits=8, n_lanes=8, enob=6.5),
                           ffe=FfeConfig(n_pre=3, n_post=8)),
               sim=SimConfig(n_symbols=3000, seed=2, pattern="prbs13q"))
    taps = np.zeros(cfg.rx.ffe.n_pre + cfg.rx.ffe.n_post + 1)
    taps[cfg.rx.ffe.n_pre] = 1.0            # a trivial main-cursor-only FFE
    eye = post_ffe_eye(cfg, taps, n_traces=600, with_noise=False)
    assert eye.shape == (600, 2 * cfg.osr)
    assert np.ptp(eye) > 0
    # empty taps -> falls back to the raw front-end eye (no crash)
    assert post_ffe_eye(cfg, np.array([]), n_traces=100).shape == (100, 2 * cfg.osr)
