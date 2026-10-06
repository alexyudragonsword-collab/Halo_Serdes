// Fixed-point Viterbi MLSD over the residual ISI.
//
// An INDEPENDENT SystemVerilog implementation of
// halo_serdes.dsp.fixed_viterbi._viterbi_fixed_py, checked bit-exact by
// tb_viterbi_mlsd.sv. Input: slicer words y[k] and the table of expected
// words per (state, new symbol); output: the decided symbols.
//
//   state s: the last NS-states' worth of symbols, newest in the lowest
//            base-NL digit; next state (s * NL + m) % NS
//   branch metric (y - expected[s][m])^2 >>> SQ_SHIFT; path metric the sum,
//            saturated at METRIC_MAX
//   add-compare-select: previous states ascending, symbols ascending, a
//            strictly smaller candidate wins
//   renormalise: subtract the smallest path metric every symbol
//   traceback over the whole run from the first state with the smallest
//            metric (a hardware detector would cut it at a fixed depth)
//
// A task with module-internal memories, as adc_dsp_loop.sv.

module viterbi_mlsd;
    `include "vit_dims.svh"

    longint y   [N];
    longint ex  [NS * NL];       // expected[s][m] at s * NL + m
    longint dec [N];
    longint bp  [N * NS];

    task automatic run();
        longint metric [NS];
        longint nmetric [NS];
        bit valid [NS];
        bit nvalid [NS];
        longint e, cand, lo;
        int ns, best, s;
        for (int i = 0; i < NS; i++) begin metric[i] = 0; valid[i] = 1; end
        for (int k = 0; k < N; k++) begin
            for (int i = 0; i < NS; i++) nvalid[i] = 0;
            for (int p = 0; p < NS; p++) begin
                if (valid[p]) begin
                    for (int m = 0; m < NL; m++) begin
                        e = y[k] - ex[p * NL + m];
                        cand = metric[p] + ((e * e) >>> SQ_SHIFT);
                        if (cand > METRIC_MAX) cand = METRIC_MAX;
                        ns = (p * NL + m) % NS;
                        if (!nvalid[ns] || cand < nmetric[ns]) begin
                            nmetric[ns] = cand;
                            nvalid[ns] = 1;
                            bp[k * NS + ns] = p;
                        end
                    end
                end
            end
            lo = METRIC_MAX;
            for (int i = 0; i < NS; i++)
                if (nvalid[i] && nmetric[i] < lo) lo = nmetric[i];
            for (int i = 0; i < NS; i++) begin
                valid[i] = nvalid[i];
                metric[i] = nvalid[i] ? nmetric[i] - lo : 0;
            end
        end
        best = 0;
        for (int i = 0; i < NS; i++)
            if (valid[i] && (!valid[best] || metric[i] < metric[best])) best = i;
        s = best;
        for (int k = N - 1; k >= 0; k--) begin
            dec[k] = s % NL;
            s = bp[k * NS + s];
        end
    endtask
endmodule
