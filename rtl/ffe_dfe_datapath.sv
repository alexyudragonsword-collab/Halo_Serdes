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
//
// Partial response (PR_MODE, 2026-11-03), as _ffe_dfe_pr_fixed_py: 0 the
// delta target; 1 subtracts ctl = (a L[x[s-1]] + b L[x[s-2]] + c L[x[s-3]])
// >>> PR_FL (rounded) before the slicer, the DFE starting after the PR_NT - 1
// controlled cursors; 2 slices the composite levels pr_lv and decodes the
// precoded 1 + D symbol as (q - dec[s-1]) mod NL. x is the line-symbol
// estimate the DFE and the controlled cursors read (the decisions under 0, 1).

module ffe_dfe_datapath;
    `include "dims.svh"          // localparam N, N_OUT, NF, ND, NL, ... CW

    logic signed [CW-1:0] codes  [N];
    logic signed [CW-1:0] w_ffe  [NF];
    logic signed [CW-1:0] w_dfe  [ND];
    logic signed [CW-1:0] levels [NL];
    logic signed [CW-1:0] pr_abc [3];
    logic signed [CW-1:0] pr_lv  [NPL];
    logic        [31:0]   dec    [N_OUT];
    logic        [31:0]   xl     [N_OUT];
    logic signed [CW-1:0] v_out  [N_OUT];

    localparam longint LIM_HI =  (longint'(1) << (OUT_BITS-1)) - 1;
    localparam longint LIM_LO = -(longint'(1) << (OUT_BITS-1));
    localparam longint HALF_F = (FFE_SHIFT > 0) ? (longint'(1) << (FFE_SHIFT-1)) : 0;
    localparam longint HALF_D = (DFE_SHIFT > 0) ? (longint'(1) << (DFE_SHIFT-1)) : 0;
    localparam longint HALF_P = (PR_FL > 0) ? (longint'(1) << (PR_FL-1)) : 0;
    localparam int     GAP    = (PR_MODE > 0) ? PR_NT : 1;

    always_comb begin
        longint acc, fb, v, dd, bd, ctl, u;
        int k, j, jj, best, q, xc, pd;
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
                jj = s - GAP - d;
                if (jj >= 0)
                    fb += longint'(w_dfe[d]) * longint'(levels[xl[jj]]);
            end
            if (DO_ROUND == 1) fb += HALF_D;
            fb >>>= DFE_SHIFT;
            // --- subtract, saturate ---
            v = acc - fb;
            if (v > LIM_HI) v = LIM_HI;
            else if (v < LIM_LO) v = LIM_LO;
            v_out[s] = v;                       // longint -> signed[CW-1:0]
            // --- controlled cursors (PR_MODE 1) ---
            ctl = 0;
            if (PR_MODE == 1) begin
                for (int t = 1; t < PR_NT; t++)
                    if (s - t >= 0)
                        ctl += longint'(pr_abc[t-1]) * longint'(levels[xl[s-t]]);
                if (DO_ROUND == 1) ctl += HALF_P;
                ctl >>>= PR_FL;
            end
            if (PR_MODE == 2) begin
                // --- composite slicer, precoded 1 + D decode ---
                q = 0;
                bd = (v > pr_lv[0]) ? (v - pr_lv[0]) : (pr_lv[0] - v);
                for (int m = 1; m < NPL; m++) begin
                    dd = (v > pr_lv[m]) ? (v - pr_lv[m]) : (pr_lv[m] - v);
                    if (dd < bd) begin bd = dd; q = m; end
                end
                pd = (s >= 1) ? int'(dec[s-1]) : 0;
                dec[s] = ((q - pd) % NL + NL) % NL;   // Python's mod: non-negative
                xc = q - ((s >= 1) ? int'(xl[s-1]) : 0);
                if (xc < 0) xc = 0;
                else if (xc > NL - 1) xc = NL - 1;
                xl[s] = xc;
            end else begin
                // --- nearest-level slicer (first minimum on ties) ---
                u = v - ctl;
                best = 0;
                bd = (u > levels[0]) ? (u - levels[0]) : (levels[0] - u);
                for (int m = 1; m < NL; m++) begin
                    dd = (u > levels[m]) ? (u - levels[m]) : (levels[m] - u);
                    if (dd < bd) begin bd = dd; best = m; end
                end
                dec[s] = best;                  // int -> [31:0]
                xl[s] = best;
            end
        end
    end
endmodule
