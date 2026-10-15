"""Power model (analysis/power.py): the closed forms it is built from, the
structure it reads off the config, and the fairness between architectures."""

import dataclasses

import pytest

from halo_serdes.analysis.power import (
    PowerCoefficients,
    adc_power,
    add_energy,
    mult_energy,
    power_report,
    trellis_states,
)
from halo_serdes.config import LinkConfig, PrConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, DfeConfig, FfeConfig, MlsdConfig, OpticalConfig, RxConfig,
    TopologyConfig, TxConfig,
)

K = PowerCoefficients()


def adc_rx(enob=6.0, n_pre=4, n_post=10, n_dfe=1, mlsd=None, memory=2):
    return RxConfig(arch="adc_dsp", adc=AdcConfig(n_bits=8, enob=enob),
                    ffe=FfeConfig(n_pre=n_pre, n_post=n_post), dfe=DfeConfig(n_taps=n_dfe),
                    mlsd=MlsdConfig(kind=mlsd, memory=memory) if mlsd else MlsdConfig(),
                    cdr=CdrConfig(kind="mueller_muller"))


def ms_rx(n_dfe=3, tap1="direct", adapt="sign_sign", cdr="bang_bang"):
    return RxConfig(arch="mixed_signal", dfe=DfeConfig(n_taps=n_dfe, adapt=adapt, tap1_mode=tap1),
                    cdr=CdrConfig(kind=cdr))


def link(rx, baud=56e9, **kw):
    return LinkConfig(modulation="pam4", symbol_rate=baud, rx=rx, **kw)


# ------------------------------------------------------------- closed forms

def test_walden_reproduces_a_survey_row():
    # ISSCC 2024, 5 nm, 200 GS/s: 400 mW at 5.70 ENOB, FOM_W 38.4 fJ (Murmann survey)
    k = dataclasses.replace(K, adc_fom_walden_j=38.4e-15)
    assert adc_power(200e9, 5.70, k) == pytest.approx(0.400, rel=0.01)


def test_schreier_reproduces_the_same_row():
    # same row, FOM_S 150.1 dB
    k = dataclasses.replace(K, adc_fom="schreier", adc_fom_schreier_db=150.1)
    assert adc_power(200e9, 5.70, k) == pytest.approx(0.400, rel=0.02)


def test_adc_power_per_bit_walden_2x_schreier_4x():
    w = adc_power(100e9, 7.0, K) / adc_power(100e9, 6.0, K)
    ks = dataclasses.replace(K, adc_fom="schreier")
    s = adc_power(100e9, 7.0, ks) / adc_power(100e9, 6.0, ks)
    assert w == pytest.approx(2.0)
    assert s == pytest.approx(10 ** 0.602, rel=1e-6)          # 6.02 dB per bit


def test_digital_primitives_scale_with_width():
    assert mult_energy(32, 32, K) == pytest.approx(3.1e-12 * K.node_scale)
    assert add_energy(32, K) == pytest.approx(0.1e-12 * K.node_scale)
    assert mult_energy(16, 8, K) == pytest.approx(mult_energy(32, 32, K) * 128 / 1024)
    assert add_energy(8, K) == pytest.approx(add_energy(32, K) / 4)
    k3 = dataclasses.replace(K, digital_overhead=3.0)
    assert add_energy(12, k3) == pytest.approx(3 * add_energy(12, K))


def test_coefficients_validate():
    with pytest.raises(ValueError, match="adc_fom"):
        PowerCoefficients(adc_fom="murmann")
    with pytest.raises(ValueError, match="node_scale"):
        PowerCoefficients(node_scale=-1.0)


# ------------------------------------------------------------- structure

def test_report_sums_and_per_bit():
    r = power_report(link(adc_rx()))
    assert r.total_w == pytest.approx(sum(b.watts for b in r.blocks))
    assert r.pj_per_bit == pytest.approx(r.total_w / (56e9 * 2) * 1e12)
    assert r.watts("host.rx") + r.watts("host.tx") == pytest.approx(r.total_w)
    assert "total" in r.table()


def test_per_bit_energy_does_not_depend_on_rate():
    a = power_report(link(adc_rx(), baud=28e9)).pj_per_bit
    b = power_report(link(adc_rx(), baud=112e9)).pj_per_bit
    assert a == pytest.approx(b)


def test_ffe_power_is_linear_in_taps():
    p = [power_report(link(adc_rx(n_post=n))).watts("host.rx.ffe") for n in (8, 9, 10, 11)]
    steps = [b - a for a, b in zip(p, p[1:])]
    # the accumulator widens by a bit past 16 taps; below that, equal steps
    assert steps[0] == pytest.approx(steps[1], rel=1e-9)
    assert all(s > 0 for s in steps)


@pytest.mark.parametrize("target, memory, states", [
    ((1.0,), 1, 4), ((1.0,), 2, 16), ((1.0, 0.5), 2, 64),
    ((1.0, 0.5, 0.0), 2, 256), ((1.0, 0.5, 0.0, 0.0), 1, 256), ((1.0, 0.5, 0.0, 0.0), 2, 1024),
])
def test_trellis_states_follow_the_engine(target, memory, states):
    cfg = link(adc_rx(mlsd="viterbi", memory=memory), pr=PrConfig(target=target))
    assert trellis_states(cfg) == states
    assert f"{states} states" in {b.name: b.note for b in power_report(cfg).blocks}["host.rx.mlsd"]


def test_trellis_states_nrz_and_no_detector():
    assert trellis_states(LinkConfig(modulation="nrz", rx=adc_rx(mlsd="viterbi", memory=3))) == 8
    assert trellis_states(LinkConfig(modulation="pam6", rx=adc_rx(mlsd="viterbi", memory=2))) == 36
    assert trellis_states(LinkConfig(modulation="pam8", rx=adc_rx(mlsd="viterbi", memory=2))) == 64
    assert trellis_states(link(adc_rx())) == 0
    assert trellis_states(link(adc_rx(mlsd="sliding"))) == 0


def test_viterbi_power_grows_about_n_per_controlled_cursor():
    w = [power_report(link(adc_rx(mlsd="viterbi"), pr=PrConfig(target=t))).watts("host.rx.mlsd")
         for t in ((1.0,), (1.0, 0.5), (1.0, 0.5, 0.0))]
    # the metric adds are exactly x4; the per-cursor multiplies (one more each) keep
    # the ratio under, less so as the trellis grows
    assert 3.7 < w[1] / w[0] < 4.0
    assert 3.9 < w[2] / w[1] < 4.0


def test_sliding_detector_is_priced_by_its_window():
    a = power_report(link(adc_rx(mlsd="sliding"))).watts("host.rx.mlsd")
    rx8 = dataclasses.replace(adc_rx(mlsd="sliding"), mlsd=MlsdConfig(kind="sliding", seq_len=8))
    b = power_report(link(rx8)).watts("host.rx.mlsd")
    assert b == pytest.approx(2 * a)


def test_enob_unset_costs_n_bits_and_says_so():
    r = power_report(link(adc_rx(enob=None)))
    adc = {b.name: b for b in r.blocks}["host.rx.adc"]
    assert adc.watts == pytest.approx(adc_power(56e9, 8, K))
    assert "n_bits" in adc.note


# ------------------------------------------------------------- mixed signal

def test_unrolled_tap1_multiplies_data_comparators_by_n():
    d = {b.name: b for b in power_report(link(ms_rx(adapt="none", cdr="mueller_muller"))).blocks}
    u = {b.name: b for b in power_report(link(ms_rx(tap1="unrolled", adapt="none",
                                                    cdr="mueller_muller"))).blocks}
    assert u["host.rx.comparators"].watts == pytest.approx(4 * d["host.rx.comparators"].watts)
    # the unrolled tap leaves the summing node
    assert u["host.rx.dfe"].watts == pytest.approx(d["host.rx.dfe"].watts * 2 / 3)


def test_error_and_edge_slicers_counted():
    base = power_report(link(ms_rx(adapt="none", cdr="mueller_muller"))).watts("host.rx.comparators")
    both = power_report(link(ms_rx())).watts("host.rx.comparators")
    assert both == pytest.approx(base * 5 / 3)                 # 3 data + error + edge


def test_architectures_share_the_common_blocks():
    """Invariant 2: what both receivers have is priced by the same coefficient."""
    ms = power_report(link(ms_rx()))
    adc = power_report(link(adc_rx()))
    assert ms.watts("host.rx.common") == pytest.approx(adc.watts("host.rx.common"))
    assert ms.watts("host.tx") == pytest.approx(adc.watts("host.tx"))
    assert ms.watts("host.rx.adc") == 0.0 and adc.watts("host.rx.comparators") == 0.0


# ------------------------------------------------------------- transmitter

def test_dac_tx_adds_its_fir_and_tx_pr_lengthens_it():
    plain = power_report(link(adc_rx(), tx=TxConfig(fir_taps=(-0.1, 1.0, -0.2), fir_n_pre=1)))
    dac = power_report(link(adc_rx(), tx=TxConfig(fir_taps=(-0.1, 1.0, -0.2), fir_n_pre=1, dac_bits=7)))
    pr = power_report(link(adc_rx(mlsd="viterbi"), pr=PrConfig(target=(1.0, 0.5), at="tx"),
                           tx=TxConfig(fir_taps=(-0.1, 1.0, -0.2), fir_n_pre=1, dac_bits=7)))
    assert plain.watts("host.tx.fir") == 0.0
    assert dac.watts("host.tx.fir") == pytest.approx(3 * add_energy(7, K) * 56e9)
    assert pr.watts("host.tx.fir") == pytest.approx(4 * add_energy(7, K) * 56e9)


# ------------------------------------------------------------- topologies

def _optical(retimer="none", placement="pluggable"):
    return TopologyConfig(optical=OpticalConfig(kind="vcsel_mmf", modal_bw_mhz_km=4700.0), retimer=retimer,
                          retimer_rx=adc_rx(enob=5.5, n_dfe=0), retimer_tx=TxConfig(),
                          optics_placement=placement)


def test_optics_only_with_an_optical_topology():
    assert power_report(link(adc_rx())).watts("optics") == 0.0
    r = power_report(link(adc_rx(), topology=_optical()))
    assert r.pj("optics") == pytest.approx(K.optics_j_per_bit * 1e12)


def test_co_packaged_optics_drop_only_the_module_overhead():
    lpo = power_report(link(adc_rx(), topology=_optical()))
    cpo = power_report(link(adc_rx(), topology=_optical(placement="co_packaged")))
    assert cpo.pj("optics") == pytest.approx(K.co_packaged_optics_j_per_bit * 1e12)
    assert cpo.watts("host") == pytest.approx(lpo.watts("host"))      # same receivers, same price
    assert lpo.pj_per_bit - cpo.pj_per_bit == pytest.approx(
        (K.optics_j_per_bit - K.co_packaged_optics_j_per_bit) * 1e12)
    # a retimed co-packaged engine keeps its retimers
    ret = power_report(link(adc_rx(), topology=_optical("both", "co_packaged")))
    assert ret.watts("retimer") == pytest.approx(
        power_report(link(adc_rx(), topology=_optical("both"))).watts("retimer"))


def test_optics_placement_is_validated():
    with pytest.raises(ValueError, match="optics_placement"):
        _optical(placement="onboard")


def test_retimed_is_lpo_plus_two_retimers():
    lpo = power_report(link(adc_rx(), topology=_optical()))
    ret = power_report(link(adc_rx(), topology=_optical("both")))
    one = (power_report(link(adc_rx(enob=5.5, n_dfe=0))).watts("host.rx")
           + power_report(link(adc_rx())).watts("host.tx"))
    assert ret.watts("retimer") == pytest.approx(2 * one)
    assert ret.total_w == pytest.approx(lpo.total_w + 2 * one)
