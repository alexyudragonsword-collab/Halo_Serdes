// Lockstep testbench for viterbi_mlsd.sv against the vectors
// halo_serdes.dsp.fixed_viterbi.dump_viterbi_vectors wrote. Exits non-zero
// on any mismatch.
//
//   iverilog -g2012 -o sim -I <vecdir> viterbi_mlsd.sv tb_viterbi_mlsd.sv
//   vvp sim +vecdir=<vecdir>

`timescale 1ns/1ps
module tb_viterbi_mlsd;
    `include "vit_dims.svh"

    viterbi_mlsd dut ();

    longint gold [N];
    string dir;
    int fd, r, errors;
    longint val;

    initial begin
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";
        fd = $fopen({dir, "/vit_y.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); dut.y[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/vit_expected.txt"}, "r");
        for (int i = 0; i < NS * NL; i++) begin r = $fscanf(fd, "%d", val); dut.ex[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/vit_dec.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); gold[i] = val; end
        $fclose(fd);

        dut.run();

        errors = 0;
        for (int i = 0; i < N; i++)
            if (dut.dec[i] !== gold[i]) begin
                if (errors < 8)
                    $display("  mismatch[%0d]: dec %0d/%0d (rtl/gold)", i, dut.dec[i], gold[i]);
                errors++;
            end
        $display("VITERBI LOCKSTEP %s  n=%0d  states=%0d  errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", N, NS, errors);
        if (errors != 0) $fatal(1, "bit-exact mismatch");
        $finish;
    end
endmodule
