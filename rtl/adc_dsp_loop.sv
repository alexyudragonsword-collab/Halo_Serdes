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
//   slice nearest level, first minimum on ties
//   MM PD on symbol s-1: x1 * (sign x2 - sign x0), on FFE outputs (PD_FFE) or
//         input words; summed over LANES symbols, then
//         pd = -acc - PD_OFF; integ += pd <<< KI_SH; c = (pd <<< KP_SH) + integ
//         (a negative shift is >>> by its magnitude); clamp; LAT blocks of
//         latency through a queue
//   phase register += c >>> LANE_SHIFT every symbol; PI code = ph >>> PI_SH
//
// Python's >> on a negative int floors; SV >>> on a signed operand does too.
// Memories are module-internal, as in ffe_dfe_datapath.sv (Icarus' unpacked
// array ports are weak); the testbench pokes them, calls run() and peeks the
// results hierarchically. run() is a task rather than an always_comb block:
// the loop carries state from symbol to symbol, and Icarus 12 asserts on a
// combinational block of this size (vvp_fun_anyedge_sa).

module adc_dsp_loop;
    `include "loop_dims.svh"

    longint xin    [N];
    longint w_ffe  [NF];
    longint w_dfe  [ND];
    longint levels [NL];
    longint pi_out [N];
    longint dec    [N];
    longint v_out  [N];

    localparam longint LIM_HI = (longint'(1) << (OUT_BITS - 1)) - 1;
    localparam longint LIM_LO = -(longint'(1) << (OUT_BITS - 1));

    function automatic longint ashift(input longint v, input longint sh);
        if (sh >= 0) return v <<< sh;
        return v >>> (-sh);
    endfunction

    task automatic run();
        longint acc, fb, v, dd, bd, x0, x1, x2, a0, a2, pd, c;
        longint ph, integ, corr, pd_acc, pd_n, qi;
        longint queue [LAT + 1];
        int s, j, jj, best;
        ph = 0; integ = 0; corr = 0; pd_acc = 0; pd_n = 0; qi = 0;
        for (int q = 0; q <= LAT; q++) queue[q] = 0;
        for (int k = 0; k < N; k++) begin
            pi_out[k] = ph >>> PI_SH;
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
                    jj = s - 1 - d;
                    if (jj >= 0) fb += w_dfe[d] * levels[dec[jj]];
                end
                if (DO_ROUND == 1 && DFE_SHIFT > 0) fb += longint'(1) << (DFE_SHIFT - 1);
                fb = fb >>> DFE_SHIFT;
                v = acc - fb;
                if (v > LIM_HI) v = LIM_HI;
                else if (v < LIM_LO) v = LIM_LO;
                v_out[s] = v;
                best = 0;
                bd = (v > levels[0]) ? (v - levels[0]) : (levels[0] - v);
                for (int m = 1; m < NL; m++) begin
                    dd = (v > levels[m]) ? (v - levels[m]) : (levels[m] - v);
                    if (dd < bd) begin bd = dd; best = m; end
                end
                dec[s] = best;

                if (s >= 2) begin
                    if (PD_FFE == 1) begin
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
        end
    endtask
endmodule
