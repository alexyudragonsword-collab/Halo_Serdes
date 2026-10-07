"""Generate RTL lockstep vectors from small fixed-point runs.

1. FFE + DFE + slicer: runs a short ADC-based link, captures the ADC codes +
   adapted weights, replays the bit-true datapath (the Python golden), and
   dumps the stimulus/response vectors plus dims.svh.
2. The whole back end with its CDR: runs the same link with
   numeric.mode='fixed' (the bit-true loop closes through the sampler), with
   a wandering Rx clock so the phase interpolator has to move, and dumps the
   recorded ADC words, PI codes, slicer values and decisions plus
   loop_dims.svh (dsp/fixed_loop.dump_loop_vectors); then the same loop with
   a partial-response target -- 1 + aD + bD^2 with a and b adapted, into
   <out>/pr, and precoded 1 + D on the composite slicer, into <out>/pre.
3. The sliding-detector MLSD (dsp/fixed_mlsd): slicer words of a channel
   with a strong residual postcursor, so the detector has errors to correct
   (an MMSE FFE leaves the loop above none), with a non-zero metric shift,
   saturation and margin, dumped with mlsd_dims.svh.
4. The Viterbi MLSD (dsp/fixed_viterbi): PAM4 through 1 + 0.6D + 0.2D^2
   (16 states), with a metric shift and a narrow saturating metric, dumped
   with vit_dims.svh.

    python rtl/gen_vectors.py [out_dir]   (default: rtl/vectors)
"""

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.config import LinkConfig  # noqa: E402
import dataclasses  # noqa: E402

from halo_serdes.config.schema import (  # noqa: E402
    AdcCalConfig, AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig,
    FfeConfig, MlsdConfig, NumericConfig, PrConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.dsp.fixed_datapath import (  # noqa: E402
    dump_sv_package, dump_vectors, run_fixed_datapath,
)
from halo_serdes.dsp.fixed_loop import dump_loop_vectors, loop_artifacts  # noqa: E402
from halo_serdes.dsp.fixed_mlsd import (  # noqa: E402
    FixedSliding, dump_mlsd_vectors, run_fixed_sliding,
)
from halo_serdes.dsp.fixed_viterbi import (  # noqa: E402
    FixedViterbi, dump_viterbi_vectors, expected_table, run_fixed_viterbi,
)
from halo_serdes.engine import run_time_link  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "rtl" / "vectors"

cfg = LinkConfig(
    modulation="pam4", symbol_rate=106.25e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.12, rdc=5.0,
                          r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
    tx=TxConfig(swing=1.0, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
    rx=RxConfig(arch="adc_dsp", ctle=CtleConfig(enable=True, peak_db=4.0),
                adc=AdcConfig(n_bits=8, n_lanes=16, fullscale=0.6),
                ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                noise_rms=0.0015),
    sim=SimConfig(n_symbols=30000, seed=3, pattern="prbs13q"))

res = run_time_link(cfg)
adc = res.extras["adc"]
q = res.extras["q_hist_head"]            # first 8192 dequantized samples
codes = np.round(q / adc.q_step - 0.5).astype(np.int64)
codes = codes[:2000]                     # keep the RTL run fast

dec, v_float, art = run_fixed_datapath(
    codes, res.ffe_taps, res.dfe_taps, res.extras["levels"], cfg.rx.ffe.n_pre,
    cfg.numeric, adc.cfg.fullscale, adc.cfg.n_bits)

dump_vectors(out, art)
dump_sv_package(out, art)
print(f"wrote vectors to {out}  (N={codes.size}, NF={art['w_ffe_int'].size}, "
      f"ND={art['w_dfe_int'].size}, NL={art['levels_out'].size})")

# --- 2. the back end with its CDR, closed through the sampler ---
# a wandering Rx clock and a fast loop, with clamp and pipeline latency, so
# every branch of the loop filter, many PI codes and the integer LMS are exercised
loop_cfg = dataclasses.replace(
    cfg, numeric=NumericConfig(mode="fixed"),
    rx=dataclasses.replace(
        cfg.rx, clock=ClockConfig(sj_ui=0.12, sj_freq=cfg.symbol_rate / 1500),
        cdr=dataclasses.replace(cfg.rx.cdr, kp_shift=3, ki_shift=7, clamp=0.02,
                                loop_latency_symbols=32),
        # larger LMS steps and a short start-up, so the window holds CDR
        # settling, data-aided training and decision-directed adaptation
        ffe=dataclasses.replace(cfg.rx.ffe, mu=2e-3),
        dfe=dataclasses.replace(cfg.rx.dfe, mu=2e-3)),
    sim=dataclasses.replace(cfg.sim, n_symbols=6000, cdr_settle=500, train_symbols=1000))
res = run_time_link(loop_cfg)
fx = res.extras["fixed"]
rec = fx["record"]
art = loop_artifacts(fx["loop"], rec["xin"][:3000], rec["ref"][:3000])
dump_loop_vectors(out, art)

moved = int(np.abs(art["w_ffe_end"] - art["w_ffe_int"]).max())
print(f"wrote loop vectors to {out}  (N={art['xin'].size}, PI codes "
      f"{int(art['pi'].min())}..{int(art['pi'].max())}, FFE weights moved up to {moved} LSB, "
      f"SER {res.ser:.2e})")

# offset / gain / skew mismatch with the background calibration on, fast steps so
# the window holds the registers moving (the RTL checks them at the end too)
cal_rx = dataclasses.replace(loop_cfg.rx, adc=dataclasses.replace(
    loop_cfg.rx.adc, offset_sigma=0.01, gain_sigma=0.03, skew_sigma_ui=0.04,
    cal=AdcCalConfig("background", 2.0 ** -6, 2.0 ** -6, 2.0 ** -6)))
cal_res = run_time_link(dataclasses.replace(loop_cfg, rx=cal_rx))
cal_fx = cal_res.extras["fixed"]
cal_art = loop_artifacts(cal_fx["loop"], cal_fx["record"]["xin"][:3000],
                         cal_fx["record"]["ref"][:3000])
dump_loop_vectors(out / "cal", cal_art)
print(f"wrote calibrated loop vectors to {out / 'cal'}  (gains "
      f"{int(cal_art['cal_end'][16:32].min())}..{int(cal_art['cal_end'][16:32].max())} "
      f"/ 2^{int(cal_fx['loop'].cal[2])}, SER {cal_res.ser:.2e})")

for sub, extra in (("pr", dict(pr=PrConfig(target=(1.0, 0.6, 0.2), adapt="lms", mu=1e-2))),
                   ("pre", dict(pr=PrConfig(target=(1.0, 1.0)), precode=True))):
    pr_cfg = dataclasses.replace(
        loop_cfg, rx=dataclasses.replace(loop_cfg.rx, mlsd=MlsdConfig(kind="viterbi", memory=1)),
        **extra)
    pr_res = run_time_link(pr_cfg)
    pr_fx = pr_res.extras["fixed"]
    pr_art = loop_artifacts(pr_fx["loop"], pr_fx["record"]["xin"][:3000],
                            pr_fx["record"]["ref"][:3000])
    dump_loop_vectors(out / sub, pr_art)
    print(f"wrote PR loop vectors to {out / sub}  (mode {int(pr_fx['loop'].lp[11])}, "
          f"a/b {pr_art['ab'].tolist()} -> {pr_art['ab_end'].tolist()}, SER {pr_res.ser:.2e})")

# --- 3. the sliding-detector MLSD on a strong residual postcursor ---
rng = np.random.default_rng(11)
levels = np.array([-600, -200, 200, 600], dtype=np.int64)
fbt = (int(round(0.45 * 256)) * levels + 128) >> 8
sym = rng.integers(0, 4, 4000)
v = levels[sym].copy()
v[1:] += fbt[sym[:-1]]
v += np.round(rng.normal(scale=110.0, size=v.size)).astype(np.int64)
dec0 = np.zeros(v.size, dtype=np.int64)
for k in range(v.size):
    dec0[k] = int(np.argmin(np.abs(v[k] - (fbt[dec0[k - 1]] if k else 0) - levels)))
fsd = FixedSliding(rp=int(round(0.45 * 256)), rp_fl=8, fbt=fbt, seq_len=4, margin=300,
                   sq_shift=4, metric_max=(1 << 18) - 1)
dec_m = run_fixed_sliding(fsd, v, dec0, levels)
dump_mlsd_vectors(out, fsd, v, dec0, levels, dec_m)
print(f"wrote MLSD vectors to {out}  (N={v.size}, flips {int(np.sum(dec_m != dec0))}, "
      f"slicer errors {int(np.sum(dec0 != sym))} -> {int(np.sum(dec_m != sym))})")

# --- 4. the Viterbi MLSD on a two-postcursor channel ---
rng = np.random.default_rng(12)
c_int = np.array([256, 154, 51], dtype=np.int64)               # 1 + 0.6D + 0.2D^2
sym = rng.integers(0, 4, 3000)
y = np.zeros(sym.size, dtype=np.int64)
for i, c in enumerate(c_int):
    y[i:] += (c * levels[sym[: sym.size - i]] + 128) >> 8
y += np.round(rng.normal(scale=150.0, size=y.size)).astype(np.int64)
fvd = FixedViterbi(cursors=c_int, c_fl=8, expected=expected_table(c_int, levels, 8),
                   sq_shift=3, metric_max=(1 << 20) - 1)
dec_v = run_fixed_viterbi(fvd, y, 4)
dump_viterbi_vectors(out, fvd, y, 4, dec_v)
print(f"wrote Viterbi vectors to {out}  (N={y.size}, {fvd.expected.shape[0]} states, "
      f"errors {int(np.sum(dec_v != sym))})")
