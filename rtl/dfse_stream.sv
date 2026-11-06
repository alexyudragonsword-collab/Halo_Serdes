// Streaming reduced-state Viterbi (DFSE) — the synthesizable form of dfse_mlsd.sv.
//
// Same arithmetic as dfse_mlsd.sv / halo_serdes.dsp.fixed_viterbi._dfse_fixed_py (bit-exact
// against the same golden vectors, tb_dfse_stream.sv), one slicer word per clock through
// ports, every word sized by a parameter instead of longint. As viterbi_stream.sv, except that
// every state carries its survivor's tail (the T decisions older than the state, newest
// first; level 0 at the start) and the expected word of a branch is
//   ((acc[p][m] + sum_j fbt[j][tail[p][j]]) + half) >>> C_FL
// so it depends on the tails, which are loop state: the branch metric (and its squarer) is
// inside the one-cycle add-compare-select, which is what per-survivor processing costs. The
// winner of a state takes the previous state's tail shifted by the oldest digit of p. Survivor
// memory port and the traceback over the whole block as in viterbi_stream.sv.
//
// Widths: Y_W (slicer word), ACC_W (acc words), FB_W (fbt words) signed; MW holds METRIC_MAX;
// KW the symbol index. A new block starts after rst.

module dfse_stream #(
    parameter int NL = 4, NS = 4, K = 1, T = 2, C_FL = 8, SQ_SHIFT = 3,
    parameter int METRIC_MAX = 1048575,
    parameter int Y_W = 32, ACC_W = 32, FB_W = 32, KW = 16,
    parameter int SB = (NS > 1) ? $clog2(NS) : 1,               // state index width
    parameter int TW = (T > 0) ? T : 1                           // tail storage (no 0-size)
) (
    input  logic                       clk,
    input  logic                       rst,
    input  logic                       in_valid,
    input  logic                       in_last,     // with in_valid: the block's last symbol
    input  logic signed [Y_W-1:0]      y,
    input  logic        [NS*NL*ACC_W-1:0] acc,      // acc[p][m] at [(p*NL+m)*ACC_W +: ACC_W]
    input  logic        [TW*NL*FB_W-1:0]  fbt,      // fbt[j][l] at [(j*NL+l)*FB_W +: FB_W]
    output logic                       bp_we,
    output logic        [KW-1:0]       bp_waddr,
    output logic        [NS*SB-1:0]    bp_wdata,
    output logic                       bp_re,       // read request: data next clock
    output logic        [KW-1:0]       bp_raddr,
    input  logic        [NS*SB-1:0]    bp_rdata,
    output logic                       out_valid,
    output logic        [KW-1:0]       out_k,
    output logic        [31:0]         dec
);
    localparam int DB  = (NL > 1) ? $clog2(NL) : 1;
    localparam int MW  = $clog2(METRIC_MAX + 1);
    localparam int A_W = ((ACC_W > FB_W) ? ACC_W : FB_W) + $clog2(T + 2) + 1;  // acc + fb + half
    localparam int X_W = A_W;                                    // expected word after the shift
    localparam int E_W = ((Y_W > X_W) ? Y_W : X_W) + 1;
    localparam int Q_W = 2 * E_W;
    localparam int C_W = ((Q_W > MW) ? Q_W : MW) + 1;
    localparam logic [C_W-1:0] MMAX = C_W'(METRIC_MAX);
    localparam int TOP = NL ** (K > 0 ? K - 1 : 0);              // oldest digit of a state
    localparam logic signed [A_W-1:0] HALF = (C_FL > 0) ? A_W'(1) <<< (C_FL - 1) : '0;

    logic signed [Y_W-1:0] y_r;
    logic                  va, la;
    always_ff @(posedge clk) begin
        if (rst) begin va <= 1'b0; la <= 1'b0; end
        else begin
            va <= in_valid;
            la <= in_valid && in_last;
            if (in_valid) y_r <= y;
        end
    end

    // ---- add-compare-select with per-survivor feedback -------------------------------
    logic [MW-1:0]  metric  [NS];
    logic           valid   [NS];
    logic [DB-1:0]  tail    [NS][TW];
    logic [C_W-1:0] nmetric [NS];
    logic           nvalid  [NS];
    logic [SB-1:0]  nbp     [NS];
    logic [DB-1:0]  ntail   [NS][TW];
    logic [MW-1:0]  lo;
    logic signed [A_W-1:0] fb, ex;
    logic signed [E_W-1:0] e;
    logic [Q_W-1:0] bm;
    logic [C_W-1:0] cand;

    always_comb begin
        for (int i = 0; i < NS; i++) begin
            nmetric[i] = '0; nvalid[i] = 1'b0; nbp[i] = '0;
            for (int j = 0; j < TW; j++) ntail[i][j] = tail[i][j];   // the golden keeps them
        end
        for (int p = 0; p < NS; p++) begin
            fb = '0;
            for (int j = 0; j < T; j++)
                fb += A_W'($signed(fbt[(j*NL + tail[p][j])*FB_W +: FB_W]));
            for (int m = 0; m < NL; m++) begin
                ex = (A_W'($signed(acc[(p*NL+m)*ACC_W +: ACC_W])) + fb + HALF) >>> C_FL;
                e = E_W'(y_r) - E_W'(ex);
                bm = Q_W'(e * e) >> SQ_SHIFT;                    // e * e >= 0
                cand = C_W'(metric[p]) + C_W'(bm);
                if (cand > MMAX) cand = MMAX;
                if (valid[p] && (!nvalid[(p*NL+m) % NS] || cand < nmetric[(p*NL+m) % NS])) begin
                    nmetric[(p*NL+m) % NS] = cand;
                    nvalid[(p*NL+m) % NS]  = 1'b1;
                    nbp[(p*NL+m) % NS]     = SB'(p);
                    if (T > 0) begin
                        ntail[(p*NL+m) % NS][0] = DB'(p / TOP);
                        for (int j = 1; j < T; j++) ntail[(p*NL+m) % NS][j] = tail[p][j-1];
                    end
                end
            end
        end
        lo = MW'(METRIC_MAX);
        for (int i = 0; i < NS; i++)
            if (nvalid[i] && MW'(nmetric[i]) < lo) lo = MW'(nmetric[i]);
        for (int i = 0; i < NS; i++) bp_wdata[i*SB +: SB] = nbp[i];
    end

    // ---- traceback ------------------------------------------------------------------
    typedef enum logic [1:0] {ACS, BEST, TRACE, DONE} st_t;
    st_t           st;
    logic [KW-1:0] k;                 // symbols seen (ACS); symbol being traced (TRACE)
    logic [SB-1:0] s, best;
    logic          rd_pending;        // bp_rdata holds symbol k's back-pointers

    always_comb begin
        best = '0;
        for (int i = 0; i < NS; i++)
            if (valid[i] && (!valid[best] || metric[i] < metric[best])) best = SB'(i);
    end

    assign bp_we    = (st == ACS) && va;
    assign bp_waddr = k;
    // read the last symbol's back-pointers when the best state is picked, then one symbol
    // further back every clock while tracing
    assign bp_re    = (st == BEST) || (st == TRACE && rd_pending && k != '0);
    assign bp_raddr = k - 1'b1;

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < NS; i++) begin
                metric[i] <= '0; valid[i] <= 1'b1;
                for (int j = 0; j < TW; j++) tail[i][j] <= '0;
            end
            st         <= ACS;
            k          <= '0;
            s          <= '0;
            rd_pending <= 1'b0;
            out_valid  <= 1'b0;
        end else begin
            out_valid <= 1'b0;
            case (st)
                ACS: if (va) begin
                    for (int i = 0; i < NS; i++) begin
                        valid[i]  <= nvalid[i];
                        metric[i] <= nvalid[i] ? MW'(nmetric[i]) - lo : '0;
                        for (int j = 0; j < TW; j++) tail[i][j] <= ntail[i][j];
                    end
                    k <= k + 1'b1;
                    if (la) st <= BEST;
                end
                BEST: begin                    // metrics of the last symbol are registered
                    s          <= best;
                    k          <= k - 1'b1;     // the last symbol
                    rd_pending <= 1'b1;
                    st         <= TRACE;
                end
                TRACE: if (rd_pending) begin
                    out_valid <= 1'b1;
                    out_k     <= k;
                    dec       <= 32'(s % NL);
                    s         <= bp_rdata[s*SB +: SB];
                    if (k == '0) begin
                        rd_pending <= 1'b0;
                        st         <= DONE;
                    end else k <= k - 1'b1;
                end
                default: ;
            endcase
        end
    end
endmodule
