// Streaming sliding-detector MLSD — the synthesizable form of sliding_mlsd.sv.
//
// Same arithmetic as sliding_mlsd.sv / halo_serdes.dsp.fixed_mlsd._sliding_fixed_py
// (bit-exact against the same golden vectors, tb_sliding_stream.sv), one (slicer word,
// decision) per clock through ports, every word sized by a parameter instead of longint.
//
// The golden scans the block PASSES times; pass p visits k = 1 .. N - SEQ_LEN - 2 in order and
// may move dec[k] by one level. Step k changes only dec[k] (and the residuals of k and k + 1,
// which the golden keeps equal to v - L[dec] - fbt[previous dec]), so at every step the
// residuals are those of the current decisions. A pass is therefore a stream stage: a window
// of the last SEQ_LEN + 2 symbols; when symbol n arrives, step k = n - SEQ_LEN - 1 runs on the
// window (it reads up to n - 2; symbol n only proves k is not among the last SEQ_LEN + 2), the
// result goes back into the window, and symbol k - 1 (final for this pass) leaves. After the
// block's last symbol the window drains unchanged. PASSES stages in cascade are the PASSES
// passes. Output: (v, dec) in order with out_valid and out_last.
//
// Widths: V_W (slicer word) and LV_W (levels, feedback words) are signed.

module sliding_pass #(
    parameter int NL = 4, SEQ_LEN = 4, MARGIN = 300, SQ_SHIFT = 4, METRIC_MAX = 262143,
    parameter int V_W = 32, LV_W = 32,
    parameter int DB = (NL > 1) ? $clog2(NL) : 1               // decision width
) (
    input  logic                    clk,
    input  logic                    rst,
    input  logic                    in_valid,
    input  logic                    in_last,
    input  logic signed [V_W-1:0]   in_v,
    input  logic        [DB-1:0]    in_dec,
    input  logic        [NL*LV_W-1:0] levels,
    input  logic        [NL*LV_W-1:0] fbt,
    output logic                    out_valid,
    output logic                    out_last,
    output logic signed [V_W-1:0]   out_v,
    output logic        [DB-1:0]    out_dec
);
    localparam int WN  = SEQ_LEN + 2;                          // window: w[0] newest
    localparam int K   = SEQ_LEN;                              // step k sits at w[K]
    localparam int E_W = ((V_W > LV_W) ? V_W : LV_W) + 2;      // v - L - fbt
    localparam int Q_W = 2 * E_W;
    localparam int MW  = $clog2(METRIC_MAX + 1);
    localparam int S_W = ((Q_W > MW) ? Q_W : MW) + $clog2(SEQ_LEN + 1) + 2;   // sums, signed

    logic signed [V_W-1:0] wv [WN];
    logic [DB-1:0]         wd [WN];
    logic                  wq [WN];                            // entry holds a symbol
    logic                  wl [WN];                            // ... the block's last one
    logic                  drain;

    logic signed [LV_W-1:0] lv [NL];
    logic signed [LV_W-1:0] fb [NL];
    always_comb
        for (int i = 0; i < NL; i++) begin
            lv[i] = $signed(levels[i*LV_W +: LV_W]);
            fb[i] = $signed(fbt[i*LV_W +: LV_W]);
        end

    // step k = (index of w[0]) - SEQ_LEN on the window, before symbol n shifts in:
    // w[j] is symbol n - 1 - j, so k is w[K], k - 1 is w[K + 1], k + j is w[K - j]
    logic signed [E_W-1:0] r [SEQ_LEN];                        // residual of k + j
    logic signed [E_W-1:0] e0, e1;
    logic signed [S_W-1:0] base, best, sse, tail;
    logic [DB-1:0]         newd;
    logic                  step;
    int                    m;

    always_comb begin
        for (int j = 0; j < SEQ_LEN; j++)
            r[j] = E_W'(wv[K-j]) - E_W'(lv[wd[K-j]]) - E_W'(fb[wd[K-j+1]]);
        tail = '0;
        for (int j = 2; j < SEQ_LEN; j++) tail += S_W'((Q_W'(r[j] * r[j])) >> SQ_SHIFT);
        base = tail + S_W'((Q_W'(r[0] * r[0])) >> SQ_SHIFT) + S_W'((Q_W'(r[1] * r[1])) >> SQ_SHIFT);
        if (base > S_W'(METRIC_MAX)) base = S_W'(METRIC_MAX);
        best = base - S_W'(MARGIN);
        newd = wd[K];
        for (int d = -1; d <= 1; d += 2) begin
            m = int'(wd[K]) + d;
            e0 = '0; e1 = '0; sse = '0;
            if (m >= 0 && m < NL) begin
                e0 = E_W'(wv[K]) - E_W'(lv[m]) - E_W'(fb[wd[K+1]]);
                e1 = E_W'(wv[K-1]) - E_W'(lv[wd[K-1]]) - E_W'(fb[m]);
                sse = tail + S_W'((Q_W'(e0 * e0)) >> SQ_SHIFT) + S_W'((Q_W'(e1 * e1)) >> SQ_SHIFT);
                if (sse > S_W'(METRIC_MAX)) sse = S_W'(METRIC_MAX);
                if (sse < best) begin best = sse; newd = DB'(m); end
            end
        end
        // runs when symbol n arrives and k >= 1 (k - 1 is in the window)
        step = in_valid && wq[K+1];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < WN; i++) begin wq[i] <= 1'b0; wl[i] <= 1'b0; end
            drain     <= 1'b0;
            out_valid <= 1'b0;
            out_last  <= 1'b0;
        end else begin
            out_valid <= 1'b0;
            out_last  <= 1'b0;
            if (in_valid || drain) begin
                // the oldest symbol leaves (final for this pass)
                out_valid <= wq[WN-1];
                out_last  <= wl[WN-1];
                out_v     <= wv[WN-1];
                out_dec   <= wd[WN-1];
                for (int i = WN - 1; i > 0; i--) begin
                    wv[i] <= wv[i-1]; wq[i] <= wq[i-1]; wl[i] <= wl[i-1];
                    // k moves from w[K] to w[K+1], with the step's decision: one write per
                    // element (a second NBA to wd[K+1] after the loop is what Verilator 5.032
                    // mis-orders)
                    wd[i] <= (step && i == K + 1) ? newd : wd[i-1];
                end
                wv[0] <= in_v;
                wd[0] <= in_dec;
                wq[0] <= in_valid;
                wl[0] <= in_valid && in_last;
                if (in_valid && in_last) drain <= 1'b1;
                if (drain && !wq[WN-2]) drain <= 1'b0;        // the last one leaves now
            end
        end
    end
endmodule

module sliding_stream #(
    parameter int NL = 4, SEQ_LEN = 4, MARGIN = 300, SQ_SHIFT = 4, METRIC_MAX = 262143,
    parameter int PASSES = 2, V_W = 32, LV_W = 32
) (
    input  logic                    clk,
    input  logic                    rst,
    input  logic                    in_valid,
    input  logic                    in_last,
    input  logic signed [V_W-1:0]   v,
    input  logic        [31:0]      dec0,
    input  logic        [NL*LV_W-1:0] levels,
    input  logic        [NL*LV_W-1:0] fbt,
    output logic                    out_valid,
    output logic                    out_last,
    output logic        [31:0]      dec
);
    localparam int DB = (NL > 1) ? $clog2(NL) : 1;
    logic                  sv [PASSES+1];
    logic                  sl [PASSES+1];
    logic signed [V_W-1:0] sx [PASSES+1];
    logic [DB-1:0]         sd [PASSES+1];
    assign sv[0] = in_valid;
    assign sl[0] = in_last;
    assign sx[0] = v;
    assign sd[0] = DB'(dec0);
    for (genvar p = 0; p < PASSES; p++) begin : g_pass
        sliding_pass #(.NL(NL), .SEQ_LEN(SEQ_LEN), .MARGIN(MARGIN), .SQ_SHIFT(SQ_SHIFT),
                       .METRIC_MAX(METRIC_MAX), .V_W(V_W), .LV_W(LV_W), .DB(DB)) u (
            .clk(clk), .rst(rst), .in_valid(sv[p]), .in_last(sl[p]), .in_v(sx[p]),
            .in_dec(sd[p]), .levels(levels), .fbt(fbt), .out_valid(sv[p+1]),
            .out_last(sl[p+1]), .out_v(sx[p+1]), .out_dec(sd[p+1]));
    end
    assign out_valid = sv[PASSES];
    assign out_last  = sl[PASSES];
    assign dec       = 32'(sd[PASSES]);
endmodule
