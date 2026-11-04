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

    string dir, fn;   // fn: the file being read, for error messages
    int fd, i, val, r, errors;

    initial begin
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";

        fn = {dir, "/codes.txt"};
        fd = $fopen(fn, "r");
        if (fd == 0) $fatal(1, "cannot open %s", fn);
        for (i = 0; i < N;     i++) begin r = $fscanf(fd, "%d", val); if (r != 1) $fatal(1, "short read: %s entry %0d", fn, i); dut.codes[i]  = val; end
        $fclose(fd);
        fn = {dir, "/w_ffe_int.txt"};
        fd = $fopen(fn, "r");
        if (fd == 0) $fatal(1, "cannot open %s", fn);
        for (i = 0; i < NF;    i++) begin r = $fscanf(fd, "%d", val); if (r != 1) $fatal(1, "short read: %s entry %0d", fn, i); dut.w_ffe[i]  = val; end
        $fclose(fd);
        fn = {dir, "/w_dfe_int.txt"};
        fd = $fopen(fn, "r");
        if (fd == 0) $fatal(1, "cannot open %s", fn);
        for (i = 0; i < ND;    i++) begin r = $fscanf(fd, "%d", val); if (r != 1) $fatal(1, "short read: %s entry %0d", fn, i); dut.w_dfe[i]  = val; end
        $fclose(fd);
        fn = {dir, "/levels_out.txt"};
        fd = $fopen(fn, "r");
        if (fd == 0) $fatal(1, "cannot open %s", fn);
        for (i = 0; i < NL;    i++) begin r = $fscanf(fd, "%d", val); if (r != 1) $fatal(1, "short read: %s entry %0d", fn, i); dut.levels[i] = val; end
        $fclose(fd);
        fn = {dir, "/dec.txt"};
        fd = $fopen(fn, "r");
        if (fd == 0) $fatal(1, "cannot open %s", fn);
        for (i = 0; i < N_OUT; i++) begin r = $fscanf(fd, "%d", val); if (r != 1) $fatal(1, "short read: %s entry %0d", fn, i); dec_gold[i]   = val; end
        $fclose(fd);
        fn = {dir, "/v_out.txt"};
        fd = $fopen(fn, "r");
        if (fd == 0) $fatal(1, "cannot open %s", fn);
        for (i = 0; i < N_OUT; i++) begin r = $fscanf(fd, "%d", val); if (r != 1) $fatal(1, "short read: %s entry %0d", fn, i); v_gold[i]     = val; end
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
