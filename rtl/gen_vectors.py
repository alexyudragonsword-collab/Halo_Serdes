"""Generate RTL lockstep vectors from small fixed-point runs.

1. FFE + DFE + slicer: runs a short ADC-based link, captures the ADC codes +
   adapted weights, replays the bit-true datapath (the Python golden), and
   dumps the stimulus/response vectors plus dims.svh.
2. The whole back end with its CDR: runs the same link with
   numeric.mode='fixed' (the bit-true loop closes through the sampler), with
   a wandering Rx clock so the phase interpolator has to move, and dumps the
   recorded ADC words, PI codes, slicer values and decisions plus
   loop_dims.svh (dsp/fixed_loop.dump_loop_vectors).

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
    AdcConfig, CdrConfig, ChannelConfig, ClockConfig, CtleConfig, DfeConfig,
    FfeConfig, NumericConfig, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.dsp.fixed_datapath import (  # noqa: E402
    dump_sv_package, dump_vectors, run_fixed_datapath,
)
from halo_serdes.dsp.fixed_loop import dump_loop_vectors, loop_artifacts  # noqa: E402
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
# every branch of the loop filter and many PI codes are exercised
loop_cfg = dataclasses.replace(
    cfg, numeric=NumericConfig(mode="fixed"),
    rx=dataclasses.replace(
        cfg.rx, clock=ClockConfig(sj_ui=0.12, sj_freq=cfg.symbol_rate / 1500),
        cdr=dataclasses.replace(cfg.rx.cdr, kp_shift=3, ki_shift=7, clamp=0.02,
                                loop_latency_symbols=32)),
    sim=dataclasses.replace(cfg.sim, n_symbols=6000))
res = run_time_link(loop_cfg)
fx = res.extras["fixed"]
rec = fx["record"]
art = loop_artifacts(fx["loop"], {k: (v[:3000] if k in ("xin", "pi") else v) for k, v in rec.items()}
                     | {"dec": rec["dec"][:3000 - fx["loop"].n_pre],
                        "v_out": rec["v_out"][:3000 - fx["loop"].n_pre],
                        "n_dec": 3000 - fx["loop"].n_pre})
dump_loop_vectors(out, art)
print(f"wrote loop vectors to {out}  (N={art['xin'].size}, PI codes "
      f"{int(art['pi'].min())}..{int(art['pi'].max())}, SER {res.ser:.2e})")
