// Lockstep testbench for dfse_mlsd.sv against the vectors
// halo_serdes.dsp.fixed_viterbi.dump_dfse_vectors wrote. Exits non-zero on
// any mismatch.
//
//   iverilog -g2012 -o sim -I <vecdir> dfse_mlsd.sv tb_dfse_mlsd.sv
//   vvp sim +vecdir=<vecdir>

`timescale 1ns/1ps
module tb_dfse_mlsd;
    `include "dfse_dims.svh"

    dfse_mlsd dut ();

    longint gold [N];
    string dir;
    int fd, r, errors;
    longint val;

    initial begin
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";
        fd = $fopen({dir, "/dfse_y.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); dut.y[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/dfse_acc.txt"}, "r");
        for (int i = 0; i < NS * NL; i++) begin r = $fscanf(fd, "%d", val); dut.acc[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/dfse_fbt.txt"}, "r");
        for (int i = 0; i < T * NL; i++) begin r = $fscanf(fd, "%d", val); dut.fbt[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/dfse_dec.txt"}, "r");
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
        $display("DFSE LOCKSTEP %s  n=%0d  states=%0d  fed back=%0d  errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", N, NS, T, errors);
        if (errors != 0) $fatal(1, "bit-exact mismatch");
        $finish;
    end
endmodule
