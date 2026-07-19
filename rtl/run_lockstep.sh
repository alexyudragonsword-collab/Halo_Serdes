#!/usr/bin/env bash
# Generate golden vectors, build the SV datapath + testbench, and run the
# bit-exact lockstep check. Requires iverilog (apt install iverilog).
set -euo pipefail
cd "$(dirname "$0")"

VEC="${1:-vectors}"
python gen_vectors.py "$VEC"
iverilog -g2012 -o /tmp/ffe_lockstep.vvp -I "$VEC" ffe_dfe_datapath.sv tb_ffe_dfe.sv
vvp /tmp/ffe_lockstep.vvp +vecdir="$VEC"
