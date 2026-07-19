"""Phase 6 deliverable: float vs fixed datapath + RTL golden vectors.

Runs the 106.25 GBd ADC link in float, captures the ADC codes, then replays
the FFE+DFE+slicer datapath bit-true at several weight word lengths:
- the BER/mismatch wall vs word length (fixed-point design guidance);
- default Q formats are DragonPHY2's silicon-proven widths (10b weights);
- dumps RTL testbench lockstep vectors (codes/weights/decisions + params).
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    NumericConfig, QFormat, RxConfig, SimConfig, TxConfig,
)
from halo_serdes.dsp.fixed_datapath import dump_vectors, run_fixed_datapath  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402

OUT = REPO / "examples" / "output"
OUT.mkdir(exist_ok=True)

cfg = LinkConfig(
    modulation="pam4", symbol_rate=106.25e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.12, rdc=5.0,
                          r_skin=2.0e-3, loss_tangent=0.012, n_freq=8192),
    tx=TxConfig(swing=1.0, fir_taps=(-0.05, 1.0, -0.1), fir_n_pre=1),
    rx=RxConfig(arch="adc_dsp",
                ctle=CtleConfig(enable=True, peak_db=4.0),
                adc=AdcConfig(n_bits=8, n_lanes=16, fullscale=0.6),
                ffe=FfeConfig(n_pre=4, n_post=10, adapt="lms", mu=5e-5),
                dfe=DfeConfig(n_taps=1, adapt="lms", mu=5e-5),
                cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                noise_rms=0.0015),
    sim=SimConfig(n_symbols=120_000, seed=3, pattern="prbs13q"),
)

res = run_time_link(cfg)
print("== float reference run ==")
print(" ", res.summary())

# recover integer ADC codes from the captured (dequantized) samples
adc = res.extras["adc"]
q = res.extras["q_hist_head"]
codes = np.round(q / adc.q_step - 0.5).astype(np.int64)
levels = res.extras["levels"]
w_ffe = res.ffe_taps
w_dfe = res.dfe_taps
n_pre = cfg.rx.ffe.n_pre

# float-datapath reference decisions on the same code window
numeric_wide = NumericConfig(ffe_weight=QFormat(20, 16), dfe_weight=QFormat(20, 16))
dec_ref, _, _ = run_fixed_datapath(codes, w_ffe, w_dfe, levels, n_pre,
                                   numeric_wide, adc.cfg.fullscale, adc.cfg.n_bits)

print("== fixed-point replay: weight word-length sweep ==")
wls = [4, 5, 6, 7, 8, 10, 12]
mismatch = []
for wl in wls:
    numeric = NumericConfig(ffe_weight=QFormat(wl, wl - 2),
                            dfe_weight=QFormat(wl, wl - 2))
    dec_fx, _, art = run_fixed_datapath(codes, w_ffe, w_dfe, levels, n_pre,
                                        numeric, adc.cfg.fullscale, adc.cfg.n_bits)
    m = float(np.mean(dec_fx != dec_ref))
    mismatch.append(max(m, 1e-6))
    tag = " (DragonPHY silicon width)" if wl == 10 else ""
    print(f"  wl={wl:2d}: decision mismatch vs wide-word reference = {m:.2e}{tag}")

# RTL handoff vectors at the default (10b) format
numeric10 = NumericConfig()
dec10, _, art10 = run_fixed_datapath(codes, w_ffe, w_dfe, levels, n_pre,
                                     numeric10, adc.cfg.fullscale, adc.cfg.n_bits)
vec_dir = dump_vectors(OUT / "rtl_vectors", art10)
print(f"== RTL lockstep vector package: {vec_dir} ==")
for f in sorted(vec_dir.iterdir()):
    print(f"   {f.name}")

fig, ax = plt.subplots(figsize=(6, 4))
ax.semilogy(wls, mismatch, "o-")
ax.axvline(10, ls="--", c="gray", label="DragonPHY2 silicon width (10b)")
ax.set(xlabel="FFE/DFE weight word length [bits]",
       ylabel="decision mismatch vs wide-word ref",
       title="Fixed-point BER wall (106.25 GBd PAM4 datapath)")
ax.grid(True, alpha=0.3)
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "08_fixed_point.png", dpi=130)
print(f"wrote {OUT / '08_fixed_point.png'}")
