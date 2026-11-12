// Lockstep testbench for the streaming datapath (ffe_dfe_stream.sv): the same golden vectors as
// tb_ffe_dfe.sv, but the ADC words go in one per clock through the ports and every (v, dec)
// that comes out with out_valid is checked against the Python golden in order. Exits non-zero
// on any mismatch or a missing output.
//
//   iverilog -g2012 -o sim -I <vecdir> ffe_dfe_stream.sv tb_ffe_dfe_stream.sv
//   vvp sim +vecdir=<vecdir>
//
// The word widths default to CW (dims.svh); a consumer can narrow them (CODE_W, WF_W, WD_W,
// LV_W, PR_W > 0) to the widths it synthesizes and check the same vectors still pass, and pick
// the decision-loop form (LOOKAHEAD 0 / 1); GAPS = 1 streams with idle clocks between codes.

`timescale 1ns/1ps
module tb_ffe_dfe_stream #(
    parameter int CODE_W = 0, WF_W = 0, WD_W = 0, LV_W = 0, PR_W = 0,
    parameter int LOOKAHEAD = 0,
    parameter int GAPS = 0          // 1: idle clocks between some codes (in_valid low)
);
    `include "dims.svh"
    localparam int CWC = (CODE_W > 0) ? CODE_W : CW;
    localparam int CWF = (WF_W > 0) ? WF_W : CW;
    localparam int CWD = (WD_W > 0) ? WD_W : CW;
    localparam int CWL = (LV_W > 0) ? LV_W : CW;
    localparam int CWP = (PR_W > 0) ? PR_W : CW;

    logic clk = 0, rst = 1, in_valid = 0, out_valid;
    logic signed [CWC-1:0]      code = '0;
    logic        [NF*CWF-1:0]   w_ffe;
    logic        [ND*CWD-1:0]   w_dfe;
    logic        [NL*CWL-1:0]   levels;
    logic        [3*CWP-1:0]    pr_abc;
    logic        [NPL*CWL-1:0]  pr_lv;
    logic signed [OUT_BITS-1:0] v;
    logic        [31:0]         dec;

    ffe_dfe_stream #(.NF(NF), .ND(ND), .NL(NL), .N_PRE(N_PRE), .FFE_SHIFT(FFE_SHIFT),
                     .DFE_SHIFT(DFE_SHIFT), .OUT_BITS(OUT_BITS), .DO_ROUND(DO_ROUND),
                     .PR_MODE(PR_MODE), .PR_NT(PR_NT), .PR_FL(PR_FL), .NPL(NPL),
                     .CODE_W(CWC), .WF_W(CWF), .WD_W(CWD), .LV_W(CWL), .PR_W(CWP),
                     .LOOKAHEAD(LOOKAHEAD)) dut (
        .clk(clk), .rst(rst), .in_valid(in_valid), .code(code), .w_ffe(w_ffe), .w_dfe(w_dfe),
        .levels(levels), .pr_abc(pr_abc), .pr_lv(pr_lv), .out_valid(out_valid), .v(v), .dec(dec));

    always #5 clk = ~clk;

    logic [31:0]          dec_gold [N_OUT];
    logic signed [CW-1:0] v_gold   [N_OUT];
    integer               codes    [N];
    string dir, fn;
    int fd, i, val, r, errors, n_out;

    task automatic read_into(input string name, input int count, output integer arr []);
        arr = new[count];
        fn = {dir, "/", name};
        fd = $fopen(fn, "r");
        if (fd == 0) $fatal(1, "cannot open %s", fn);
        for (int j = 0; j < count; j++) begin
            r = $fscanf(fd, "%d", val);
            if (r != 1) $fatal(1, "short read: %s entry %0d", fn, j);
            arr[j] = val;
        end
        $fclose(fd);
    endtask

    always @(posedge clk) #1 if (out_valid) begin
        if (n_out < N_OUT) begin
            if (dec !== dec_gold[n_out] || CW'(v) !== v_gold[n_out]) begin
                if (errors < 8)
                    $display("  mismatch[%0d]: dec %0d/%0d  v %0d/%0d (rtl/gold)",
                             n_out, dec, dec_gold[n_out], v, v_gold[n_out]);
                errors++;
            end
        end
        n_out++;
    end

    initial begin
        integer a [];
        errors = 0; n_out = 0;
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";
        read_into("codes.txt", N, a);      for (i = 0; i < N; i++) codes[i] = a[i];
        read_into("w_ffe_int.txt", NF, a); for (i = 0; i < NF; i++) w_ffe[i*CWF +: CWF] = a[i];
        read_into("w_dfe_int.txt", ND, a); for (i = 0; i < ND; i++) w_dfe[i*CWD +: CWD] = a[i];
        read_into("levels_out.txt", NL, a); for (i = 0; i < NL; i++) levels[i*CWL +: CWL] = a[i];
        pr_abc = '0;
        pr_lv  = '0;
        if (PR_MODE > 0) begin
            read_into("pr_abc.txt", 3, a); for (i = 0; i < 3; i++) pr_abc[i*CWP +: CWP] = a[i];
        end
        if (PR_MODE == 2) begin
            read_into("pr_lv.txt", NPL, a); for (i = 0; i < NPL; i++) pr_lv[i*CWL +: CWL] = a[i];
        end
        read_into("dec.txt", N_OUT, a);   for (i = 0; i < N_OUT; i++) dec_gold[i] = a[i];
        read_into("v_out.txt", N_OUT, a); for (i = 0; i < N_OUT; i++) v_gold[i] = a[i];

        @(posedge clk); #2;
        rst = 0;
        // only the codes the N_OUT outputs need (k = s + N_PRE)
        for (i = 0; i < N_OUT + N_PRE; i++) begin
            code = codes[i];
            in_valid = 1;
            @(posedge clk); #2;
            // a fixed pattern of bubbles: one idle clock after every 5th code, three after
            // every 13th, so a stage sometimes sees its successor at once and sometimes later
            if (GAPS != 0 && (i % 5 == 4 || i % 13 == 12)) begin
                in_valid = 0;
                code = '0;
                repeat ((i % 13 == 12) ? 3 : 1) begin @(posedge clk); #2; end
            end
        end
        in_valid = 0;
        repeat (8) @(posedge clk);
        #2;
        if (n_out != N_OUT) begin
            $display("  output count %0d != %0d", n_out, N_OUT);
            errors++;
        end
        $display("STREAM LOCKSTEP %s  n=%0d  pr_mode=%0d  errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", N_OUT, PR_MODE, errors);
        if (errors != 0) $fatal(1, "bit-exact mismatch");
        $finish;
    end
endmodule
