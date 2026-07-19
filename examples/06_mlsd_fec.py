"""Phase 5 deliverable (1/2): MLSD gain and RS-FEC on a hard PAM4 link.

- runs the ADC-based RX on a channel with deliberate residual ISI (short FFE),
  then applies Viterbi MLSE over the residual cursors -> quantified gain;
- KP4/KR4 pre/post-FEC projection + real codec spot check.
"""

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from halo_serdes.config import LinkConfig  # noqa: E402
from halo_serdes.config.schema import (  # noqa: E402
    AdcConfig, CdrConfig, ChannelConfig, CtleConfig, DfeConfig, FfeConfig,
    RxConfig, SimConfig, TxConfig,
)
from halo_serdes.core.prbs import symbol_checker  # noqa: E402
from halo_serdes.dsp import viterbi_mlsd  # noqa: E402
from halo_serdes.engine import run_time_link  # noqa: E402
from halo_serdes.fec import bits_to_gf_symbols, pre_to_post_fec_ber, rs_kp4  # noqa: E402

# deliberately unequalized (1-tap FFE): the channel postcursors reach the
# slicer intact so the sequence detector has real ISI structure to exploit
cfg = LinkConfig(
    modulation="pam4", symbol_rate=106.25e9, osr=16,
    channel=ChannelConfig(kind="analytic", length_m=0.10, rdc=5.0,
                          r_skin=1.6e-3, loss_tangent=0.009, n_freq=8192),
    tx=TxConfig(swing=1.0),
    rx=RxConfig(arch="adc_dsp",
                ctle=CtleConfig(enable=False),
                adc=AdcConfig(n_bits=9, n_lanes=16),
                ffe=FfeConfig(n_pre=0, n_post=0, adapt="none"),
                dfe=DfeConfig(n_taps=0),   # leave postcursor ISI for the MLSD
                cdr=CdrConfig(kind="mueller_muller", kp_shift=7, ki_shift=15),
                noise_rms=0.010),
    sim=SimConfig(n_symbols=300_000, seed=9, pattern="prbs13q"),
)

res = run_time_link(cfg)
print("== baseline (FFE only, no DFE): residual ISI left on purpose ==")
print(" ", res.summary())

# residual cursors seen at the slicer: estimate from slicer stream vs decisions
levels = res.extras["levels"]
warm = res.extras["warmup"]

# residual channel estimate via LMMSE fit y[k] ~ sum_i c_i * L[ref[k-i]]
y = res.y_slicer
from halo_serdes.dsp.kernels import slice_nearest  # noqa: E402
from halo_serdes.engine.static_link import make_pattern  # noqa: E402

sym_all = make_pattern(cfg)
ref = sym_all[warm: warm + y.size]
ld = levels[ref]
cols = [ld] + [np.concatenate([np.zeros(i), ld[:-i]]) for i in (1, 2)]
coef, *_ = np.linalg.lstsq(np.vstack(cols).T, y, rcond=None)
print(f"  residual channel fit: main={coef[0]:.4f}, "
      f"h1={coef[1]:.4f} ({coef[1] / coef[0] * 100:.0f}%), "
      f"h2={coef[2]:.4f} ({coef[2] / coef[0] * 100:.0f}%)")

dec_sl = slice_nearest(y, levels)
dec_m1 = viterbi_mlsd(y.astype(np.float64), levels.astype(np.float64), coef[:2])
dec_m2 = viterbi_mlsd(y.astype(np.float64), levels.astype(np.float64), coef[:3])
ber_base = symbol_checker(ref, dec_sl)
ber_m1 = symbol_checker(ref, dec_m1)
ber_m2 = symbol_checker(ref, dec_m2)
print(f"  slicer          BER = {ber_base.ber:.3e}")
print(f"  MLSE (memory 1) BER = {ber_m1.ber:.3e}  "
      f"(gain {ber_base.ber / max(ber_m1.ber, 1e-12):.1f}x)")
print(f"  MLSE (memory 2) BER = {ber_m2.ber:.3e}  "
      f"(gain {ber_base.ber / max(ber_m2.ber, 1e-12):.1f}x)")

# ---------------------------------------------------------------- RS-FEC ---
print("== KP4 / KR4 projection from pre-FEC BER ==")
for pre in (1e-3, 3e-4, 1e-4, 2.4e-4):
    kp4 = pre_to_post_fec_ber(pre, "kp4")
    kr4 = pre_to_post_fec_ber(pre, "kr4")
    print(f"  pre-FEC {pre:.1e}  ->  post KP4 {kp4:.2e}   post KR4 {kr4:.2e}")

# real codec spot checks: within vs beyond correction capability
rs = rs_kp4()
rng = np.random.default_rng(1)
msg = rng.integers(0, 1024, size=514)
cw = rs.encode(msg)
for n_errs in (15, 40):
    bad = cw.copy()
    pos = rng.choice(544, size=n_errs, replace=False)
    bad[pos] ^= rng.integers(1, 1024, size=n_errs)
    dec, n_corr = rs.decode(bad)
    ok = np.array_equal(dec, msg)
    print(f"  codec: {n_errs:2d} symbol errors -> "
          f"{'corrected' if ok else 'uncorrectable (flagged)'}"
          f"  [t=15{', as expected' if ok == (n_errs <= 15) else ' — UNEXPECTED'}]")
p_sym = 1 - (1 - ber_m2.ber) ** 10
print(f"  note: this demo link's pre-FEC BER ({ber_m2.ber:.1e}) is far above the "
      f"KP4 waterfall (~2.4e-4) — expected symbol errors/frame = {544 * p_sym:.0f} >> t")
