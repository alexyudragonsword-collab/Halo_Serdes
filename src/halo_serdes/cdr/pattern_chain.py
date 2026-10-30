"""One step of the pattern-phase vote chain, as a fused loop (numba when available).

The statistical engine's pattern-phase factor (``engine/stat_pattern_phase.py``
``_pattern_phase_factor``) runs the bang-bang loop's Markov chain over
(pattern class, phase, pairing state) to its fixed point. Its numpy step
there defines the arithmetic -- about thirty passes over the mass arrays a
symbol, three quarters of the factor's time. :func:`chain_step_py` is the
same step as one pass, in the same float32 operations and the same order
of additions into every element, so the jitted copy gives the numpy
step's arrays bit for bit (``tests/test_pattern_phase.py``). Without numba
(``HALO_NO_JIT=1``, the phone) ``chain_step`` is None and the engine keeps
its numpy step: as a pure-Python loop this one would take minutes.

Layout (see the engine's comments for the meaning): ``st`` / ``out`` are
(n_state, n_cls, n_u) float32, the two pairing states' masses and, with the
integral path (n_state 4), the same masses times the integrator's mean.
A class index has h_1 .. h_n_hist at bits 0 .. n_hist-1 and the next
symbol at bit n_hist. ``p_stay`` / ``p_late`` / ``p_early`` are
(2, n_cls, n_u): per value of the previous data sample's raw sign.
"""

from __future__ import annotations

import os

import numpy as np


def chain_step_py(st, out, p_stay, p_late, p_early, n_hist, n_mv, ki, second_order, dus):
    """``out`` = the chain one symbol on from ``st`` (``out`` is overwritten)."""
    n_state, n_cls, n_u = st.shape
    half = n_cls // 4
    f32 = np.float32
    ki32 = f32(ki)
    p5 = f32(0.5)
    out[:, :, :] = f32(0.0)
    n_j = 2 if n_state == 4 else 1
    for b in range(2):
        for i_j in range(n_j):
            j = b + 2 * i_j
            if j < 2:
                k_same, k_diff = b, 1 - b
            else:
                k_same, k_diff = b + 2, 3 - b
            for c in range(n_cls):
                # the two predecessors (h_n_hist either way); where h'_1 is
                # set the history is complemented, the low bits read backwards
                r = (c >> 1) & (half - 1)
                if (c & 1) == 0:
                    c1 = r
                else:
                    c1 = 2 * half + (half - 1 - r)
                c2 = c1 + half
                same = (c & 1) == ((c >> 1) & 1)
                ks = k_same if same else k_diff        # staying
                kv = k_diff if same else k_same        # a vote
                # one pass: an element of ``out`` takes the late vote from
                # u - n_mv before the early one from u + n_mv, as the numpy
                # step's separate passes add them
                for u in range(n_u):
                    a = (st[j, c1, u] + st[j, c2, u]) * p5
                    out[ks, c, u] += a * p_stay[b, c, u]
                    late = a * p_late[b, c, u]
                    early = a * p_early[b, c, u]
                    if j >= 2:
                        a0 = (st[b, c1, u] + st[b, c2, u]) * p5
                        late += (a0 * p_late[b, c, u]) * ki32
                        early -= (a0 * p_early[b, c, u]) * ki32
                    if u < n_u - n_mv:
                        out[kv, c, u + n_mv] += late
                    if u >= n_mv:
                        out[kv, c, u - n_mv] += early
    if n_state != 4:
        return
    # the integrator's push, at its mean given the phase
    m0 = np.zeros(n_u, dtype=np.float32)
    m1 = np.zeros(n_u, dtype=np.float32)
    m2 = np.zeros(n_u, dtype=np.float32)
    m3 = np.zeros(n_u, dtype=np.float32)
    for c in range(n_cls):
        for u in range(n_u):
            m0[u] += out[0, c, u]
            m1[u] += out[1, c, u]
            m2[u] += out[2, c, u]
            m3[u] += out[3, c, u]
    d32 = f32(dus)
    f_up = np.zeros(n_u, dtype=np.float32)
    f_dn = np.zeros(n_u, dtype=np.float32)
    for u in range(n_u):
        tot = m0[u] + m1[u]
        if tot < f32(1e-30):
            tot = f32(1e-30)
        im = (m2[u] + m3[u]) / tot
        fr = abs(im) / d32
        if fr > f32(1.0):
            fr = f32(1.0)
        if im > f32(0.0):
            f_up[u] = fr
        elif im < f32(0.0):
            f_dn[u] = fr
    f_up[n_u - 1] = f32(0.0)
    f_dn[0] = f32(0.0)
    g_up = np.zeros(n_u, dtype=np.float32)
    g_dn = np.zeros(n_u, dtype=np.float32)
    if second_order:
        for u in range(n_u):
            g_up[u] = p5 * f_up[u] * (f32(1.0) - f_up[u])
            g_dn[u] = p5 * f_dn[u] * (f32(1.0) - f_dn[u])
    a_up = f_up - g_up
    a_dn = f_dn - g_dn
    up_lo, up_hi = 0, 0
    for u in range(n_u):
        if f_up[u] > f32(0.0):
            if up_hi == 0:
                up_lo = u
            up_hi = u + 1
    dn_lo, dn_hi = 1, 1
    first = True
    for u in range(n_u):
        if f_dn[u] > f32(0.0):
            if first:
                dn_lo = u
                first = False
            dn_hi = u + 1
    fu = np.zeros(n_u, dtype=np.float32)
    fd = np.zeros(n_u, dtype=np.float32)
    for k in range(4):
        for c in range(n_cls):
            # both fluxes from the mass before either moves
            for u in range(up_lo, up_hi):
                fu[u] = out[k, c, u] * a_up[u]
                if second_order:
                    fu[u] += out[k, c, u + 1] * g_up[u]
            for u in range(dn_lo, dn_hi):
                fd[u] = out[k, c, u] * a_dn[u]
                if second_order:
                    fd[u] += out[k, c, u - 1] * g_dn[u]
            for u in range(up_lo, up_hi):
                out[k, c, u] -= fu[u]
            for u in range(dn_lo, dn_hi):
                out[k, c, u] -= fd[u]
            for u in range(up_lo, up_hi):
                out[k, c, u + 1] += fu[u]
            for u in range(dn_lo, dn_hi):
                out[k, c, u - 1] += fd[u]


#: the jitted step, or None (no numba, or HALO_NO_JIT=1): the engine then
#: takes its numpy step
chain_step = None
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        chain_step = numba.njit(cache=True)(chain_step_py)
    except ImportError:
        pass
