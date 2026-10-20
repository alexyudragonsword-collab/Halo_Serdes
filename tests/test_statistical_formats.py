"""The statistical engine on PAM6 and PAM8 (448G/lane formats).

The engine took every level as sent 1/N of the time and every wrong
neighbour as one Gray bit. PAM8 keeps both; PAM6's corner32 map sends its
outer levels 1/8 of the time against 3/16 for the inner four, and a wrong
neighbour costs the bits of ``core.mapping.bit_distance_matrix`` -- a
per-symbol stand-in for the pair count the time engine does. Closed forms
first, then invariant #3 (both engines within 2x) on the mixed-signal and
the ADC receiver, the time engine the referee.
"""

import numpy as np
import pytest

from halo_serdes.analysis.metrics import pam4_ser_awgn, pamN_ser_awgn, qfunc
from halo_serdes.cdr.adc_kernel import _adc_rx_py, adc_rx
from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig
from halo_serdes.config.schema import (
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig, FfeConfig, MlsdConfig,
    PrConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.mapping import GRAY_BIT_DISTANCE, bit_distance_matrix, level_probabilities
from halo_serdes.engine import run_time_link
import halo_serdes.engine.statistical as st
from halo_serdes.engine.statistical import (
    _bb_pairs, isi_pdf, pr_symbol_decisions, run_statistical, tx_edge_jitter,
)
from halo_serdes.tx.pipeline import TxPipeline

# the invariant-3 cells run 300-400k symbols on the time engine: minutes each
# through the pure-Python kernels, and the 3.10 nojit CI leg ran past its
# 45-minute cap with them (PR #91); the closed forms above run on both
needs_jit = pytest.mark.skipif(adc_rx is _adc_rx_py,
                               reason="engine-level cross-check; minutes through the pure-Python kernels")


def _flat_channel(f_max=128e9, n=1025):
    """test_statistical.py's: no ISI, half the swing arrives."""
    f = np.linspace(0.0, f_max, n)
    return ChannelModel(np.full(n, 0.5, dtype=complex), f, name="flat")


def _cfg(mod, noise, **kw):
    """test_statistical.py's: 32 GBd, no CTLE / FFE, the noise given."""
    return LinkConfig(
        modulation=mod, symbol_rate=32e9, osr=8,
        channel=ChannelConfig(kind="analytic"),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(ctle=CtleConfig(enable=False),
                    ffe=FfeConfig(n_pre=0, n_post=0),
                    dfe=DfeConfig(n_taps=kw.get("n_dfe", 0)),
                    noise_rms=noise),
        sim=SimConfig(n_symbols=kw.get("n_symbols", 100_000), seed=5,
                      pattern=kw.get("pattern", "prbs31")),
    )


def _gauss_pdfs(n_levels, sigma, v):
    """test_pr.py's: the same Gaussian around every level."""
    p = np.exp(-0.5 * (v / sigma) ** 2)
    return [p / p.sum()] * n_levels


def test_pamN_closed_forms():
    """2 (N - 1) / N thresholds per symbol on equiprobable levels; the corner32
    occupancy makes PAM6's 1.75 (1.667 equiprobable)."""
    assert pamN_ser_awgn(4, 0.25, 0.02) == pytest.approx(pam4_ser_awgn(0.25, 0.02))
    assert pamN_ser_awgn(8, 0.25, 0.01) == pytest.approx(1.75 * qfunc(0.25 / 7 / 0.01))
    assert pamN_ser_awgn(6, 0.25, 0.01) == pytest.approx(5.0 / 3.0 * qfunc(0.25 / 5 / 0.01))
    p6 = level_probabilities("pam6")
    assert pamN_ser_awgn(6, 0.25, 0.01, p6) == pytest.approx(1.75 * qfunc(0.25 / 5 / 0.01))


def test_isi_pdf_weighs_the_levels_as_sent():
    """One cursor: the PDF is the level occupancy, at c times each level."""
    v = np.linspace(-2, 2, 4001)
    lv = np.array([-1.0, -0.6, -0.2, 0.2, 0.6, 1.0])
    p6 = level_probabilities("pam6")
    pdf = isi_pdf(np.array([0.5]), lv, v, p6)
    assert pdf.sum() == pytest.approx(1.0)
    for a, p in zip(lv, p6):
        assert pdf[np.argmin(np.abs(v - 0.5 * a))] == pytest.approx(p)
    uni = isi_pdf(np.array([0.5]), lv, v)
    assert uni[np.argmin(np.abs(v - 0.5))] == pytest.approx(1.0 / 6.0)


def test_flat_channel_pam8_matches_q():
    """No ISI: SER = 7/4 Q(d / sigma), one Gray bit per wrong symbol of three."""
    sigma = (0.25 / 7.0) / 4.0
    cfg = _cfg("pam8", sigma, pattern="prbs13")
    res = run_statistical(cfg, channel=_flat_channel())
    expected = pamN_ser_awgn(8, 0.25, sigma)
    assert abs(res.ser / expected - 1.0) < 0.05, (res.ser, expected)
    assert abs(res.ber / (expected / 3.0) - 1.0) < 0.05


def test_flat_channel_pam6_matches_q_with_corner32_occupancy():
    """SER = 7/4 Q(d / sigma) under the corner32 occupancy; BER from the
    per-symbol bit distances of the neighbours, over 2.5 bits per symbol."""
    sigma = (0.25 / 5.0) / 4.0
    cfg = _cfg("pam6", sigma, pattern="prbs13")
    res = run_statistical(cfg, channel=_flat_channel())
    p6, b6 = level_probabilities("pam6"), bit_distance_matrix("pam6")
    q = qfunc((0.25 / 5.0) / sigma)
    expected_ser = pamN_ser_awgn(6, 0.25, sigma, p6)
    bits = sum(p6[k] * q * ((b6[k, k + 1] if k < 5 else 0.0) + (b6[k, k - 1] if k > 0 else 0.0))
               for k in range(6))
    assert abs(res.ser / expected_ser - 1.0) < 0.05, (res.ser, expected_ser)
    assert abs(res.ber / (bits / 2.5) - 1.0) < 0.05, (res.ber, bits / 2.5)


def test_pr_symbol_decisions_defaults_are_pam4_gray():
    """Passing PAM4's own matrix and occupancy changes nothing."""
    levels = np.linspace(-1.0, 1.0, 4)
    v = np.linspace(-3.0, 3.0, 6001)
    pdfs = _gauss_pdfs(4, 0.11, v)
    for head, kw in (((1.0, 0.5), {}), ((1.0, 0.5), {"precoded": True}), ((1.0, 1.0), {"composite": True})):
        a = pr_symbol_decisions(pdfs, v, levels, head, composite=kw.get("composite", False),
                                precoded=bool(kw))
        b = pr_symbol_decisions(pdfs, v, levels, head, composite=kw.get("composite", False),
                                precoded=bool(kw), bit_dist=GRAY_BIT_DISTANCE,
                                level_probs=np.full(4, 0.25))
        assert a == b


def test_pr_symbol_decisions_pam8_counts_gray_bits_and_pam6_its_matrix():
    """A plain slicer (no controlled cursor): bits per wrong symbol are the
    neighbours' distances, 1 for PAM8's Gray code, PAM6's matrix entries
    (the chain reads its thresholds on the PDF's bins: half a bin of bias,
    a few per cent of a tail four sigma out)."""
    v = np.linspace(-3.0, 3.0, 6001)
    lv8 = np.linspace(-1.0, 1.0, 8)
    ser, nbe = pr_symbol_decisions(_gauss_pdfs(8, 0.035, v), v, lv8, (1.0,), composite=False,
                                   precoded=False, bit_dist=bit_distance_matrix("pam8"))
    assert ser == pytest.approx(pamN_ser_awgn(8, 1.0, 0.035), rel=0.08)
    assert nbe == pytest.approx(ser, rel=1e-9)
    lv6 = np.linspace(-1.0, 1.0, 6)
    p6, b6 = level_probabilities("pam6"), bit_distance_matrix("pam6")
    ser, nbe = pr_symbol_decisions(_gauss_pdfs(6, 0.05, v), v, lv6, (1.0,), composite=False,
                                   precoded=False, bit_dist=b6, level_probs=p6)
    assert ser == pytest.approx(pamN_ser_awgn(6, 1.0, 0.05, p6), rel=0.08)
    q = qfunc(0.2 / 0.05)
    bits = sum(p6[k] * q * ((b6[k, k + 1] if k < 5 else 0.0) + (b6[k, k - 1] if k > 0 else 0.0))
               for k in range(6))
    # the two tails of an inner level read opposite half-bin biases
    assert nbe / ser == pytest.approx(bits / pamN_ser_awgn(6, 1.0, 0.05, p6), rel=1e-2)


def _ms_cfg(mod, rj=0.0):
    return LinkConfig(modulation=mod, symbol_rate=16e9, osr=32,
                      channel=ChannelConfig(kind="analytic", length_m=0.15),
                      tx=TxConfig(swing=1.0, clock=ClockConfig(rj_ui=rj)),
                      rx=RxConfig(arch="mixed_signal"))


def test_tx_edge_jitter_bins_pam8_and_weighs_pam6_as_sent():
    cfg = _ms_cfg("pam8", rj=0.01)
    h = ChannelModel.from_config(cfg).response_set(cfg.dt).h.y
    ej = tx_edge_jitter(cfg, TxPipeline.from_config(cfg), h, cfg.osr)
    assert ej.sigma.shape == (8,) * (1 + len(st._BINNED_NEIGHBOURS))
    assert np.all(ej.sigma >= ej.rest)
    cfg6 = _ms_cfg("pam6", rj=0.01)
    h6 = ChannelModel.from_config(cfg6).response_set(cfg6.dt).h.y
    ej6 = tx_edge_jitter(cfg6, TxPipeline.from_config(cfg6), h6, cfg6.osr)
    assert ej6.sigma.shape == (6,) * (1 + len(st._BINNED_NEIGHBOURS))


@pytest.mark.parametrize("mod", ["pam6", "pam8"])
def test_bb_pairs_cover_every_sign_change(mod):
    """Every pair of levels of opposite sign is a transition; the symmetric
    ones cross at the lock (offset 0), the rest elsewhere."""
    cfg = _ms_cfg(mod)
    rs = ChannelModel.from_config(cfg).response_set(cfg.dt)
    pulse = rs.pulse(cfg.osr) if hasattr(rs, "pulse") else None
    if pulse is None:
        from halo_serdes.channel.response import pulse_from_impulse

        pulse = pulse_from_impulse(rs.h, cfg.osr)
    n = cfg.n_levels
    lv = np.linspace(-1.0, 1.0, n)
    pairs = _bb_pairs(pulse, cfg.osr, 0.01, 1.0, lv, level_probs=level_probabilities(mod))
    expect = {(i, j) for i in range(n) for j in range(n) if (lv[i] > 0) != (lv[j] > 0)}
    assert set(pairs) == expect
    for (i, j), (offset, edge) in pairs.items():
        assert edge > 0.0
        if lv[i] == -lv[j]:
            assert offset == 0.0
        else:
            assert offset != 0.0


def test_dfse_kernel_runs_six_levels():
    """``dsp.mlsd`` packs states base N: a noiseless PAM6 sequence through a
    two-cursor trellis, K = 1, comes back exactly."""
    from halo_serdes.dsp.mlsd import dfse_mlsd, viterbi_mlsd

    rng = np.random.default_rng(2)
    lv = np.linspace(-1.0, 1.0, 6)
    sym = rng.integers(0, 6, 2000)
    cur = np.array([1.0, 0.4, 0.15])
    y = np.convolve(lv[sym], cur)[: sym.size]
    assert np.array_equal(dfse_mlsd(y, lv, cur, 1), sym)
    assert np.array_equal(viterbi_mlsd(y, lv, cur), sym)


def test_pair_isi_pdfs_average_back_to_the_independent_one():
    """Per level the +-1 cursors' ISI is conditioned on the pair partner;
    weighted by the occupancy the per-level PDFs are the independent one,
    and an outer level's PDF has no mass at the partner's extreme shifts."""
    from halo_serdes.core.mapping import neighbour_conditional
    from halo_serdes.engine.statistical import _pair_isi_pdfs

    v = np.linspace(-1, 1, 2001)
    lv = np.linspace(-1.0, 1.0, 6)
    p6, cond = level_probabilities("pam6"), neighbour_conditional("pam6")
    per_level, free, free_pos = _pair_isi_pdfs(np.array([0.1, 0.3]), lv, v, p6, cond)
    assert np.allclose(sum(pk * w for pk, w in zip(per_level, p6)), free, atol=1e-12)
    assert np.allclose(free, isi_pdf(np.array([0.1, 0.3]), lv, v, p6), atol=1e-12)
    # the free neighbour per position: c(+1) alone for the first, c(-1) for the second
    assert np.allclose(free_pos[0], isi_pdf(np.array([0.3]), lv, v, p6), atol=1e-12)
    assert np.allclose(free_pos[1], isi_pdf(np.array([0.1]), lv, v, p6), atol=1e-12)
    # outer level 0: the partner (at +0.3 half the time) is never +-1, so the
    # far shift 0.3 * 1 + 0.1 * 1 = 0.4 carries less mass than for an inner one
    far = np.argmin(np.abs(v - 0.4))
    assert per_level[0][far] < 0.5 * per_level[2][far]


def _one_post_channel(m_amp: float, c_amp: float, swing: float = 1.0) -> ChannelModel:
    """test_statistical.py's: baud cursors at the slicer exactly [m_amp, c_amp]
    volts for the outer levels."""
    f = np.linspace(0, 128e9, 1025)
    ui = 1 / 32e9
    H = (m_amp + c_amp * np.exp(-2j * np.pi * f * ui)) / (swing / 2.0)
    return ChannelModel(H, f, name="1post")


def test_pam6_bits_follow_the_partner_the_error_came_with():
    """One post-cursor, AWGN: a PAM6 symbol errs mostly when the symbol
    before it pushes it over a threshold. In the pair's second position
    that symbol is its partner, so the bits the error costs are not the
    partner-averaged ``bit_distance_matrix`` but the pair's own
    (``pair_bit_distance``): B6 read 1.17-1.34 bits per error on the
    mixed-signal cells where the checker counted 1.01-1.06 (ROADMAP P2 #33).
    Closed form against the engine, and the B6 figure shown to differ."""
    from halo_serdes.core.mapping import neighbour_conditional, pair_bit_distance

    m_amp, c_amp, sigma = 0.25, 0.02, 0.012
    cfg = _cfg("pam6", sigma, pattern="prbs13")
    res = run_statistical(cfg, channel=_one_post_channel(m_amp, c_amp))
    a = np.array([-1.0, -0.6, -0.2, 0.2, 0.6, 1.0])          # pam6 levels, normalised
    p6, cond, D = level_probabilities("pam6"), neighbour_conditional("pam6"), pair_bit_distance("pam6")
    B6 = bit_distance_matrix("pam6")
    half = 0.2 * m_amp

    def tails(shift):
        up = qfunc((half - shift) / sigma)
        dn = qfunc((half + shift) / sigma)
        return up, dn

    ser = ber = ber_b6 = 0.0
    for k in range(6):
        for m in range(6):
            w = 0.5 * p6[k] * cond[k, m]
            if w == 0.0:
                continue
            # second position: the partner came before, on the post-cursor
            up, dn = tails(c_amp * a[m])
            up, dn = (up if k < 5 else 0.0), (dn if k > 0 else 0.0)
            ser += w * (up + dn)
            ber += w * (up * D[1, k, m, min(k + 1, 5)] + dn * D[1, k, m, max(k - 1, 0)])
            ber_b6 += w * (up * B6[k, min(k + 1, 5)] + dn * B6[k, max(k - 1, 0)])
            # first position: the symbol before is free, the partner after is not seen here
            for i in range(6):
                up, dn = tails(c_amp * a[i])
                up, dn = (up if k < 5 else 0.0), (dn if k > 0 else 0.0)
                ser += w * p6[i] * (up + dn)
                ber += w * p6[i] * (up * D[0, k, m, min(k + 1, 5)] + dn * D[0, k, m, max(k - 1, 0)])
                ber_b6 += w * p6[i] * (up * B6[k, min(k + 1, 5)] + dn * B6[k, max(k - 1, 0)])
    assert res.ser == pytest.approx(ser, rel=0.05), (res.ser, ser)
    assert res.ber == pytest.approx(ber / 2.5, rel=0.05), (res.ber, ber / 2.5)
    assert abs(ber_b6 / ber - 1.0) > 0.08, (ber_b6, ber)    # the per-symbol stand-in is off here


def test_pam6_pairs_lower_the_mixed_signal_ser_against_independent_symbols(monkeypatch):
    """ROADMAP P2 #35: on the 16 GBd mixed-signal link without a DFE the
    +-1 cursors carry the ISI, and the pair partner never being an outer
    level for an outer symbol makes errors rarer. The time engine on
    corner32 read 1.6-2.8x below itself on i.i.d. symbols (0.10-0.15 m);
    the engine conditioned on the partner follows it, the independent model
    does not."""
    import halo_serdes.engine.statistical as st_mod

    cfg = LinkConfig(
        modulation="pam6", symbol_rate=16e9, osr=32,
        channel=ChannelConfig(kind="analytic", length_m=0.10, rdc=2.0, r_skin=1.5e-3,
                              loss_tangent=0.01),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=False), dfe=DfeConfig(n_taps=0),
                    noise_rms=0.004),
        sim=SimConfig(n_symbols=100_000, seed=3, pattern="prbs13"))
    cm = ChannelModel.from_config(cfg)
    paired = run_statistical(cfg, channel=cm)
    monkeypatch.setattr(st_mod, "neighbour_conditional", lambda m: None)
    independent = run_statistical(cfg, channel=cm)
    assert 2.0 < independent.ser / paired.ser < 3.5, (independent.ser, paired.ser)
    # the time engine measured 4.7e-4 (corner32) and 1.3e-3 (i.i.d.) here
    assert 3e-4 < paired.ser < 7e-4 and 1e-3 < independent.ser < 1.7e-3


# ---- invariant #3: both engines within 2x -----------------------------------

@needs_jit
@pytest.mark.parametrize("mod,length_m,noise,n_dfe,ctle_db", [
    ("pam6", 0.2, 0.005, 2, None), ("pam6", 0.15, 0.006, 0, None),
    ("pam8", 0.15, 0.004, 2, 4.0), ("pam8", 0.12, 0.003, 0, 4.0)])
def test_mixed_signal_cross_check_within_2x(mod, length_m, noise, n_dfe, ctle_db):
    """The mixed-signal receiver at 16 GBd on the short analytic channel of
    ``test_statistical.py``, PAM6 and PAM8 (16 GBd is past their calibrated
    mixed-signal envelopes, 32.5 / 33 Gb/s; the run warns "exceeds" and goes
    on). PAM8's eye is closed there without equalisation (1/7 of
    the swing between levels), so it gets the CTLE. A failure here reports
    the ratio, not a skip.

    Measured (cairn/448G调制格式.md §3): PAM6 with the DFE 0.55-0.58, without
    it 1.05-1.18 once the +-1 cursors' ISI is conditioned on the pair
    partner (1.45-3.9 before, ROADMAP P2 #35) and the bits counted per pair
    (1.30-1.37 before, P2 #33); PAM8 0.53-1.07."""
    cfg = LinkConfig(
        modulation=mod, symbol_rate=16e9, osr=32,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=2.0,
                              r_skin=1.5e-3, loss_tangent=0.01),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="mixed_signal",
                    ctle=CtleConfig(enable=ctle_db is not None, peak_db=ctle_db or 6.0),
                    dfe=DfeConfig(n_taps=n_dfe), noise_rms=noise),
        sim=SimConfig(n_symbols=400_000, seed=3, pattern="prbs13"))
    cm = ChannelModel.from_config(cfg)
    with pytest.warns(UserWarning, match="exceeds the mixed-signal envelope"):
        mc = run_time_link(cfg, channel=cm)       # 40 / 48 Gb/s, past the 32.5 / 33 limits
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    stat = run_statistical(cfg, channel=cm)
    ratio = stat.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (mod, length_m, noise, n_dfe, stat.ber, mc.ber.ber, ratio)


def _adc_cfg(mod, length_m, pr=None, memory=1, n_sym=300_000):
    """Example 38's receiver shape: 21-tap LMS FFE, Viterbi, MM CDR, 112 GBd."""
    return LinkConfig(
        modulation=mod, symbol_rate=112e9, osr=16,
        channel=ChannelConfig(kind="analytic", length_m=length_m, rdc=5.0, r_skin=2.0e-3,
                              loss_tangent=0.012, n_freq=8192),
        tx=TxConfig(swing=1.0, fir_taps=(-0.06, 1.0, -0.12), fir_n_pre=1),
        rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=6.0),
                    adc=AdcConfig(n_bits=8, n_lanes=16, enob=6.5, fullscale=0.6),
                    ffe=FfeConfig(n_pre=6, n_post=14, adapt="lms", mu=3e-5),
                    dfe=DfeConfig(n_taps=0),
                    mlsd=MlsdConfig(kind="viterbi", memory=memory),
                    cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                    noise_rms=0.0015),
        sim=SimConfig(n_symbols=n_sym, seed=3, pattern="prbs13"), pr=pr or PrConfig())


@needs_jit
@pytest.mark.parametrize("mod,length_m,pr", [
    ("pam6", 0.18, None), ("pam8", 0.15, None),
    ("pam6", 0.20, PrConfig(target=(1.0, 0.5), adapt="mmse")),
    ("pam8", 0.17, PrConfig(target=(1.0, 0.5), adapt="mmse"))])
def test_adc_cross_check_within_2x(mod, length_m, pr):
    """The ADC receiver (example 38's shape) on PAM6 / PAM8, delta and a
    1 + aD MMSE target with the Viterbi detector, the time engine's FFE
    handed over as the statistical engine does with the ADC receiver."""
    cfg = _adc_cfg(mod, length_m, pr)
    cm = ChannelModel.from_config(cfg)
    mc = run_time_link(cfg, channel=cm)
    assert mc.ber.n_errors > 100, mc.ber.n_errors
    stat = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
    ratio = stat.ber / mc.ber.ber
    assert 0.5 < ratio < 2.0, (mod, length_m, pr, stat.ber, mc.ber.ber, ratio)
    if mod == "pam6":
        # bits per symbol error: the pairs' words in both engines (P2 #33 on
        # the slicer path, P3 #12 under the sequence detector; B6 read 1.16x
        # the checker on the 1 + aD cell)
        per_err = (stat.ber / stat.ser) / (mc.ber.ber / mc.ser)
        assert 0.92 < per_err < 1.08, (pr, stat.ber / stat.ser * 2.5, mc.ber.ber / mc.ser * 2.5)


# ---- invariant #3 with PAM6 crosstalk: the aggressors send the pair stream too ----

def _pam6_bank(cfg, coupling_db):
    from halo_serdes.channel import aggressor_bank

    return aggressor_bank(2, 2, coupling_db, cfg.ui, cfg.dt, modulation="pam6", base_seed=1)


def test_mixed_signal_pam6_crosstalk_cross_check_within_2x():
    """ROADMAP P3 #12: PAM6 aggressors drawn uniform over six levels read
    0.81-0.86x more SER in both engines than the corner32 stream they send
    (second moment 0.467 against 0.4), and an engine on either model against
    the other 1.17-1.27x. Both now take the occupancy: the 16 GBd
    mixed-signal link without a DFE, four aggressors at -40 dB (12x the
    clean SER), 1.01x. 16 GBd PAM6 is past the calibrated envelope (32.5
    Gb/s): the run warns "exceeds" and goes on, as the invariant-3 probes
    always did."""
    cfg = LinkConfig(
        modulation="pam6", symbol_rate=16e9, osr=32,
        channel=ChannelConfig(kind="analytic", length_m=0.10, rdc=2.0, r_skin=1.5e-3,
                              loss_tangent=0.01),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(arch="mixed_signal", ctle=CtleConfig(enable=False), dfe=DfeConfig(n_taps=0),
                    noise_rms=0.004),
        sim=SimConfig(n_symbols=200_000, seed=3, pattern="prbs13"))
    cm = ChannelModel.from_config(cfg)
    bank = _pam6_bank(cfg, -40.0)
    with pytest.warns(UserWarning, match="exceeds the mixed-signal envelope"):
        clean = run_time_link(cfg, channel=cm)
        mc = run_time_link(cfg, channel=cm, xtalk=bank)
    assert mc.ber.n_errors > 300 and mc.ser > 2.0 * clean.ser, (mc.ser, clean.ser)
    stat = run_statistical(cfg, channel=cm, xtalk_pulses=[a.pulse(cfg.osr) for a in bank])
    ratio = stat.ser / mc.ser
    assert 0.5 < ratio < 2.0, (stat.ser, mc.ser, ratio)


@needs_jit
def test_adc_pam6_crosstalk_cross_check_within_2x():
    """The ADC receiver of example 38 at 112 GBd, PAM6, four aggressors at
    -52 dB (crosstalk 3x the clean SER): 1.06x SER / 1.08x BER with both
    engines on the corner32 occupancy (ROADMAP P3 #12)."""
    cfg = _adc_cfg("pam6", 0.18, n_sym=300_000)
    cm = ChannelModel.from_config(cfg)
    bank = _pam6_bank(cfg, -52.0)
    mc = run_time_link(cfg, channel=cm, xtalk=bank)
    assert mc.ber.n_errors > 500, mc.ber.n_errors
    stat = run_statistical(cfg, channel=cm, ffe_taps=mc.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre,
                           xtalk_pulses=[a.pulse(cfg.osr) for a in bank])
    ratio = stat.ser / mc.ser
    assert 0.5 < ratio < 2.0, (stat.ser, mc.ser, ratio)
