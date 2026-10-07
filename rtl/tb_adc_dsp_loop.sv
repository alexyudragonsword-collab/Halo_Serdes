// Lockstep testbench for adc_dsp_loop.sv: load the vectors
// halo_serdes.dsp.fixed_loop.dump_loop_vectors wrote from a closed-loop run,
// replay the input words through the SV back end, and require every PI code,
// slicer value and decision to equal the Python golden. Exits non-zero on
// any mismatch.
//
//   iverilog -g2012 -o sim -I <vecdir> adc_dsp_loop.sv tb_adc_dsp_loop.sv
//   vvp sim +vecdir=<vecdir>

`timescale 1ns/1ps
module tb_adc_dsp_loop;
    `include "loop_dims.svh"

    adc_dsp_loop dut ();

    longint pi_gold  [N];
    longint dec_gold [N_DEC];
    longint v_gold   [N_DEC];
    longint wf_gold  [NF];
    longint wd_gold  [ND];
    longint ab_gold  [2];
    longint cal_gold [2 * LANES + 1];   // final lane offsets, gains, mean power

    string dir;
    int fd, r, errors;
    longint val;

    initial begin
        if (!$value$plusargs("vecdir=%s", dir)) dir = ".";

        fd = $fopen({dir, "/loop_xin.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); dut.xraw[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_ref.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); dut.ref_sym[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_w_ffe_end.txt"}, "r");
        for (int i = 0; i < NF; i++) begin r = $fscanf(fd, "%d", val); wf_gold[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_w_dfe_end.txt"}, "r");
        for (int i = 0; i < ND; i++) begin r = $fscanf(fd, "%d", val); wd_gold[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_pr_lv.txt"}, "r");
        for (int i = 0; i < NPL; i++) begin r = $fscanf(fd, "%d", val); dut.pr_lv[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_ab.txt"}, "r");
        for (int i = 0; i < 2; i++) begin r = $fscanf(fd, "%d", val); dut.ab[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_ab_end.txt"}, "r");
        for (int i = 0; i < 2; i++) begin r = $fscanf(fd, "%d", val); ab_gold[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_w_ffe_int.txt"}, "r");
        for (int i = 0; i < NF; i++) begin r = $fscanf(fd, "%d", val); dut.w_ffe[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_w_dfe_int.txt"}, "r");
        for (int i = 0; i < ND; i++) begin r = $fscanf(fd, "%d", val); dut.w_dfe[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_levels_out.txt"}, "r");
        for (int i = 0; i < NL; i++) begin r = $fscanf(fd, "%d", val); dut.levels[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_cal_end.txt"}, "r");
        for (int i = 0; i < 2 * LANES + 1; i++) begin r = $fscanf(fd, "%d", val); cal_gold[i] = val; end
        $fclose(fd);

        fd = $fopen({dir, "/loop_pi.txt"}, "r");
        for (int i = 0; i < N; i++) begin r = $fscanf(fd, "%d", val); pi_gold[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_dec.txt"}, "r");
        for (int i = 0; i < N_DEC; i++) begin r = $fscanf(fd, "%d", val); dec_gold[i] = val; end
        $fclose(fd);
        fd = $fopen({dir, "/loop_v_out.txt"}, "r");
        for (int i = 0; i < N_DEC; i++) begin r = $fscanf(fd, "%d", val); v_gold[i] = val; end
        $fclose(fd);

        dut.run();

        errors = 0;
        for (int i = 0; i < N; i++) begin
            if (dut.pi_out[i] !== pi_gold[i]) begin
                if (errors < 8)
                    $display("  pi mismatch[%0d]: %0d/%0d (rtl/gold)", i, dut.pi_out[i], pi_gold[i]);
                errors++;
            end
        end
        for (int i = 0; i < N_DEC; i++) begin
            if (dut.dec[i] !== dec_gold[i] || dut.v_out[i] !== v_gold[i]) begin
                if (errors < 8)
                    $display("  mismatch[%0d]: dec %0d/%0d  v %0d/%0d (rtl/gold)",
                             i, dut.dec[i], dec_gold[i], dut.v_out[i], v_gold[i]);
                errors++;
            end
        end
        for (int i = 0; i < NF; i++)
            if (dut.w_ffe[i] !== wf_gold[i]) begin
                $display("  final FFE weight[%0d]: %0d/%0d (rtl/gold)", i, dut.w_ffe[i], wf_gold[i]);
                errors++;
            end
        for (int i = 0; i < ND; i++)
            if (dut.w_dfe[i] !== wd_gold[i]) begin
                $display("  final DFE weight[%0d]: %0d/%0d (rtl/gold)", i, dut.w_dfe[i], wd_gold[i]);
                errors++;
            end
        for (int i = 0; i < 2; i++)
            if (dut.ab[i] !== ab_gold[i]) begin
                $display("  final PR cursor[%0d]: %0d/%0d (rtl/gold)", i, dut.ab[i], ab_gold[i]);
                errors++;
            end
        for (int l = 0; l < LANES; l++)
            if (dut.co[l] !== cal_gold[l] || dut.cg[l] !== cal_gold[LANES + l]) begin
                $display("  final calibration, lane %0d: offset %0d/%0d gain %0d/%0d (rtl/gold)",
                         l, dut.co[l], cal_gold[l], dut.cg[l], cal_gold[LANES + l]);
                errors++;
            end
        if (dut.cpm !== cal_gold[2 * LANES]) begin
            $display("  final calibration power: %0d/%0d (rtl/gold)", dut.cpm, cal_gold[2 * LANES]);
            errors++;
        end
        $display("LOOP LOCKSTEP %s  n=%0d  errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", N, errors);
        if (errors != 0) $fatal(1, "bit-exact mismatch");
        $finish;
    end
endmodule
