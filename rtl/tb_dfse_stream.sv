// Lockstep testbench for the streaming DFSE (dfse_stream.sv): the same golden vectors as
// tb_dfse_mlsd.sv, the slicer words in one per clock through the ports, the survivor memory
// here, and every (out_k, dec) that comes out with out_valid checked against the golden.
// Exits non-zero on any mismatch, a symbol decided twice or not at all.
//
//   iverilog -g2012 -o sim -I <vecdir> dfse_stream.sv tb_dfse_stream.sv
//   vvp sim +vecdir=<vecdir>
//
// Y_W / EX_W default to CW (dfse_dims.svh); a consumer can narrow them to the widths it
// synthesizes. GAPS = 1 streams with idle clocks between symbols.

`timescale 1ns/1ps
module tb_dfse_stream #(
    parameter int Y_W = 32, ACC_W = 32, FB_W = 32,
    parameter int GAPS = 0
);
    `include "dfse_dims.svh"
    localparam int TW = (T > 0) ? T : 1;
    localparam int SB = (NS > 1) ? $clog2(NS) : 1;
    localparam int KW = $clog2(N + 1);

    logic clk = 0, rst = 1, in_valid = 0, in_last = 0;
    logic signed [Y_W-1:0]        y = '0;
    logic        [NS*NL*ACC_W-1:0] acc;
    logic        [TW*NL*FB_W-1:0]  fbt;
    logic                       bp_we, bp_re, out_valid;
    logic        [KW-1:0]       bp_waddr, bp_raddr, out_k;
    logic        [NS*SB-1:0]    bp_wdata, bp_rdata;
    logic        [31:0]         dec;
    logic        [NS*SB-1:0]    mem [N];

    dfse_stream #(.NL(NL), .NS(NS), .K(K), .T(T), .C_FL(C_FL), .SQ_SHIFT(SQ_SHIFT),
                  .METRIC_MAX(METRIC_MAX), .Y_W(Y_W), .ACC_W(ACC_W), .FB_W(FB_W), .KW(KW)) dut (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last), .y(y),
        .acc(acc), .fbt(fbt), .bp_we(bp_we), .bp_waddr(bp_waddr), .bp_wdata(bp_wdata),
        .bp_re(bp_re), .bp_raddr(bp_raddr), .bp_rdata(bp_rdata), .out_valid(out_valid),
        .out_k(out_k), .dec(dec));

    always #5 clk = ~clk;
    always @(posedge clk) begin                         // the survivor memory: sync read
        if (bp_we) mem[bp_waddr] <= bp_wdata;
        if (bp_re) bp_rdata <= mem[bp_raddr];
    end

    longint ys [N];
    longint gold [N];
    bit     seen [N];
    string dir, fn;
    int fd, r, errors, n_out;
    longint val;

    task automatic read_into(input string name, input int count, output longint arr []);
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
        if (out_k >= N || seen[out_k]) begin
            $display("  symbol %0d decided twice or out of range", out_k);
            errors++;
        end else begin
            seen[out_k] = 1;
            if (dec !== gold[out_k]) begin
                if (errors < 8)
                    $display("  mismatch[%0d]: dec %0d/%0d (rtl/gold)", out_k, dec, gold[out_k]);
                errors++;
            end
        end
        n_out++;
    end

    initial begin
        longint a [];
        errors = 0; n_out = 0;
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";
        read_into("dfse_y.txt", N, a);          for (int i = 0; i < N; i++) ys[i] = a[i];
        read_into("dfse_acc.txt", NS * NL, a);
        for (int i = 0; i < NS * NL; i++) acc[i*ACC_W +: ACC_W] = a[i];
        fbt = '0;
        if (T > 0) begin
            read_into("dfse_fbt.txt", T * NL, a);
            for (int i = 0; i < T * NL; i++) fbt[i*FB_W +: FB_W] = a[i];
        end
        read_into("dfse_dec.txt", N, a);        for (int i = 0; i < N; i++) gold[i] = a[i];
        for (int i = 0; i < N; i++) seen[i] = 0;

        @(posedge clk); #2;
        rst = 0;
        for (int i = 0; i < N; i++) begin
            y = ys[i];
            in_valid = 1;
            in_last = (i == N - 1);
            @(posedge clk); #2;
            if (GAPS != 0 && (i % 5 == 4 || i % 13 == 12)) begin
                in_valid = 0; in_last = 0; y = '0;
                repeat ((i % 13 == 12) ? 3 : 1) begin @(posedge clk); #2; end
            end
        end
        in_valid = 0; in_last = 0;
        repeat (N + 8) @(posedge clk);
        #2;
        if (n_out != N) begin
            $display("  output count %0d != %0d", n_out, N);
            errors++;
        end
        $display("DFSE STREAM LOCKSTEP %s  n=%0d  states=%0d  errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", N, NS, errors);
        if (errors != 0) $fatal(1, "bit-exact mismatch");
        $finish;
    end
endmodule
