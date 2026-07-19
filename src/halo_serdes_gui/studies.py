"""Parameter-sweep / projection computations for the study tabs.

All studies are cheap enough to run on render: the statistical engine
(analytic, ~tens of ms), pure FEC formulas, behavioral COM, and fixed-point
datapath *replay* on already-captured ADC codes (no engine re-run). Results
are cached per run id so switching away and back is instant.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from halo_serdes.channel import ChannelModel
from halo_serdes.config import LinkConfig

_CACHE: dict[tuple, object] = {}
_ORDER: list[tuple] = []
_MAX = 64


def _cached(key, fn):
    if key in _CACHE:
        return _CACHE[key]
    val = fn()
    _CACHE[key] = val
    _ORDER.append(key)
    while len(_ORDER) > _MAX:
        _CACHE.pop(_ORDER.pop(0), None)
    return val


def _stat_ber(cfg: LinkConfig, channel=None, xtalk_pulses=None) -> float:
    from halo_serdes.engine.statistical import run_statistical
    if channel is None:
        channel = ChannelModel.from_config(cfg)
    return run_statistical(cfg, channel=channel, xtalk_pulses=xtalk_pulses).ber


def _reach(loss, post):
    """Interpolate the loss where post-FEC BER crosses 1e-15 (or None)."""
    x = np.asarray(loss)
    lp = np.log10(np.maximum(post, 1e-300))
    for i in range(len(x) - 1):
        if lp[i] < -15 <= lp[i + 1]:
            t = (-15 - lp[i]) / (lp[i + 1] - lp[i])
            return float(x[i] + t * (x[i + 1] - x[i]))
    return None


# --- reach vs channel loss -------------------------------------------------

def reach_study(rec) -> dict:
    def compute():
        cfg = rec.cfg
        if cfg.channel.kind != "analytic":
            return {"error": "reach sweep varies analytic channel length; "
                    "set channel.kind = analytic."}
        base = cfg.channel.length_m or 0.2
        lengths = np.linspace(0.6 * base, 1.9 * base, 8)
        from halo_serdes.fec import pre_to_post_fec_ber
        loss, pre = [], []
        for L in lengths:
            c = dataclasses.replace(cfg, channel=dataclasses.replace(
                cfg.channel, length_m=float(L)))
            cm = ChannelModel.from_config(c)
            loss.append(-cm.loss_at(cfg.f_nyquist))
            pre.append(max(_stat_ber(c, cm), 1e-300))
        loss = np.array(loss); pre = np.array(pre)
        kp4 = np.array([pre_to_post_fec_ber(max(p, 1e-9), "kp4") for p in pre])
        kr4 = np.array([pre_to_post_fec_ber(max(p, 1e-9), "kr4") for p in pre])
        return {"loss": loss, "pre": pre, "kp4": kp4, "kr4": kr4,
                "reach_kp4": _reach(loss, kp4)}
    return _cached(("reach", rec.id), compute)


# --- crosstalk sweep -------------------------------------------------------

def crosstalk_study(rec) -> dict:
    def compute():
        from halo_serdes.channel import synthetic_aggressor
        cfg = rec.cfg
        cm = ChannelModel.from_config(cfg)
        base = _stat_ber(cfg, cm)
        couplings = [-40, -34, -30, -26, -22, -18]
        ber = []
        for cdb in couplings:
            fext = synthetic_aggressor("fext", cdb, cfg.ui, cfg.dt, seed=11,
                                       modulation=cfg.modulation)
            nxt = synthetic_aggressor("next", cdb - 3, cfg.ui, cfg.dt, seed=22,
                                      modulation=cfg.modulation)
            xp = [fext.pulse(cfg.osr), nxt.pulse(cfg.osr)]
            ber.append(max(_stat_ber(cfg, cm, xtalk_pulses=xp), 1e-300))
        return {"coupling": np.array(couplings), "ber": np.array(ber),
                "baseline": max(base, 1e-300)}
    return _cached(("xtalk", rec.id), compute)


# --- behavioral COM vs loss ------------------------------------------------

def com_study(rec) -> dict:
    def compute():
        from halo_serdes.io import NativeCom
        cfg = rec.cfg
        if cfg.channel.kind != "analytic":
            return {"error": "COM sweep varies analytic channel length; "
                    "set channel.kind = analytic."}
        com = NativeCom(n_dfe=max(cfg.rx.dfe.n_taps, 1),
                        rx_ffe_taps=cfg.rx.ffe.n_pre + 1 + cfg.rx.ffe.n_post,
                        rx_ffe_pre=cfg.rx.ffe.n_pre)
        base = cfg.channel.length_m or 0.2
        loss, comdb = [], []
        for L in np.linspace(0.5 * base, 2.0 * base, 9):
            c = dataclasses.replace(cfg, channel=dataclasses.replace(
                cfg.channel, length_m=float(L)))
            cm = ChannelModel.from_config(c)
            loss.append(-cm.loss_at(cfg.f_nyquist))
            comdb.append(com.compute(cm, c).com_db)
        return {"loss": np.array(loss), "com_db": np.array(comdb)}
    return _cached(("com", rec.id), compute)


# --- FEC projection (config-independent) -----------------------------------

def fec_projection() -> dict:
    def compute():
        from halo_serdes.fec import (
            concatenated_post_fec_ber, pre_to_post_fec_ber,
        )
        pre = np.logspace(-2, -5, 40)
        return {
            "pre": pre,
            "kp4": np.array([pre_to_post_fec_ber(p, "kp4") for p in pre]),
            "kr4": np.array([pre_to_post_fec_ber(p, "kr4") for p in pre]),
            "concat": np.array([concatenated_post_fec_ber(p, 255, 5) for p in pre]),
        }
    return _cached(("fec", "static"), compute)


# --- fixed-point word-length sweep (datapath replay) -----------------------

def fixedpoint_study(rec) -> dict:
    def compute():
        cfg = rec.cfg
        e = rec.sim.extras if rec.sim is not None else {}
        adc = e.get("adc")
        q = e.get("q_hist_head")
        if adc is None or q is None or rec.sim.ffe_taps is None:
            return {"error": "fixed-point replay needs an ADC run (rx.arch = "
                    "adc_dsp) — load the PAM4 224G ADC preset and Run."}
        from halo_serdes.config.schema import NumericConfig, QFormat
        from halo_serdes.dsp.fixed_datapath import run_fixed_datapath

        codes = np.round(np.asarray(q) / adc.q_step - 0.5).astype(np.int64)
        levels = e["levels"]; n_pre = cfg.rx.ffe.n_pre
        wide = NumericConfig(ffe_weight=QFormat(20, 16), dfe_weight=QFormat(20, 16))
        dec_ref, *_ = run_fixed_datapath(codes, rec.sim.ffe_taps, rec.sim.dfe_taps,
                                         levels, n_pre, wide, adc.cfg.fullscale,
                                         adc.cfg.n_bits)
        wls = [4, 5, 6, 7, 8, 10, 12]
        mism = []
        for wl in wls:
            num = NumericConfig(ffe_weight=QFormat(wl, wl - 2),
                                dfe_weight=QFormat(wl, wl - 2))
            dec, *_ = run_fixed_datapath(codes, rec.sim.ffe_taps, rec.sim.dfe_taps,
                                         levels, n_pre, num, adc.cfg.fullscale,
                                         adc.cfg.n_bits)
            mism.append(max(float(np.mean(dec != dec_ref)), 1e-6))
        return {"wl": np.array(wls), "mismatch": np.array(mism)}
    return _cached(("fixed", rec.id), compute)


# --- jitter tolerance (JTOL) ------------------------------------------------

def jtol_study(rec) -> dict:
    def compute():
        import numpy as _np

        from halo_serdes.analysis import jitter_tolerance, jtol_mask
        cfg = rec.cfg
        # reduced-fidelity sweep for GUI responsiveness (each point is a full
        # time-domain run x binary search); raise n_symbols in scripts.
        freqs = _np.array([1e6, 5e6, 2e7, 5e7, 1e8, 2e8, 4e8])
        jt = jitter_tolerance(cfg, freqs, ber_threshold=1e-3, amp_lo=0.05,
                              amp_hi=4.0, iters=4,
                              n_symbols=min(cfg.sim.n_symbols, 12000))
        mask = jtol_mask(freqs, lf_max_ui=4.0, f_corner=3e6, hf_floor_ui=0.15)
        return {"freqs": jt.freqs, "tol_ui": jt.tol_ui, "mask": mask,
                "threshold": jt.ber_threshold}
    return _cached(("jtol", rec.id), compute)
