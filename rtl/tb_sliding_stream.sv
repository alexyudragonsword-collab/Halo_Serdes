// Lockstep testbench for the streaming sliding-detector MLSD (sliding_stream.sv): the same
// golden vectors as tb_sliding_mlsd.sv, (v, dec0) in one per clock through the ports, every
// decision that comes out with out_valid checked in order against the golden. Exits non-zero
// on any mismatch or a missing output.
//
//   iverilog -g2012 -o sim -I <vecdir> sliding_stream.sv tb_sliding_stream.sv
//   vvp sim +vecdir=<vecdir>
//
// V_W / LV_W default to CW (mlsd_dims.svh); a consumer can narrow them to the widths it
// synthesizes. GAPS = 1 streams with idle clocks between symbols.

`timescale 1ns/1ps
module tb_sliding_stream #(
    parameter int V_W = 0, LV_W = 0,
    parameter int GAPS = 0
);
    `include "mlsd_dims.svh"
    localparam int CV = (V_W > 0) ? V_W : CW;
    localparam int CL = (LV_W > 0) ? LV_W : CW;

    logic clk = 0, rst = 1, in_valid = 0, in_last = 0, out_valid, out_last;
    logic signed [CV-1:0]   v = '0;
    logic        [31:0]     dec0 = '0, dec;
    logic        [NL*CL-1:0] levels, fbt;

    sliding_stream #(.NL(NL), .SEQ_LEN(SEQ_LEN), .MARGIN(MARGIN), .SQ_SHIFT(SQ_SHIFT),
                     .METRIC_MAX(METRIC_MAX), .PASSES(PASSES), .V_W(CV), .LV_W(CL)) dut (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last), .v(v), .dec0(dec0),
        .levels(levels), .fbt(fbt), .out_valid(out_valid), .out_last(out_last), .dec(dec));

    always #5 clk = ~clk;

    longint vs [N];
    longint d0 [N];
    longint gold [N];
    string dir, fn;
    int fd, r, errors, n_out, flips;
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
        if (n_out < N) begin
            if (dec !== gold[n_out]) begin
                if (errors < 8)
                    $display("  mismatch[%0d]: dec %0d/%0d (rtl/gold)", n_out, dec, gold[n_out]);
                errors++;
            end
            if (out_last != (n_out == N - 1)) begin
                $display("  out_last wrong at %0d", n_out);
                errors++;
            end
            if (gold[n_out] != d0[n_out]) flips++;
        end
        n_out++;
    end

    initial begin
        longint a [];
        errors = 0; n_out = 0; flips = 0;
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";
        read_into("mlsd_v.txt", N, a);      for (int i = 0; i < N; i++) vs[i] = a[i];
        read_into("mlsd_dec0.txt", N, a);   for (int i = 0; i < N; i++) d0[i] = a[i];
        read_into("mlsd_levels.txt", NL, a); for (int i = 0; i < NL; i++) levels[i*CL +: CL] = a[i];
        read_into("mlsd_fbt.txt", NL, a);   for (int i = 0; i < NL; i++) fbt[i*CL +: CL] = a[i];
        read_into("mlsd_dec.txt", N, a);    for (int i = 0; i < N; i++) gold[i] = a[i];

        @(posedge clk); #2;
        rst = 0;
        for (int i = 0; i < N; i++) begin
            v = vs[i];
            dec0 = d0[i];
            in_valid = 1;
            in_last = (i == N - 1);
            @(posedge clk); #2;
            if (GAPS != 0 && (i % 5 == 4 || i % 13 == 12)) begin
                in_valid = 0; in_last = 0;
                repeat ((i % 13 == 12) ? 3 : 1) begin @(posedge clk); #2; end
            end
        end
        in_valid = 0; in_last = 0;
        repeat (PASSES * (SEQ_LEN + 4) + 8) @(posedge clk);
        #2;
        if (n_out != N) begin
            $display("  output count %0d != %0d", n_out, N);
            errors++;
        end
        $display("MLSD STREAM LOCKSTEP %s  n=%0d  flips=%0d  errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", N, flips, errors);
        if (errors != 0) $fatal(1, "bit-exact mismatch");
        $finish;
    end
endmodule
