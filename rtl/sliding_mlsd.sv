// Fixed-point sliding-detector MLSD (DragonPHY's error-event post-detector).
//
// An INDEPENDENT SystemVerilog implementation of
// halo_serdes.dsp.fixed_mlsd._sliding_fixed_py, checked bit-exact by
// tb_sliding_mlsd.sv. Input: slicer words v[k] and the slicer's decisions;
// output: the corrected decisions.
//
//   residual  e[k] = v[k] - L[d[k]] - fbt[d[k-1]]      (fbt: feedback per level)
//   window metric = sum_{j<SEQ_LEN} (e[k+j]^2 >>> SQ_SHIFT), saturated at METRIC_MAX
//   hypothesis d[k] = m = d[k] +- 1 recomputes e[k] (with L[m]) and e[k+1]
//   (with fbt[m]); accept the best whose metric < base - MARGIN; PASSES passes
//
// A task (see adc_dsp_loop.sv for why), with module-internal memories the
// testbench pokes and peeks.

module sliding_mlsd;
    `include "mlsd_dims.svh"

    longint v      [N];
    longint dec0   [N];
    longint levels [NL];
    longint fbt    [NL];
    longint dec    [N];

    longint resid  [N];

    function automatic longint sq(input longint e);
        return (e * e) >>> SQ_SHIFT;
    endfunction

    task automatic run();
        longint base, best, sse, e0, e1, m;
        int best_d;
        for (int i = 0; i < N; i++) dec[i] = dec0[i];
        if (N >= SEQ_LEN + 2) begin
            for (int p = 0; p < PASSES; p++) begin
                for (int k = 0; k < N; k++) begin
                    resid[k] = v[k] - levels[dec[k]];
                    if (k > 0) resid[k] = resid[k] - fbt[dec[k - 1]];
                end
                for (int k = 1; k < N - SEQ_LEN - 1; k++) begin
                    base = 0;
                    for (int j = 0; j < SEQ_LEN; j++) base += sq(resid[k + j]);
                    if (base > METRIC_MAX) base = METRIC_MAX;
                    best = base - MARGIN;
                    best_d = 0;
                    for (int d = -1; d <= 1; d += 2) begin
                        m = dec[k] + d;
                        if (m >= 0 && m < NL) begin
                            e0 = v[k] - levels[m] - fbt[dec[k - 1]];
                            e1 = v[k + 1] - levels[dec[k + 1]] - fbt[m];
                            sse = sq(e0) + sq(e1);
                            for (int j = 2; j < SEQ_LEN; j++) sse += sq(resid[k + j]);
                            if (sse > METRIC_MAX) sse = METRIC_MAX;
                            if (sse < best) begin best = sse; best_d = d; end
                        end
                    end
                    if (best_d != 0) begin
                        m = dec[k] + best_d;
                        resid[k] = v[k] - levels[m] - fbt[dec[k - 1]];
                        resid[k + 1] = v[k + 1] - levels[dec[k + 1]] - fbt[m];
                        dec[k] = m;
                    end
                end
            end
        end
    endtask
endmodule
