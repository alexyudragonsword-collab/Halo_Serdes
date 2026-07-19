// Fixed-point FFE + DFE + slicer datapath — RTL golden-model reimplementation.
//
// An INDEPENDENT SystemVerilog implementation of
// halo_serdes.dsp.fixed_datapath._ffe_dfe_fixed_py, verified bit-exact against
// the Python golden by tb_ffe_dfe.sv. The point is to catch fixed-point spec
// ambiguities (round direction, arithmetic vs logical shift, saturation,
// slicer tie-breaking) before real RTL. Semantics follow DragonPHY comb_ffe:
// integer weight*code products, wide (longint) accumulator, rounding add +
// arithmetic right shift, saturation to OUT_BITS, nearest-level slicer.
//
// Python `>>` on a negative int is an arithmetic (floor) shift; SV `>>>` on a
// signed operand matches it on 2's-complement.
//
// The input/output memories are module-internal (not ports) because Icarus
// Verilog's unpacked-array-port support is weak; the testbench loads and reads
// them by hierarchical reference. Dimensions/shifts come from the generated
// dims.svh (single source with the Python model — see dump_sv_package).

module ffe_dfe_datapath;
    `include "dims.svh"          // localparam N, N_OUT, NF, ND, NL, ... CW

    logic signed [CW-1:0] codes  [N];
    logic signed [CW-1:0] w_ffe  [NF];
    logic signed [CW-1:0] w_dfe  [ND];
    logic signed [CW-1:0] levels [NL];
    logic        [31:0]   dec    [N_OUT];
    logic signed [CW-1:0] v_out  [N_OUT];

    localparam longint LIM_HI =  (longint'(1) << (OUT_BITS-1)) - 1;
    localparam longint LIM_LO = -(longint'(1) << (OUT_BITS-1));
    localparam longint HALF_F = (FFE_SHIFT > 0) ? (longint'(1) << (FFE_SHIFT-1)) : 0;
    localparam longint HALF_D = (DFE_SHIFT > 0) ? (longint'(1) << (DFE_SHIFT-1)) : 0;

    always_comb begin
        longint acc, fb, v, dd, bd;
        int k, j, jj, best;
        for (int s = 0; s < N_OUT; s++) begin
            k = s + N_PRE;
            // --- FFE MAC ---
            acc = 0;
            for (int i = 0; i < NF; i++) begin
                j = k - i;
                if (j >= 0 && j < N)
                    acc += longint'(w_ffe[i]) * longint'(codes[j]);
            end
            if (DO_ROUND == 1) acc += HALF_F;
            acc >>>= FFE_SHIFT;                 // arithmetic right shift
            if (acc > LIM_HI) acc = LIM_HI;
            else if (acc < LIM_LO) acc = LIM_LO;
            // --- DFE MAC (feedback from prior decisions) ---
            fb = 0;
            for (int d = 0; d < ND; d++) begin
                jj = s - 1 - d;
                if (jj >= 0)
                    fb += longint'(w_dfe[d]) * longint'(levels[dec[jj]]);
            end
            if (DO_ROUND == 1) fb += HALF_D;
            fb >>>= DFE_SHIFT;
            // --- subtract, saturate ---
            v = acc - fb;
            if (v > LIM_HI) v = LIM_HI;
            else if (v < LIM_LO) v = LIM_LO;
            v_out[s] = v;                       // longint -> signed[CW-1:0]
            // --- nearest-level slicer (first minimum on ties) ---
            best = 0;
            bd = (v > levels[0]) ? (v - levels[0]) : (levels[0] - v);
            for (int m = 1; m < NL; m++) begin
                dd = (v > levels[m]) ? (v - levels[m]) : (levels[m] - v);
                if (dd < bd) begin bd = dd; best = m; end
            end
            dec[s] = best;                      // int -> [31:0]
        end
    end
endmodule
