#!/usr/bin/env bash
# Generate golden vectors, build the SV models + testbenches, and run the
# bit-exact lockstep checks: the FFE + DFE + slicer datapath, the whole back
# end with its CDR loop, the sliding-detector MLSD and the Viterbi MLSD.
# Requires iverilog (apt install iverilog).
set -euo pipefail
cd "$(dirname "$0")"

VEC="${1:-vectors}"
python gen_vectors.py "$VEC"
iverilog -g2012 -o /tmp/ffe_lockstep.vvp -I "$VEC" ffe_dfe_datapath.sv tb_ffe_dfe.sv
vvp /tmp/ffe_lockstep.vvp +vecdir="$VEC"
for d in "$VEC" "$VEC/pr" "$VEC/pr4" "$VEC/pre" "$VEC/cal"; do   # delta, 1 + aD + bD^2, + cD^3, precoded 1 + D, ADC calibration
    iverilog -g2012 -o /tmp/loop_lockstep.vvp -I "$d" adc_dsp_loop.sv tb_adc_dsp_loop.sv
    vvp /tmp/loop_lockstep.vvp +vecdir="$d"
done
iverilog -g2012 -o /tmp/mlsd_lockstep.vvp -I "$VEC" sliding_mlsd.sv tb_sliding_mlsd.sv
vvp /tmp/mlsd_lockstep.vvp +vecdir="$VEC"
iverilog -g2012 -o /tmp/vit_lockstep.vvp -I "$VEC" viterbi_mlsd.sv tb_viterbi_mlsd.sv
vvp /tmp/vit_lockstep.vvp +vecdir="$VEC"
