// Lockstep testbench for sliding_mlsd.sv against the vectors
// halo_serdes.dsp.fixed_mlsd.dump_mlsd_vectors wrote. Exits non-zero on any
// mismatch.
//
//   iverilog -g2012 -o sim -I <vecdir> sliding_mlsd.sv tb_sliding_mlsd.sv
//   vvp sim +vecdir=<vecdir>

`timescale 1ns/1ps
module tb_sliding_mlsd;
    `include "mlsd_dims.svh"

    sliding_mlsd dut ();

    longint gold [N];
    string dir;
    int fd, r, errors, flips;
    longint val;

    initial begin
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";
        fd = $fopen({dir, "/mlsd_v.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); dut.v[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/mlsd_dec0.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); dut.dec0[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/mlsd_levels.txt"}, "r");
        for (int i = 0; i < NL; i++) begin r = $fscanf(fd, "%d", val); dut.levels[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/mlsd_fbt.txt"}, "r");
        for (int i = 0; i < NL; i++) begin r = $fscanf(fd, "%d", val); dut.fbt[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/mlsd_dec.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); gold[i] = val; end
        $fclose(fd);

        dut.run();

        errors = 0;
        flips = 0;
        for (int i = 0; i < N; i++) begin
            if (dut.dec[i] != dut.dec0[i]) flips++;
            if (dut.dec[i] !== gold[i]) begin
                if (errors < 8)
                    $display("  mismatch[%0d]: dec %0d/%0d (rtl/gold)", i, dut.dec[i], gold[i]);
                errors++;
            end
        end
        $display("MLSD LOCKSTEP %s  n=%0d  flips=%0d  errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", N, flips, errors);
        if (errors != 0) $fatal(1, "bit-exact mismatch");
        $finish;
    end
endmodule
