"""MM phase-detector input: what "auto" resolves to, and the two failure modes
it steers around (raw ADC samples lock PAM4 off the peak; equalised samples
leave a light-ISI NRZ link without a timing gradient)."""

import numpy as np
import pytest

from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig,
                                       FfeConfig, RxConfig, SimConfig, TxConfig)
from halo_serdes.engine import run_time_link


@pytest.mark.parametrize("mod, pd, expected", [
    ("pam4", "auto", "ffe"), ("nrz", "auto", "adc"),
    ("pam4", "adc", "adc"), ("nrz", "ffe", "ffe"),
])
def test_auto_resolves_by_modulation_and_explicit_wins(mod, pd, expected):
    cfg = LinkConfig(modulation=mod, rx=RxConfig(arch="adc_dsp", cdr=CdrConfig(pd_input=pd)))
    assert cfg.mm_pd_input == expected


def test_default_is_auto():
    assert CdrConfig().pd_input == "auto"


def _link(mod, length, pd, peak_db=3.0):
    fb = 53.125e9 if mod == "pam4" else 26.5625e9
    rx = RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=peak_db),
                  adc=AdcConfig(n_bits=8, n_lanes=16, fullscale=0.3),
                  ffe=FfeConfig(n_pre=3, n_post=10, adapt="lms", mu=3e-5), dfe=DfeConfig(n_taps=0),
                  cdr=CdrConfig(kind="mueller_muller", kp_shift=6, ki_shift=14, pd_input=pd),
                  noise_rms=1e-3)
    return LinkConfig(modulation=mod, symbol_rate=fb, osr=16,
                      channel=ChannelConfig(kind="analytic", length_m=length, rdc=5.0, r_skin=2e-3,
                                            loss_tangent=0.012, n_freq=4096),
                      tx=TxConfig(swing=1.0), rx=rx,
                      sim=SimConfig(n_symbols=120_000, seed=1,
                                    pattern="prbs13q" if mod == "pam4" else "prbs13"))


def _wander_ui(cfg, res):
    ph = np.asarray(res.extras["phase_track"])
    moved = (ph - ph[0] - np.arange(ph.size) * cfg.osr) / cfg.osr
    return float(np.ptp(moved[res.extras.get("settle", 0):]))


def test_pam4_raw_adc_samples_lock_off_the_peak():
    """0.25 m PAM4: on raw samples MM settles where h(-1) = h(+1) of the
    unequalised pulse, ~10 dB of slicer SNR away from the equalised optimum."""
    snr = {pd: run_time_link(_link("pam4", 0.25, pd, 6.0)).slicer_snr_db for pd in ("adc", "ffe")}
    assert snr["ffe"] > snr["adc"] + 6.0, snr
    assert run_time_link(_link("pam4", 0.25, "auto", 6.0)).slicer_snr_db == snr["ffe"]


def test_nrz_equalised_samples_leave_mm_without_a_gradient():
    """0.1 m NRZ: the FFE zeroes h(+-1) over a wide range of phases, so MM on
    its output random-walks; on raw samples it holds still."""
    wander = {}
    for pd in ("adc", "ffe"):
        cfg = _link("nrz", 0.1, pd)
        wander[pd] = _wander_ui(cfg, run_time_link(cfg))
    assert wander["adc"] < 0.05 and wander["ffe"] > 0.2, wander


def test_statistical_engine_reads_where_mm_on_raw_samples_locks():
    """The statistical engine reports the ADC's BER where the receiver
    samples. On raw samples MM locks where the pre-FFE pulse has h(-1) =
    h(+1); on equalised samples the loop stays on the pre-FFE peak it starts
    from. The difference of the two reported lock phases is therefore the
    raw-sample loop's offset from the peak -- and the time engine's loop,
    started on the peak, has to settle there."""
    from halo_serdes.engine.statistical import run_statistical

    cfg = _link("nrz", 0.25, "adc")
    res = run_time_link(cfg)
    ph = np.asarray(res.extras["phase_track"])
    moved = ph - ph[0] - np.arange(ph.size) * cfg.osr
    settled = float(moved[ph.size // 2:].mean())                 # samples from the start
    kw = dict(ffe_taps=res.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    lock_raw = run_statistical(cfg, **kw).extras["lock_phase_ui"]
    lock_eq = run_statistical(_link("nrz", 0.25, "ffe"), **kw).extras["lock_phase_ui"]
    predicted = (lock_raw - lock_eq) * cfg.osr
    assert predicted < -0.3, predicted                             # off the peak
    assert abs(predicted - settled) < 0.25, (predicted, settled)
