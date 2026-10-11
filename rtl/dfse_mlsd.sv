// Fixed-point reduced-state Viterbi MLSD (decision-feedback sequence
// estimation).
//
// An INDEPENDENT SystemVerilog implementation of
// halo_serdes.dsp.fixed_viterbi._dfse_fixed_py, checked bit-exact by
// tb_dfse_mlsd.sv. Input: slicer words y[k], the unrounded state part of
// the expected word per (state, new symbol) and the unrounded share of each
// fed-back cursor per level; output: the decided symbols.
//
//   state s: the last K symbols, newest in the lowest base-NL digit; next
//            state (s * NL + m) % NS
//   tail[s][j]: the survivor's decision j + K + 1 symbols back (T = L - K
//            of them, newest first), carried with the survivor through ACS;
//            all zero (level 0) at the start
//   expected word ((acc[s][m] + sum_j fbt[j][tail[s][j]]) + half) >>> C_FL
//   branch metric (y - expected)^2 >>> SQ_SHIFT; path metric the sum,
//            saturated at METRIC_MAX
//   add-compare-select: previous states ascending, symbols ascending, a
//            strictly smaller candidate wins (and takes the tail)
//   renormalise: subtract the smallest path metric every symbol
//   traceback over the whole run from the first state with the smallest
//            metric
//
// A task with module-internal memories, as viterbi_mlsd.sv.

module dfse_mlsd;
    `include "dfse_dims.svh"

    localparam longint TW = (T > 0) ? T : 1;   // tail storage width (no zero-size arrays)

    longint y    [N];
    longint acc  [NS * NL];      // acc[s][m] at s * NL + m
    longint fbt  [TW * NL];      // fbt[j][l] at j * NL + l
    longint dec  [N];
    longint bp   [N * NS];

    task automatic run();
        longint metric  [NS];
        longint nmetric [NS];
        bit valid  [NS];
        bit nvalid [NS];
        longint tail  [NS * TW];
        longint ntail [NS * TW];
        longint e, cand, lo, fb, top, half;
        int ns, best, s, oldest;
        top = 1;
        for (int i = 1; i < K; i++) top = top * NL;
        half = (C_FL > 0) ? (64'sd1 <<< (C_FL - 1)) : 0;
        for (int i = 0; i < NS; i++) begin
            metric[i] = 0; valid[i] = 1;
            for (int j = 0; j < TW; j++) begin tail[i * TW + j] = 0; ntail[i * TW + j] = 0; end
        end
        for (int k = 0; k < N; k++) begin
            for (int i = 0; i < NS; i++) nvalid[i] = 0;
            for (int p = 0; p < NS; p++) begin
                if (valid[p]) begin
                    fb = 0;
                    for (int j = 0; j < T; j++) fb = fb + fbt[j * NL + tail[p * TW + j]];
                    oldest = p / top;
                    for (int m = 0; m < NL; m++) begin
                        e = y[k] - ((acc[p * NL + m] + fb + half) >>> C_FL);
                        cand = metric[p] + ((e * e) >>> SQ_SHIFT);
                        if (cand > METRIC_MAX) cand = METRIC_MAX;
                        ns = (p * NL + m) % NS;
                        if (!nvalid[ns] || cand < nmetric[ns]) begin
                            nmetric[ns] = cand;
                            nvalid[ns] = 1;
                            bp[k * NS + ns] = p;
                            if (T > 0) begin
                                ntail[ns * TW] = oldest;
                                for (int j = 1; j < T; j++)
                                    ntail[ns * TW + j] = tail[p * TW + j - 1];
                            end
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
                for (int j = 0; j < T; j++) tail[i * TW + j] = ntail[i * TW + j];
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
