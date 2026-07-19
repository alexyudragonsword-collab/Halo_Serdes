"""Real IBIS-AMI execution: a compiled AMI shared object driven over the C ABI
(AmiCModel) must match the native FIR reference bit-for-bit. Skipped when no C
compiler is available."""

import shutil

import numpy as np
import pytest

from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    ChannelConfig, CtleConfig, DfeConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.engine import run_time_link
from halo_serdes.io import AmiCModel, NativeFirAmi, build_reference_ami, load_ami_model

pytestmark = pytest.mark.skipif(
    shutil.which("cc") is None and shutil.which("gcc") is None,
    reason="no C compiler (cc/gcc) to build the reference AMI model")

TAPS = [-0.08, 0.85, -0.05]
N_PRE = 1
DT = 1 / (28e9 * 16)
UI = 1 / 28e9


@pytest.fixture(scope="module")
def so_path(tmp_path_factory):
    out = tmp_path_factory.mktemp("ami_c")
    so, ami = build_reference_ami(out_dir=str(out))
    return so


def test_build_produces_loadable_shared_object(so_path):
    m = load_ami_model(so_file=so_path, taps=TAPS, n_pre=N_PRE)
    assert isinstance(m, AmiCModel)


def test_init_flow_bit_exact_vs_native(so_path):
    c = AmiCModel(so_path, taps=TAPS, n_pre=N_PRE)
    n = NativeFirAmi(TAPS, n_pre=N_PRE, sample_spaced=False)
    h = np.zeros(2000)
    h[500] = 1.0
    hc = c.init(h.copy(), DT, UI)
    hn = n.init(h.copy(), DT, UI)
    assert np.array_equal(hc, hn)              # LTI Init: exact
    assert "halo_serdes reference FIR AMI" in c.messages   # msg came from the .so
    c.close()


def test_getwave_flow_matches_native(so_path):
    c = AmiCModel(so_path, taps=TAPS, n_pre=N_PRE, has_getwave=True)
    n = NativeFirAmi(TAPS, n_pre=N_PRE, sample_spaced=False)
    rng = np.random.default_rng(0)
    wave = rng.standard_normal(4000)
    yc, clk = c.get_wave(wave.copy(), DT, UI)
    yn, _ = n.get_wave(wave.copy(), DT, UI)
    assert np.max(np.abs(yc - yn)) < 1e-12     # streaming FIR: machine epsilon
    assert clk is None                         # a FIR emits no recovered clock
    c.close()


def _cfg():
    return LinkConfig(
        modulation="nrz", symbol_rate=28e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=0.28, rdc=5.0,
                              r_skin=2e-3, loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(1.0,), fir_n_pre=0),  # EQ via the AMI model
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=True, peak_db=7.0),
                    dfe=DfeConfig(n_taps=2), noise_rms=0.004),
        sim=SimConfig(n_symbols=20000, seed=5, pattern="prbs13"))


@pytest.mark.parametrize("getwave", [False, True])
def test_c_model_matches_native_through_engine(so_path, getwave):
    """Same flow -> the compiled .so and the native reference give an identical
    link result; the Tx EQ also improves SNR over no EQ."""
    cfg = _cfg()
    ch = ChannelModel.from_config(cfg)
    c = AmiCModel(so_path, taps=TAPS, n_pre=N_PRE, has_getwave=getwave)
    n = NativeFirAmi(TAPS, n_pre=N_PRE, sample_spaced=False)
    n.has_getwave = getwave
    rc = run_time_link(cfg, channel=ch, tx_ami=c)
    rn = run_time_link(cfg, channel=ch, tx_ami=n)
    assert rc.slicer_snr_db == pytest.approx(rn.slicer_snr_db, abs=1e-9)
    base = run_time_link(cfg, channel=ch)
    assert rc.slicer_snr_db > base.slicer_snr_db
    c.close()


def test_close_is_idempotent(so_path):
    c = AmiCModel(so_path, taps=TAPS, n_pre=N_PRE)
    c.init(np.array([1.0, 0.0, 0.0]), DT, UI)
    c.close()
    c.close()                                  # second close must not crash
