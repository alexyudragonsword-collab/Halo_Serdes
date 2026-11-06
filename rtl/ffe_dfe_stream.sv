// Streaming FFE + DFE + slicer — the synthesizable form of ffe_dfe_datapath.sv.
//
// Same arithmetic as ffe_dfe_datapath.sv / halo_serdes.dsp.fixed_datapath._ffe_dfe_pr_fixed_py
// (bit-exact against the same golden vectors, tb_ffe_dfe_stream.sv), but one ADC word per
// clock through ports instead of whole-block memories, and every word sized by a parameter
// instead of longint, so synthesis sees real multipliers and adders:
//
//   stage 1a (registered): the NF FFE products of a shift register of the last NF codes
//   stage 1b (registered): their sum, rounding add, arithmetic shift, saturation to OUT_BITS
//   stage 2  (registered): DFE feedback (and the PR_MODE 1 controlled cursors), subtract,
//                          saturate, slicer; the decision goes back into the history in the
//                          same cycle
//
// The decision loop is one cycle and holds no multiplier (it is the critical path: select the
// precomputed products by the previous decisions, add, round, saturate, slice; unrolling it
// over the previous decision is the next step for speed). Every DFE / controlled-cursor term
// is weight x level, and those products are precomputed for every level and registered
// (weights and levels are static loaded registers; after a change of one, the products follow
// one clock later), so the loop selects a product by the previous decision, adds, rounds and
// slices. Symbol s is the output of code k = s + N_PRE, so the first N_PRE codes produce
// nothing; codes before the first one count as zero (the golden's j >= 0 test) and the
// history before the first decision is empty (its s - t >= 0 test). Outputs: v (slicer input,
// OUT_BITS signed) and dec (decision index), with out_valid, three clocks after the code.
//
// Widths: CODE_W (ADC word), WF_W / WD_W (FFE / DFE weights), LV_W (levels), PR_W (PR words)
// are signed widths. Each product is formed at its natural width (a * b in a variable of
// exactly width(a) + width(b), so a 10 x 8 multiply stays 10 x 8) and the sums are sized to
// hold every term; a word that does not fit its width breaks bit-exactness, the sums never do.

module ffe_dfe_stream #(
    parameter int NF = 15, ND = 1, NL = 4, N_PRE = 4,
    parameter int FFE_SHIFT = 4, DFE_SHIFT = 8, OUT_BITS = 12, DO_ROUND = 1,
    parameter int PR_MODE = 0, PR_NT = 1, PR_FL = 0, NPL = 1,
    parameter int CODE_W = 32, WF_W = 32, WD_W = 32, LV_W = 32, PR_W = 32
) (
    input  logic                          clk,
    input  logic                          rst,
    input  logic                          in_valid,
    input  logic signed [CODE_W-1:0]      code,
    input  logic        [NF*WF_W-1:0]     w_ffe,      // tap i at [i*WF_W +: WF_W]
    input  logic        [ND*WD_W-1:0]     w_dfe,
    input  logic        [NL*LV_W-1:0]     levels,
    input  logic        [3*PR_W-1:0]      pr_abc,
    input  logic        [NPL*LV_W-1:0]    pr_lv,
    output logic                          out_valid,
    output logic signed [OUT_BITS-1:0]    v,
    output logic        [31:0]            dec
);
    localparam int GAP  = (PR_MODE > 0) ? PR_NT : 1;
    localparam int HD   = GAP + ND - 1;                          // deepest DFE tap
    localparam int HP   = (PR_NT > 1) ? PR_NT - 1 : 1;           // deepest controlled cursor
    localparam int H    = (HD > HP) ? HD : HP;
    localparam int LB   = (NL > 1) ? $clog2(NL) : 1;             // decision index width
    localparam int FP_W = CODE_W + WF_W;                         // one FFE product
    localparam int FA_W = FP_W + $clog2(NF) + 1;                 // FFE sum (+ rounding bit)
    localparam int DP_W = WD_W + LV_W;                           // one DFE product
    localparam int DA_W = DP_W + $clog2(ND) + 1;
    localparam int CP_W = PR_W + LV_W;                           // one controlled-cursor product
    localparam int CA_W = CP_W + 3;
    // the decision loop at the widths its values need (after each shift / saturation)
    localparam int FB_W = (DA_W - DFE_SHIFT > 2) ? DA_W - DFE_SHIFT : 2;   // fb after the shift
    localparam int VV_W = ((OUT_BITS > FB_W) ? OUT_BITS : FB_W) + 1;      // ffe - fb
    localparam int CT_W = (CA_W - PR_FL > 2) ? CA_W - PR_FL : 2;          // ctl after the shift
    localparam int U_W  = ((OUT_BITS > CT_W) ? OUT_BITS : CT_W) + 1;      // v - ctl
    localparam int D_W  = ((U_W > LV_W) ? U_W : LV_W) + 1;                // |u - level|
    localparam logic signed [VV_W-1:0] V_HI = VV_W'((1 << (OUT_BITS - 1)) - 1);
    localparam logic signed [VV_W-1:0] V_LO = -VV_W'(1 << (OUT_BITS - 1));

    // ---- stage 1: FFE ------------------------------------------------------------------
    logic signed [CODE_W-1:0] win   [NF];    // win[i] = codes[k - i]
    logic signed [CODE_W-1:0] win_n [NF];    // after this clock's code is shifted in
    logic signed [FP_W-1:0]   fprod [NF];    // stage 1a register
    logic signed [FA_W-1:0]   fsum;
    logic signed [OUT_BITS-1:0] ffe_q;       // stage 1b register
    logic        [31:0]       cnt;           // codes seen
    logic                     v0, v1;

    always_comb begin
        win_n[0] = code;
        for (int i = 1; i < NF; i++) win_n[i] = win[i-1];
        fsum = '0;
        for (int i = 0; i < NF; i++) fsum += FA_W'(fprod[i]);
        if (DO_ROUND == 1 && FFE_SHIFT > 0) fsum += FA_W'(1) <<< (FFE_SHIFT - 1);
        fsum = fsum >>> FFE_SHIFT;
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < NF; i++) win[i] <= '0;
            cnt <= '0;
            v0  <= 1'b0;
            v1  <= 1'b0;
        end else begin
            v0 <= 1'b0;
            if (in_valid) begin
                for (int i = 0; i < NF; i++) begin
                    win[i]   <= win_n[i];
                    fprod[i] <= $signed(w_ffe[i*WF_W +: WF_W]) * win_n[i];
                end
                cnt <= cnt + 1;
                v0  <= (cnt >= N_PRE);                       // this code is k = cnt >= N_PRE
            end
            v1 <= v0;
            if (v0) begin
                if (fsum > FA_W'((1 << (OUT_BITS - 1)) - 1))  ffe_q <= OUT_BITS'((1 << (OUT_BITS - 1)) - 1);
                else if (fsum < -FA_W'(1 << (OUT_BITS - 1)))  ffe_q <= OUT_BITS'(-(1 << (OUT_BITS - 1)));
                else                                          ffe_q <= OUT_BITS'(fsum);
            end
        end
    end

    // ---- stage 2: DFE / controlled cursors / slicer ------------------------------------
    logic [LB-1:0] xl_h [1:H];                // xl_h[t] = x[s - t]
    logic          vh   [1:H];                // that history entry exists
    logic [LB-1:0] dec_p;                     // dec[s - 1] (PR_MODE 2)
    logic signed [LV_W-1:0] lv [NL];
    logic signed [DP_W-1:0] dcand [ND][NL];   // w_dfe[d] * levels[m], registered
    logic signed [CP_W-1:0] ccand [3][NL];    // pr_abc[t] * levels[m], registered
    logic signed [DP_W-1:0] dprod [ND];
    logic signed [CP_W-1:0] cprod [3];

    always_ff @(posedge clk)
        for (int m = 0; m < NL; m++) begin
            for (int d = 0; d < ND; d++)
                dcand[d][m] <= $signed(w_dfe[d*WD_W +: WD_W]) * $signed(levels[m*LV_W +: LV_W]);
            for (int t = 0; t < 3; t++)
                ccand[t][m] <= $signed(pr_abc[t*PR_W +: PR_W]) * $signed(levels[m*LV_W +: LV_W]);
        end
    logic signed [DA_W-1:0] fb;
    logic signed [CA_W-1:0] ctl;
    logic signed [VV_W-1:0] vv;
    logic signed [OUT_BITS-1:0] vs;           // v, saturated
    logic signed [U_W-1:0]  u;
    logic signed [D_W-1:0]  dd, bd, lv_m;
    logic [LB-1:0] best, xn, dn;
    logic [LB:0]   q;                         // composite level index (< 2 NL)
    logic signed [LB+2:0] xc, dm;

    always_comb begin
        for (int m = 0; m < NL; m++) lv[m] = $signed(levels[m*LV_W +: LV_W]);
        // DFE feedback from decisions s - GAP - d
        // (if, not ?: -- an unsigned '0 arm would make the whole conditional unsigned)
        for (int d = 0; d < ND; d++) begin
            dprod[d] = '0;
            if (vh[GAP + d]) dprod[d] = dcand[d][xl_h[GAP + d]];
        end
        fb = '0;
        for (int d = 0; d < ND; d++) fb += DA_W'(dprod[d]);
        if (DO_ROUND == 1 && DFE_SHIFT > 0) fb += DA_W'(1) <<< (DFE_SHIFT - 1);
        fb = fb >>> DFE_SHIFT;
        vv = VV_W'(ffe_q) - VV_W'(FB_W'(fb));
        if (vv > V_HI) vv = V_HI;
        else if (vv < V_LO) vv = V_LO;
        vs = OUT_BITS'(vv);
        // controlled cursors (PR_MODE 1)
        for (int t = 0; t < 3; t++) begin
            cprod[t] = '0;
            if (PR_MODE == 1 && t + 1 < PR_NT)
                if (vh[t + 1]) cprod[t] = ccand[t][xl_h[t + 1]];
        end
        ctl = '0;
        if (PR_MODE == 1) begin
            for (int t = 0; t < 3; t++) ctl += CA_W'(cprod[t]);
            if (DO_ROUND == 1 && PR_FL > 0) ctl += CA_W'(1) <<< (PR_FL - 1);
            ctl = ctl >>> PR_FL;
        end
        best = '0; xn = '0; dn = '0; q = '0; xc = '0; dm = '0; u = '0;
        lv_m = D_W'($signed(pr_lv[0 +: LV_W]));
        bd = (D_W'(vs) > lv_m) ? (D_W'(vs) - lv_m) : (lv_m - D_W'(vs));
        if (PR_MODE == 2) begin
            // composite slicer, precoded 1 + D decode: dec = (q - dec[s-1]) mod NL,
            // x = clamp(q - x[s-1], 0, NL - 1); q - dec[s-1] lies in (-NL, 2 NL), so one
            // correction step is the whole modulo
            for (int m = 1; m < NPL; m++) begin
                lv_m = D_W'($signed(pr_lv[m*LV_W +: LV_W]));
                dd = (D_W'(vs) > lv_m) ? (D_W'(vs) - lv_m) : (lv_m - D_W'(vs));
                if (dd < bd) begin bd = dd; q = (LB+1)'(m); end
            end
            dm = $signed({2'b0, q});
            if (vh[1]) dm = dm - $signed({3'b0, dec_p});
            if (dm < 0) dm = dm + NL;
            else if (dm >= NL) dm = dm - NL;
            dn = LB'(dm);
            xc = $signed({2'b0, q});
            if (vh[1]) xc = xc - $signed({3'b0, xl_h[1]});
            if (xc < 0) xc = '0;
            else if (xc > NL - 1) xc = NL - 1;
            xn = LB'(xc);
        end else begin
            // nearest level, first minimum on ties
            u = U_W'(vs) - U_W'(CT_W'(ctl));
            bd = (D_W'(u) > D_W'(lv[0])) ? (D_W'(u) - D_W'(lv[0])) : (D_W'(lv[0]) - D_W'(u));
            for (int m = 1; m < NL; m++) begin
                dd = (D_W'(u) > D_W'(lv[m])) ? (D_W'(u) - D_W'(lv[m])) : (D_W'(lv[m]) - D_W'(u));
                if (dd < bd) begin bd = dd; best = LB'(m); end
            end
            dn = best;
            xn = best;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int t = 1; t <= H; t++) begin xl_h[t] <= '0; vh[t] <= 1'b0; end
            dec_p     <= '0;
            out_valid <= 1'b0;
        end else begin
            out_valid <= v1;
            if (v1) begin
                v       <= vs;
                dec     <= 32'(dn);
                dec_p   <= dn;
                xl_h[1] <= xn;
                vh[1]   <= 1'b1;
                for (int t = 2; t <= H; t++) begin xl_h[t] <= xl_h[t-1]; vh[t] <= vh[t-1]; end
            end
        end
    end
endmodule
