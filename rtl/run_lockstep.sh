#!/usr/bin/env bash
# Generate golden vectors, build the SV models + testbenches, and run the
# bit-exact lockstep checks: the FFE + DFE + slicer datapath, then the whole
# back end with its CDR loop. Requires iverilog (apt install iverilog).
set -euo pipefail
cd "$(dirname "$0")"

VEC="${1:-vectors}"
python gen_vectors.py "$VEC"
iverilog -g2012 -o /tmp/ffe_lockstep.vvp -I "$VEC" ffe_dfe_datapath.sv tb_ffe_dfe.sv
vvp /tmp/ffe_lockstep.vvp +vecdir="$VEC"
iverilog -g2012 -o /tmp/loop_lockstep.vvp -I "$VEC" adc_dsp_loop.sv tb_adc_dsp_loop.sv
vvp /tmp/loop_lockstep.vvp +vecdir="$VEC"
