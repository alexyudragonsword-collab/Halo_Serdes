# RTL golden-model lockstep

Closes the loop the fixed-point framework was built for: an **independent
SystemVerilog reimplementation** of the FFE + DFE + slicer datapath, verified
**bit-for-bit** against the Python golden model
(`halo_serdes.dsp.fixed_datapath`).

This is the DragonPHY2 lockstep methodology in reverse — the Python model is the
golden reference, and the RTL must match it exactly. It catches the fixed-point
spec ambiguities that bite real silicon: rounding direction, arithmetic vs
logical shift, saturation bounds, and slicer tie-breaking.

The second check covers the **whole back end with its clock recovery**: FFE +
DFE + slicer + Mueller-Muller phase detector + loop filter + phase register
(`adc_dsp_loop.sv` against `halo_serdes.dsp.fixed_loop`). The golden is a
closed-loop run (`numeric.mode: fixed`): the phase register picks the
phase-interpolator code, the sampler reads the waveform there, the ADC
quantises. The RTL replays the recorded ADC words, so the analog side it does
not model is already in its input, and must reproduce every PI code, slicer
value and decision.

## Files

| file | role |
|---|---|
| `ffe_dfe_datapath.sv` | the DUT — integer MAC, rounding add + arithmetic right shift (`>>>`), saturation to `OUT_BITS`, nearest-level slicer. Mirrors `_ffe_dfe_fixed_py`. |
| `tb_ffe_dfe.sv` | loads the golden vectors, runs the DUT, asserts every decision and slicer value matches; exits non-zero on any mismatch. |
| `gen_vectors.py` | runs a small ADC link, replays the bit-true datapath (the golden), and dumps `*.txt` vectors + `dims.svh`. |
| `adc_dsp_loop.sv` | the back end with its CDR loop — mirrors `fixed_loop._digital_step_py`; a task, not `always_comb` (the loop carries state per symbol, and Icarus 12 asserts on a combinational block this size). |
| `tb_adc_dsp_loop.sv` | loads the closed-loop record, runs the loop, asserts every PI code / slicer value / decision. |
| `run_lockstep.sh` | generate → `iverilog` compile → `vvp` run, both checks. |

## Run

```bash
sudo apt-get install -y iverilog      # Icarus Verilog
bash rtl/run_lockstep.sh
# -> LOCKSTEP PASS  n=1985  errors=0
# -> LOOP LOCKSTEP PASS  n=3000  errors=0
```

`tests/test_rtl_lockstep.py` runs the same flow (skipped if `iverilog` is
absent) and additionally corrupts one golden decision to prove the check is
non-vacuous. CI runs it in the `rtl-lockstep` job.

## Single source

`dims.svh` (module dimensions and fixed-point shifts) is generated from the
golden-model artifacts by `dump_sv_package`, so the RTL parameters and the
Python model cannot drift — the config is the single source for both. This is
the concrete form of the planned `to_sv_package()` hook.
