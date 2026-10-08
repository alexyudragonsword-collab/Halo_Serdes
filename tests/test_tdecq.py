"""TDECQ (optical stage 3): closed forms for the reference receiver, the
ideal eye, and an eye with known Gaussian noise; then the measurement on a
modelled transmitter, which must move the way the physics says."""

import dataclasses

import numpy as np
import pytest

from halo_serdes.analysis.tdecq import Q_T, TARGET_SER, bt4_response, tdecq
from halo_serdes.core.prbs import prbs_q_symbols
from halo_serdes.core.sampler import hold

BAUD, OSR = 53.125e9, 16
DT = 1.0 / BAUD / OSR
OMA, PAVE = 1e-3, 1.25e-3
LEVELS = PAVE + np.array([-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0]) * OMA / 2.0
SYM = prbs_q_symbols(13, 2 * 8191)
IDEAL = hold(LEVELS[SYM], OSR)


def test_qt_is_the_ideal_eye_at_the_target_ser():
    """Q_t = 3.414: an ideal PAM4 eye has SER = 1.5 Q(OMA / (6 sigma))."""
    from halo_serdes.analysis.metrics import qfunc

    assert Q_T == pytest.approx(3.414, abs=1e-3)
    assert 1.5 * qfunc(Q_T) == pytest.approx(TARGET_SER, rel=1e-9)


def test_reference_receiver_is_a_bt4_at_half_baud():
    f3 = BAUD / 2
    assert abs(bt4_response(np.array([0.0]), f3))[0] == pytest.approx(1.0)
    assert abs(bt4_response(np.array([f3]), f3))[0] == pytest.approx(1 / np.sqrt(2), rel=1e-6)
    f = np.linspace(0, 3 * f3, 3001)
    assert np.all(np.diff(np.abs(bt4_response(f, f3))) <= 1e-12)      # no peaking
    # Bessel: near-constant group delay through the passband
    ph = np.unwrap(np.angle(bt4_response(f, f3)))
    gd = -np.diff(ph) / (2 * np.pi * np.diff(f))
    band = f[1:] < 0.8 * f3
    assert np.ptp(gd[band]) / gd[0] < 0.02


def test_ideal_eye_scores_zero():
    r = tdecq(IDEAL, DT, BAUD, SYM, reference_filter=False)
    assert abs(r.tdecq_db) < 0.02
    assert r.ceq == pytest.approx(1.0, abs=1e-3)
    assert r.oma_outer_w == pytest.approx(OMA, rel=1e-9)
    assert r.p_ave_w == pytest.approx(PAVE, rel=1e-3)
    assert r.rlm == pytest.approx(1.0, abs=1e-6)
    assert r.er_db == pytest.approx(10 * np.log10(LEVELS[3] / LEVELS[0]), rel=1e-9)


@pytest.mark.parametrize("seed", range(4))
def test_known_gaussian_noise_matches_the_closed_form(seed):
    """Noise of sigma_w already in the eye leaves room for
    sqrt(sigma_ideal^2 - sigma_w^2), so TDECQ = -5 log10(1 - (sigma_w /
    sigma_ideal)^2): 0.969 dB at 0.6 sigma_ideal, within 0.2 dB. The noise is
    one value per UI -- band-limited, as a real eye's is; white per-sample
    noise would be averaged down by the histogram window's interpolation."""
    sigma_ideal = OMA / (6 * Q_T)
    frac = 0.6
    rng = np.random.default_rng(seed)
    noisy = IDEAL + hold(rng.normal(size=SYM.size), OSR) * frac * sigma_ideal
    r = tdecq(noisy, DT, BAUD, SYM, reference_filter=False)
    assert r.tdecq_db == pytest.approx(-5 * np.log10(1 - frac ** 2), abs=0.2)


def test_ideal_transmitter_through_the_reference_receiver():
    """An infinitely fast transmitter still closes a little behind the BT4
    and the 5-tap FFE: residual ISI plus the FFE's noise gain."""
    r = tdecq(IDEAL, DT, BAUD, SYM)
    assert 0.0 < r.tdecq_db < 0.5
    assert r.ceq > 1.0 and r.taps.sum() == pytest.approx(1.0, abs=1e-9)
    assert r.n_pre in (1, 2)
    assert int(np.argmax(np.abs(r.taps))) == r.n_pre


def test_isi_raises_tdecq_monotonically():
    """A slower transmitter scores worse. A near-closed eye scores a large
    but finite number: the SER is region-based, as an instrument's
    histograms are, so samples that already sit in the wrong region are
    charged only for crossing back -- TDECQ grows without bound only as the
    eye's samples reach the thresholds."""
    def lowpass(y, f3):
        n = int(2 ** np.ceil(np.log2(y.size + 4096)))
        H = 1.0 / (1.0 + 1j * np.fft.rfftfreq(n, d=DT) / f3)
        return np.fft.irfft(np.fft.rfft(y - y.mean(), n) * H, n)[: y.size] + y.mean()

    vals = [tdecq(lowpass(IDEAL, bw), DT, BAUD, SYM, optimise=False).tdecq_db
            for bw in (60e9, 30e9, 18e9, 10e9, 4e9)]
    assert all(a < b for a, b in zip(vals, vals[1:])), vals
    assert vals[-1] > 15.0


def test_scope_noise_is_credited_back():
    """sigma_S enters under the root with sigma_G: a measured eye carrying
    the instrument's own noise is not charged for it."""
    a = tdecq(IDEAL, DT, BAUD, SYM, reference_filter=False)
    b = tdecq(IDEAL, DT, BAUD, SYM, reference_filter=False, sigma_scope_w=0.5 * a.sigma_g_w)
    assert b.tdecq_db == pytest.approx(a.tdecq_db - 10 * np.log10(np.sqrt(1.25)), abs=1e-6)


def test_non_pam4_is_refused():
    with pytest.raises(ValueError, match="PAM4"):
        tdecq(IDEAL, DT, BAUD, np.full(SYM.size, 5))


# ------------------------------------------------- modelled transmitter ---

def _lpo():
    from halo_serdes_app import config_bridge as cb

    cfg = cb.load_preset("PAM4 100G/λ LPO (VCSEL + OM4)")
    return dataclasses.replace(cfg, sim=dataclasses.replace(cfg.sim, n_symbols=2 * 8191))


def _opt(cfg, **kw):
    return dataclasses.replace(cfg, topology=dataclasses.replace(
        cfg.topology, optical=dataclasses.replace(cfg.topology.optical, **kw)))


def _tp2(cfg, **kw):
    from halo_serdes.engine.optical_stage import transmitter_power

    power, line = transmitter_power(cfg, include_seg_a=False, **kw)
    return tdecq(power, cfg.dt, cfg.symbol_rate, line)


def test_transmitter_tdecq_follows_er_bandwidth_and_compression():
    """At TP2: more ER costs less RIN per unit OMA, a faster laser closes
    less, a compressed L-I curve misplaces the inner levels -- each moves
    TDECQ in its own direction, and the measured OMA is the configured one."""
    cfg = _lpo()
    base = _tp2(cfg)
    assert 1.0 < base.tdecq_db < 4.4                        # inside the 802.3db SR1 limit
    assert base.oma_outer_w == pytest.approx(cfg.topology.optical.oma_w, rel=0.02)
    assert _tp2(_opt(cfg, er_db=2.5)).tdecq_db > base.tdecq_db > _tp2(_opt(cfg, er_db=6.0)).tdecq_db
    assert _tp2(_opt(cfg, f_r_hz=16e9)).tdecq_db > base.tdecq_db > _tp2(_opt(cfg, f_r_hz=30e9)).tdecq_db
    squeezed = _tp2(_opt(cfg, li_compression=0.4))
    assert squeezed.tdecq_db > base.tdecq_db
    assert squeezed.rlm < 0.8 < base.rlm
    # RIN is part of what TDECQ measures
    assert _tp2(cfg, include_rin=False).tdecq_db < base.tdecq_db


def test_dispersion_eye_closure_grows_with_fibre():
    """The "D" in TDECQ: the same EML through more SMF closes more. At
    -3 ps/(nm km) the first fade sits at 303 GHz after 500 m and at 38 GHz
    after 20 km, inside the reference receiver's band."""
    from halo_serdes.optical import fiber

    cfg = _opt(_lpo(), kind="eml_smf", f_r_hz=45e9, modal_bw_mhz_km=None,
               dispersion_ps_nm_km=-3.0, er_db=4.5, rin_db_hz=-145.0)
    assert fiber.first_fade_hz(-3.0, 20_000.0, 1310.0) < 40e9
    near = _tp2(_opt(cfg, length_m=500.0), through_fibre=True).tdecq_db
    far = _tp2(_opt(cfg, length_m=20_000.0), through_fibre=True).tdecq_db
    assert far > near + 0.5


# ---------------------------------------------- 802.3dj reference DFE ---

def _postcursor(h):
    """An eye with one post-cursor h, scaled so the long-run (OMA) levels
    are the ideal ones: x = (a[k] + h a[k-1]) / (1 + h)."""
    a = np.array([-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0])[SYM]
    x = (a + h * np.roll(a, 1)) / (1 + h)
    return hold(PAVE + x * OMA / 2.0, OSR)


@pytest.mark.parametrize("h", [0.1, 0.2, 0.3])
def test_dfe_cancels_a_postcursor_at_the_closed_form(h):
    """The DFE removes h a[k-1] without noise; what is left is the cursor's
    1 / (1 + h) share of the OMA, so the FFE must gain 1 + h and TDECQ =
    10 log10(1 + h) with b = h. The FFE alone has to invert the post-cursor
    and pays more."""
    w = _postcursor(h)
    with_dfe = tdecq(w, DT, BAUD, SYM, reference_filter=False, dfe=True)
    ffe = tdecq(w, DT, BAUD, SYM, reference_filter=False)
    assert with_dfe.tdecq_db == pytest.approx(10 * np.log10(1 + h), abs=0.01)
    assert with_dfe.dfe_b == pytest.approx(h, abs=0.02)
    assert with_dfe.taps.sum() - with_dfe.dfe_b == pytest.approx(1.0, abs=1e-9)
    assert ffe.dfe_b == 0.0 and ffe.tdecq_db > with_dfe.tdecq_db + 0.015


def test_dfe_coefficient_is_bounded():
    """0 <= b <= 0.3 (802.3dj): a post-cursor of 0.5 gets 0.3 and the FFE
    the rest; a negative one gets none, and then the DFE changes nothing."""
    big = tdecq(_postcursor(0.5), DT, BAUD, SYM, reference_filter=False, dfe=True)
    assert big.dfe_b == pytest.approx(0.3)
    assert 10 * np.log10(1.5) < big.tdecq_db < tdecq(_postcursor(0.5), DT, BAUD, SYM,
                                                     reference_filter=False).tdecq_db
    neg = _postcursor(-0.1)
    a = tdecq(neg, DT, BAUD, SYM, reference_filter=False, dfe=True)
    b = tdecq(neg, DT, BAUD, SYM, reference_filter=False)
    assert a.dfe_b == 0.0 and a.tdecq_db == b.tdecq_db
    with pytest.raises(ValueError, match="skip_ui"):
        tdecq(IDEAL, DT, BAUD, SYM, dfe=True, skip_ui=0)


def test_dfe_helps_a_slow_200g_transmitter_more():
    """The 200G/lambda measurement the app uses carries the DFE. On an EML
    after 500 m of SMF it never scores worse than the FFE alone, and it is
    worth more on a slow laser, whose post-cursor is larger."""
    from halo_serdes_app.studies import _tdecq_settings, tdecq_value

    cfg = _opt(_lpo(), kind="eml_smf", modal_bw_mhz_km=None, dispersion_ps_nm_km=-1.9,
               er_db=4.5, rin_db_hz=-145.0, length_m=500.0, f_r_hz=55e9)
    cfg = dataclasses.replace(cfg, symbol_rate=113.4375e9)
    assert _tdecq_settings(cfg)["dfe"] is True

    def gain(bw):
        from halo_serdes.engine.optical_stage import transmitter_power

        c = _opt(cfg, f_r_hz=bw)
        power, line = transmitter_power(c, include_seg_a=False, through_fibre=True)
        kw = dict(n_taps=15, pre_options=(1, 2, 3), f_ref_hz=53.125e9)
        ffe = tdecq(power, c.dt, c.symbol_rate, line, **kw)
        dfe = tdecq(power, c.dt, c.symbol_rate, line, dfe=True, **kw)
        assert dfe.tdecq_db <= ffe.tdecq_db + 1e-9
        assert 0.0 <= dfe.dfe_b <= 0.3
        return ffe.tdecq_db - dfe.tdecq_db

    assert gain(35e9) > gain(80e9) + 0.1
    assert tdecq_value(cfg).dfe_b > 0.0
