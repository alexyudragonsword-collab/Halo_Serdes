// Lockstep testbench: load the golden vectors dumped by
// halo_serdes.dsp.fixed_datapath (dump_vectors + dump_sv_package), run the
// independent SV datapath, and assert every decision and slicer value matches
// the Python golden bit-for-bit. Exits non-zero on any mismatch.
//
//   iverilog -g2012 -o sim -I <vecdir> ffe_dfe_datapath.sv tb_ffe_dfe.sv
//   vvp sim +vecdir=<vecdir>
//
// The DUT's input/output memories are poked/peeked by hierarchical reference
// (dut.codes[i], dut.dec[i]) — see ffe_dfe_datapath.sv for why they're not
// ports.

`timescale 1ns/1ps
module tb_ffe_dfe;
    `include "dims.svh"

    ffe_dfe_datapath dut ();

    logic [31:0]          dec_gold [N_OUT];
    logic signed [CW-1:0] v_gold   [N_OUT];

    string dir;
    int fd, i, val, r, errors;

    initial begin
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";

        fd = $fopen({dir, "/codes.txt"}, "r");
        for (i = 0; i < N;     i++) begin r = $fscanf(fd, "%d", val); dut.codes[i]  = val; end
        $fclose(fd);
        fd = $fopen({dir, "/w_ffe_int.txt"}, "r");
        for (i = 0; i < NF;    i++) begin r = $fscanf(fd, "%d", val); dut.w_ffe[i]  = val; end
        $fclose(fd);
        fd = $fopen({dir, "/w_dfe_int.txt"}, "r");
        for (i = 0; i < ND;    i++) begin r = $fscanf(fd, "%d", val); dut.w_dfe[i]  = val; end
        $fclose(fd);
        fd = $fopen({dir, "/levels_out.txt"}, "r");
        for (i = 0; i < NL;    i++) begin r = $fscanf(fd, "%d", val); dut.levels[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/dec.txt"}, "r");
        for (i = 0; i < N_OUT; i++) begin r = $fscanf(fd, "%d", val); dec_gold[i]   = val; end
        $fclose(fd);
        fd = $fopen({dir, "/v_out.txt"}, "r");
        for (i = 0; i < N_OUT; i++) begin r = $fscanf(fd, "%d", val); v_gold[i]     = val; end
        $fclose(fd);

        #1;   // let the combinational datapath settle

        errors = 0;
        for (i = 0; i < N_OUT; i++) begin
            if (dut.dec[i] !== dec_gold[i] || dut.v_out[i] !== v_gold[i]) begin
                if (errors < 8)
                    $display("  mismatch[%0d]: dec %0d/%0d  v %0d/%0d (rtl/gold)",
                             i, dut.dec[i], dec_gold[i], dut.v_out[i], v_gold[i]);
                errors++;
            end
        end
        $display("LOCKSTEP %s  n=%0d  errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", N_OUT, errors);
        if (errors != 0) $fatal(1, "bit-exact mismatch");
        $finish;
    end
endmodule
