// Fixed-point ADC receiver back end WITH its clock recovery: FFE + DFE +
// slicer + Mueller-Muller phase detector + loop filter + phase register.
//
// An INDEPENDENT SystemVerilog implementation of
// halo_serdes.dsp.fixed_loop._digital_step_py, checked bit-exact against the
// Python golden by tb_adc_dsp_loop.sv. Input: the ADC word per symbol
// (2*code + 1, recorded from a closed-loop run, so the analog side that the
// RTL does not model is already in it). Outputs: per symbol, the
// phase-interpolator code the loop chose for it, and per decided symbol the
// slicer value and decision.
//
// Semantics (all signed 64-bit):
//   FFE   acc = sum w_ffe[i] * x[k-i]; rounding add; >>> FFE_SHIFT; saturate
//   DFE   fb  = sum w_dfe[d] * levels[dec[s-1-d]]; rounding add; >>> DFE_SHIFT
//   slice nearest level, first minimum on ties; during training
//         (s < TRAIN_LEN, ref_sym[s] >= 0) the decision is the reference
//   LMS   from s >= ADAPT_START, e = v - levels[dec[s]]:
//         wacc_f[i] -= rnd(e * x[k-i], SH_F); wacc_d[d] += rnd(e * levels[dec[s-1-d]], SH_D)
//         (rnd: <<< for SH >= 0, else rounding add + >>>), saturated to the
//         weight range G bits finer; the weights are wacc >>> G
//   PR    (PR_MODE 1) ctl = rnd(a L[xl[s-1]] + b L[xl[s-2]], -PFL), slice v - ctl,
//         the DFE starts NT symbols back on the line-symbol estimates xl,
//         a / b adapted by LMS (LMS_A) on accumulators G bits finer, clipped
//         to [0, A_MAX] / [B_LO, B_HI]; (PR_MODE 2, precoded 1 + D) slice the
//         composite levels pr_lv to q, decide (q - dec[s-1]) mod NL, xl =
//         clip(q - xl[s-1]); the PD reads v - ctl when PD_FFE
//   MM PD on symbol s-1: x1 * (sign x2 - sign x0), on FFE outputs (PD_FFE) or
//         input words; summed over LANES symbols, then
//         pd = -acc - PD_OFF; integ += pd <<< KI_SH; c = (pd <<< KP_SH) + integ
//         (a negative shift is >>> by its magnitude); clamp; LAT blocks of
//         latency through a queue
//   phase register += c >>> LANE_SHIFT every symbol; PI code = ph >>> PI_SH
//   CAL   (CAL_ON) background ADC calibration of the raw word before the FFE,
//         lane l = k % LANES: d = (xraw <<< F) - co[l]; y = (d * g) >>> B,
//         with g = cg[l] - (sum cg >>> LANE_SHIFT) + 2^B (the common gain pinned);
//         x = clip(rnd(y, F), +-CAL_WMAX); co[l] += rnd(d, SH_O);
//         cg[l] += rnd((pm >>> F) - x*x, SH_G); pm += rnd((x*x <<< F) - pm, SH_P)
//         (rnd: rounding add + >>>; a negative shift skips that update)
//   SKEW  (CAL_SKEW) per-lane delay trims: PI code = (ph - trim(l)) >>> PI_SH with
//         trim(l) = cts[l] - (sum cts >>> LANE_SHIFT); after symbol k, lane
//         (k-1) % LANES: cts += shift(x[k-1] * (sign x[k] - sign x[k-2]), CAL_SH_S)
//         on the corrected words (<<< for a non-negative shift, else rounding >>>)
//
// Python's >> on a negative int floors; SV >>> on a signed operand does too.
// Memories are module-internal, as in ffe_dfe_datapath.sv (Icarus' unpacked
// array ports are weak); the testbench pokes them, calls run() and peeks the
// results hierarchically. run() is a task rather than an always_comb block:
// the loop carries state from symbol to symbol, and Icarus 12 asserts on a
// combinational block of this size (vvp_fun_anyedge_sa).

module adc_dsp_loop;
    `include "loop_dims.svh"

    longint xraw   [N];           // ADC words as recorded
    longint xin    [N];           // after calibration (= xraw with CAL_ON 0)
    longint ref_sym [N];
    longint co     [LANES];       // calibration: lane offsets, gains, mean power
    longint cg     [LANES];
    longint cpm;
    longint cgsum;                // sum of the gain registers (the common gain is pinned)
    longint cts    [LANES];       // skew: lane delay trims, and their sum
    longint ctsum;
    longint w_ffe  [NF];          // initial weights in, adapted weights out
    longint w_dfe  [ND];
    longint wacc_f [NF];
    longint wacc_d [ND];
    longint levels [NL];
    longint pr_lv  [NPL];
    longint ab     [2];           // a, b in; adapted a, b out
    longint xl     [N];
    longint r_out  [N];
    longint pi_out [N];
    longint dec    [N];
    longint v_out  [N];

    localparam longint LIM_HI = (longint'(1) << (OUT_BITS - 1)) - 1;
    localparam longint LIM_LO = -(longint'(1) << (OUT_BITS - 1));

    function automatic longint ashift(input longint v, input longint sh);
        if (sh >= 0) return v <<< sh;
        return v >>> (-sh);
    endfunction

    function automatic longint rnd(input longint v, input longint sh);
        if (sh >= 0) return v <<< sh;
        if (DO_ROUND == 1) return (v + (longint'(1) <<< (-sh - 1))) >>> (-sh);
        return v >>> (-sh);
    endfunction

    function automatic longint clip(input longint a, input longint lo, input longint hi);
        if (a > (hi <<< G) + (longint'(1) <<< G) - 1) return (hi <<< G) + (longint'(1) <<< G) - 1;
        if (a < (lo <<< G)) return lo <<< G;
        return a;
    endfunction

    function automatic longint crnd(input longint v, input longint sh);
        if (sh == 0) return v;
        return (v + (longint'(1) <<< (sh - 1))) >>> sh;
    endfunction

    task automatic run();
        longint acc, fb, v, dd, bd, x0, x1, x2, a0, a2, pd, c, e;
        longint prev, prev2, ctl, u, xc, acc_a, acc_b;
        int q, nt;
        bit training;
        longint ph, integ, corr, pd_acc, pd_n, qi;
        longint cd, cy, cx, cp2;
        int cl;
        longint queue [LAT + 1];
        int s, j, jj, best;
        ph = 0; integ = 0; corr = 0; pd_acc = 0; pd_n = 0; qi = 0;
        for (int q = 0; q <= LAT; q++) queue[q] = 0;
        for (int i = 0; i < NF; i++) wacc_f[i] = w_ffe[i] <<< G;
        for (int d = 0; d < ND; d++) wacc_d[d] = w_dfe[d] <<< G;
        acc_a = ab[0] <<< G;
        acc_b = ab[1] <<< G;
        nt = (PR_MODE > 0) ? NT : 1;
        for (int l = 0; l < LANES; l++) begin co[l] = 0; cg[l] = longint'(1) <<< CAL_B; end
        cpm = CAL_PM0;
        cgsum = longint'(LANES) <<< CAL_B;
        for (int l = 0; l < LANES; l++) cts[l] = 0;
        ctsum = 0;
        for (int k = 0; k < N; k++) begin
            pi_out[k] = (ph - (cts[k % LANES] - (ctsum >>> LANE_SHIFT))) >>> PI_SH;
            // background calibration of the raw word (inline: Icarus 12
            // crashes on a task writing an output into an array element)
            if (CAL_ON == 0) xin[k] = xraw[k];
            else begin
                cl = k % LANES;
                cd = (xraw[k] <<< CAL_F) - co[cl];
                cy = (cd * (cg[cl] - (cgsum >>> LANE_SHIFT) + (longint'(1) <<< CAL_B))) >>> CAL_B;
                cx = (cy + (longint'(1) <<< (CAL_F - 1))) >>> CAL_F;
                if (cx > CAL_WMAX) cx = CAL_WMAX;
                else if (cx < -CAL_WMAX) cx = -CAL_WMAX;
                xin[k] = cx;
                if (CAL_SH_O >= 0) co[cl] += crnd(cd, CAL_SH_O);
                cp2 = cx * cx;
                if (CAL_SH_G >= 0) begin
                    cd = crnd((cpm >>> CAL_F) - cp2, CAL_SH_G);
                    cg[cl] += cd;
                    cgsum += cd;
                end
                if (CAL_SH_P >= 0) cpm += crnd((cp2 <<< CAL_F) - cpm, CAL_SH_P);
            end
            s = k - N_PRE;
            if (s >= 0) begin
                acc = 0;
                for (int i = 0; i < NF; i++) begin
                    j = k - i;
                    if (j >= 0) acc += w_ffe[i] * xin[j];
                end
                if (DO_ROUND == 1 && FFE_SHIFT > 0) acc += longint'(1) << (FFE_SHIFT - 1);
                acc = acc >>> FFE_SHIFT;
                if (acc > LIM_HI) acc = LIM_HI;
                else if (acc < LIM_LO) acc = LIM_LO;
                fb = 0;
                for (int d = 0; d < ND; d++) begin
                    jj = s - nt - d;
                    if (jj >= 0) fb += w_dfe[d] * levels[xl[jj]];
                end
                if (DO_ROUND == 1 && DFE_SHIFT > 0) fb += longint'(1) << (DFE_SHIFT - 1);
                fb = fb >>> DFE_SHIFT;
                v = acc - fb;
                if (v > LIM_HI) v = LIM_HI;
                else if (v < LIM_LO) v = LIM_LO;
                v_out[s] = v;
                prev = 0; prev2 = 0; ctl = 0;
                if (PR_MODE > 0) begin
                    if (s >= 1) prev = levels[xl[s - 1]];
                    if (nt == 3 && s >= 2) prev2 = levels[xl[s - 2]];
                    ctl = rnd((acc_a >>> G) * prev + (acc_b >>> G) * prev2, -PFL);
                end
                r_out[s] = v - ctl;
                q = 0;
                if (PR_MODE == 2) begin
                    bd = (v > pr_lv[0]) ? (v - pr_lv[0]) : (pr_lv[0] - v);
                    for (int m = 1; m < NPL; m++) begin
                        dd = (v > pr_lv[m]) ? (v - pr_lv[m]) : (pr_lv[m] - v);
                        if (dd < bd) begin bd = dd; q = m; end
                    end
                    best = q - ((s >= 1) ? dec[s - 1] : 0);
                    best = ((best % NL) + NL) % NL;     // SV % keeps the dividend's sign
                end else begin
                    u = v - ctl;
                    best = 0;
                    bd = (u > levels[0]) ? (u - levels[0]) : (levels[0] - u);
                    for (int m = 1; m < NL; m++) begin
                        dd = (u > levels[m]) ? (u - levels[m]) : (levels[m] - u);
                        if (dd < bd) begin bd = dd; best = m; end
                    end
                end
                training = (s < TRAIN_LEN && ref_sym[s] >= 0);
                if (training) begin
                    dec[s] = ref_sym[s];
                    xl[s] = ref_sym[s];
                end else begin
                    dec[s] = best;
                    if (PR_MODE == 2) begin
                        xc = q - ((s >= 1) ? xl[s - 1] : 0);
                        if (xc < 0) xc = 0;
                        else if (xc > NL - 1) xc = NL - 1;
                        xl[s] = xc;
                    end else xl[s] = best;
                end

                if (s >= ADAPT_START && (LMS_F == 1 || LMS_D == 1)) begin
                    if (PR_MODE == 0) e = v - levels[dec[s]];
                    else if (PR_MODE == 2 && !training) e = v - pr_lv[q];
                    else begin
                        e = v - (levels[xl[s]] + ctl);
                        if (LMS_A == 1 && PR_MODE == 1) begin
                            acc_a = acc_a + rnd(e * prev, SH_A);
                            if (acc_a < 0) acc_a = 0;
                            else if (acc_a > (A_MAX <<< G) + (longint'(1) <<< G) - 1)
                                acc_a = (A_MAX <<< G) + (longint'(1) <<< G) - 1;
                            if (nt == 3) begin
                                acc_b = acc_b + rnd(e * prev2, SH_A);
                                if (acc_b < (B_LO <<< G)) acc_b = B_LO <<< G;
                                else if (acc_b > (B_HI <<< G) + (longint'(1) <<< G) - 1)
                                    acc_b = (B_HI <<< G) + (longint'(1) <<< G) - 1;
                            end
                        end
                    end
                    if (LMS_F == 1)
                        for (int i = 0; i < NF; i++) begin
                            j = k - i;
                            if (j >= 0) begin
                                wacc_f[i] = clip(wacc_f[i] - rnd(e * xin[j], SH_F), WF_LO, WF_HI);
                                w_ffe[i] = wacc_f[i] >>> G;
                            end
                        end
                    if (LMS_D == 1)
                        for (int d = 0; d < ND; d++) begin
                            jj = s - nt - d;
                            if (jj >= 0) begin
                                wacc_d[d] = clip(wacc_d[d] + rnd(e * levels[xl[jj]], SH_D),
                                                 WD_LO, WD_HI);
                                w_dfe[d] = wacc_d[d] >>> G;
                            end
                        end
                end

                if (s >= 2) begin
                    if (PD_FFE == 1 && PR_MODE > 0) begin
                        x0 = r_out[s - 2]; x1 = r_out[s - 1]; x2 = r_out[s];
                    end else if (PD_FFE == 1) begin
                        x0 = v_out[s - 2]; x1 = v_out[s - 1]; x2 = v;
                    end else begin
                        x0 = xin[s - 2]; x1 = xin[s - 1]; x2 = xin[s];
                    end
                    a0 = (x0 > 0) ? 1 : -1;
                    a2 = (x2 > 0) ? 1 : -1;
                    pd_acc += x1 * (a2 - a0);
                    pd_n += 1;
                    if (pd_n >= LANES) begin
                        pd = -pd_acc - PD_OFF;
                        integ += ashift(pd, KI_SH);
                        c = ashift(pd, KP_SH) + integ;
                        if (CLAMP > 0) begin
                            if (c > CLAMP) c = CLAMP;
                            else if (c < -CLAMP) c = -CLAMP;
                        end
                        queue[qi] = c;
                        qi = (qi + 1) % (LAT + 1);
                        corr = queue[qi];
                        pd_acc = 0;
                        pd_n = 0;
                    end
                end
            end
            ph += corr >>> LANE_SHIFT;
            if (CAL_SKEW == 1 && k >= 2) begin
                a0 = (xin[k - 2] > 0) ? 1 : -1;
                a2 = (xin[k] > 0) ? 1 : -1;
                cd = xin[k - 1] * (a2 - a0);
                if (CAL_SH_S >= 0) cd = cd <<< CAL_SH_S;
                else cd = (cd + (longint'(1) <<< (-CAL_SH_S - 1))) >>> (-CAL_SH_S);
                cts[(k - 1) % LANES] += cd;
                ctsum += cd;
            end
        end
        ab[0] = acc_a >>> G;
        ab[1] = acc_b >>> G;
    endtask
endmodule
