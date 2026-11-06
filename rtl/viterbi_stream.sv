// Streaming Viterbi MLSD — the synthesizable form of viterbi_mlsd.sv.
//
// Same arithmetic as viterbi_mlsd.sv / halo_serdes.dsp.fixed_viterbi._viterbi_fixed_py
// (bit-exact against the same golden vectors, tb_viterbi_stream.sv), but one slicer word per
// clock through ports, every word sized by a parameter instead of longint:
//
//   branch metrics, registered: (y - expected[p][m])^2 >>> SQ_SHIFT for every (p, m); they
//     depend on y only, so they are computed a clock ahead and no squarer sits in the loop.
//   add-compare-select, one symbol per clock: for every previous state p (ascending) and new
//     symbol m (ascending) the candidate metric[p] + branch metric[p][m],
//     saturated at METRIC_MAX, goes to state (p * NL + m) % NS; a strictly smaller candidate
//     wins (so the first minimum in that order). Then every metric has the smallest one
//     subtracted (renormalise). The metrics and their valid bits are the only registers in
//     the loop.
//   survivors: the NS back-pointers of each symbol leave through a write port (bp_we,
//     bp_waddr = symbol index, bp_wdata = state ns at [ns*SB +: SB]) to a survivor memory
//     outside this module (an SRAM in hardware; the testbench's array here).
//   traceback over the whole block, as the golden: after the symbol marked in_last, the first
//     state with the smallest metric, then one decision per clock from the last symbol back to
//     the first through a synchronous read port (bp_re / bp_raddr, data on bp_rdata one clock
//     later). Outputs: dec = state % NL and its symbol index out_k, with out_valid, last
//     symbol first. A detector that cuts the traceback at a fixed depth would not be bit-exact
//     to this golden, so it is not done here.
//
// Widths: Y_W (slicer word) and EX_W (expected words) are signed; MW holds METRIC_MAX; KW the
// symbol index. A new block starts after rst.

module viterbi_stream #(
    parameter int NL = 4, NS = 16, SQ_SHIFT = 3, METRIC_MAX = 1048575,
    parameter int Y_W = 32, EX_W = 32, KW = 16,
    parameter int SB = (NS > 1) ? $clog2(NS) : 1                // state index width
) (
    input  logic                       clk,
    input  logic                       rst,
    input  logic                       in_valid,
    input  logic                       in_last,     // with in_valid: the block's last symbol
    input  logic signed [Y_W-1:0]      y,
    input  logic        [NS*NL*EX_W-1:0] expected,  // expected[p][m] at [(p*NL+m)*EX_W +: EX_W]
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
    localparam int MW  = $clog2(METRIC_MAX + 1);                // a metric (0 .. METRIC_MAX)
    localparam int E_W = ((Y_W > EX_W) ? Y_W : EX_W) + 1;       // y - expected
    localparam int Q_W = 2 * E_W;                               // its square
    localparam int C_W = ((Q_W > MW) ? Q_W : MW) + 1;           // metric + branch metric
    localparam logic [C_W-1:0] MMAX = C_W'(METRIC_MAX);

    // ---- branch metrics (registered; y only) ---------------------------------------------
    logic signed [E_W-1:0] e [NS*NL];
    logic [Q_W-1:0] bm_r [NS*NL];
    logic           va, la;               // bm_r holds a symbol / the block's last one
    always_comb
        for (int b = 0; b < NS * NL; b++)
            e[b] = E_W'(y) - E_W'($signed(expected[b*EX_W +: EX_W]));
    always_ff @(posedge clk) begin
        if (rst) begin va <= 1'b0; la <= 1'b0; end
        else begin
            va <= in_valid;
            la <= in_valid && in_last;
            if (in_valid)
                for (int b = 0; b < NS * NL; b++) bm_r[b] <= Q_W'(e[b] * e[b]) >> SQ_SHIFT;  // >= 0
        end
    end

    // ---- add-compare-select -----------------------------------------------------------
    logic [MW-1:0]  metric  [NS];
    logic           valid   [NS];
    logic [C_W-1:0] nmetric [NS];
    logic           nvalid  [NS];
    logic [SB-1:0]  nbp     [NS];
    logic [MW-1:0]  lo;
    logic [C_W-1:0] cand;

    always_comb begin
        for (int i = 0; i < NS; i++) begin nmetric[i] = '0; nvalid[i] = 1'b0; nbp[i] = '0; end
        for (int p = 0; p < NS; p++)
            for (int m = 0; m < NL; m++) begin
                cand = C_W'(metric[p]) + C_W'(bm_r[p*NL+m]);
                if (cand > MMAX) cand = MMAX;
                if (valid[p] && (!nvalid[(p*NL+m) % NS] || cand < nmetric[(p*NL+m) % NS])) begin
                    nmetric[(p*NL+m) % NS] = cand;
                    nvalid[(p*NL+m) % NS]  = 1'b1;
                    nbp[(p*NL+m) % NS]     = SB'(p);
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
            for (int i = 0; i < NS; i++) begin metric[i] <= '0; valid[i] <= 1'b1; end
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
