#!/usr/bin/env bash
# Generate golden vectors, build the SV models + testbenches, and run the
# bit-exact lockstep checks: the FFE + DFE + slicer datapath (whole-block and
# streaming / synthesizable forms), the whole back
# end with its CDR loop, the sliding-detector MLSD, the Viterbi MLSD and its
# reduced-state (DFSE) form.
# Requires iverilog (apt install iverilog).
#
#   bash rtl/run_lockstep.sh             # vectors + builds in a fresh temp dir
#   bash rtl/run_lockstep.sh some/dir    # keep the vectors there (relative to rtl/)
#
# The default used to be rtl/vectors, which rewrote the committed snapshot on every
# local run; refresh that deliberately with `python rtl/gen_vectors.py`. The .vvp
# builds go to a private temp dir so two runs never overwrite each other's binaries.
set -euo pipefail
cd "$(dirname "$0")"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
VEC="${1:-$WORK/vectors}"
python gen_vectors.py "$VEC"
for d in "$VEC" "$VEC/pr" "$VEC/pr4" "$VEC/pre"; do   # delta, 1 + aD + bD^2, + cD^3, precoded 1 + D
    iverilog -g2012 -o "$WORK/ffe.vvp" -I "$d" ffe_dfe_datapath.sv tb_ffe_dfe.sv
    vvp "$WORK/ffe.vvp" +vecdir="$d"
    # the streaming (synthesizable) form, at the storage width and at the numeric widths
    # (ADC Q8.0, FFE / DFE weights Q10.8, 12-bit levels)
    iverilog -g2012 -o "$WORK/stream.vvp" -I "$d" ffe_dfe_stream.sv tb_ffe_dfe_stream.sv
    vvp "$WORK/stream.vvp" +vecdir="$d"
    iverilog -g2012 -Ptb_ffe_dfe_stream.CODE_W=8 -Ptb_ffe_dfe_stream.WF_W=10 \
        -Ptb_ffe_dfe_stream.WD_W=10 -Ptb_ffe_dfe_stream.LV_W=12 -Ptb_ffe_dfe_stream.PR_W=10 \
        -o "$WORK/stream_n.vvp" -I "$d" ffe_dfe_stream.sv tb_ffe_dfe_stream.sv
    vvp "$WORK/stream_n.vvp" +vecdir="$d"
    # the decision loop unrolled one level (LOOKAHEAD=1), streamed with idle clocks (GAPS=1)
    iverilog -g2012 -Ptb_ffe_dfe_stream.CODE_W=8 -Ptb_ffe_dfe_stream.WF_W=10 \
        -Ptb_ffe_dfe_stream.WD_W=10 -Ptb_ffe_dfe_stream.LV_W=12 -Ptb_ffe_dfe_stream.PR_W=10 \
        -Ptb_ffe_dfe_stream.LOOKAHEAD=1 -Ptb_ffe_dfe_stream.GAPS=1 \
        -o "$WORK/stream_la.vvp" -I "$d" ffe_dfe_stream.sv tb_ffe_dfe_stream.sv
    vvp "$WORK/stream_la.vvp" +vecdir="$d"
done
for d in "$VEC" "$VEC/pr" "$VEC/pr4" "$VEC/pre" "$VEC/cal"; do   # delta, 1 + aD + bD^2, + cD^3, precoded 1 + D, ADC calibration
    iverilog -g2012 -o "$WORK/loop.vvp" -I "$d" adc_dsp_loop.sv tb_adc_dsp_loop.sv
    vvp "$WORK/loop.vvp" +vecdir="$d"
done
iverilog -g2012 -o "$WORK/mlsd.vvp" -I "$VEC" sliding_mlsd.sv tb_sliding_mlsd.sv
vvp "$WORK/mlsd.vvp" +vecdir="$VEC"
iverilog -g2012 -o "$WORK/vit.vvp" -I "$VEC" viterbi_mlsd.sv tb_viterbi_mlsd.sv
vvp "$WORK/vit.vvp" +vecdir="$VEC"
iverilog -g2012 -o "$WORK/dfse.vvp" -I "$VEC" dfse_mlsd.sv tb_dfse_mlsd.sv
vvp "$WORK/dfse.vvp" +vecdir="$VEC"
