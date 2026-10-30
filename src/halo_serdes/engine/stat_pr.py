"""The statistical engine's partial-response detector: error events and decisions.

The union bound over a sequence detector's error events on a PR target
(``pr_error_events``, with coloured noise and the overlap weights the time
engine shows) and the per-symbol decisions it feeds back
(``pr_symbol_decisions``). Part of ``engine.statistical`` (split out
2026-10-30; the names are importable from there as before).
"""

from __future__ import annotations

import numpy as np

from ..core.mapping import GRAY_BIT_DISTANCE


def _pr_patterns(families: tuple[str, ...], max_len: int) -> list[np.ndarray]:
    """The error patterns ``pr_error_events`` runs over, in its order: per
    family, lengths 1..max_len (a constant run of 1 is the alternating one)."""
    return [np.where(np.arange(n) % 2 == 0, 1.0, -1.0) if fam == "alternating" else np.ones(n)
            for fam in families for n in range(1, max_len + 1)
            if not (fam == "constant" and n == 1)]


def pr_event_differences(cursors: np.ndarray, families: tuple[str, ...] = ("alternating",),
                         max_len: int = 12, state_memory: int | None = None) -> list[np.ndarray]:
    """The difference each of ``pr_error_events``' events makes to the
    detector's expected samples (pattern * cursors, cut where a reduced-state
    trellis merges), in its order and in units of the main cursor."""
    h = np.asarray(cursors, dtype=float)
    out = []
    for e in _pr_patterns(families, max_len):
        dd = np.convolve(e, h)
        if state_memory is not None:
            dd = dd[:e.size + state_memory]
        out.append(dd)
    return out


def pr_error_events(cursors: np.ndarray, noise_acf: np.ndarray, n_levels: int,
                    precoded: bool, max_len: int = 12,
                    snr: float | None = None,
                    families: tuple[str, ...] = ("alternating",),
                    n_samples: int = 1000, seed: int = 0,
                    state_memory: int | None = None) -> list[tuple[float, float]]:
    """(distance gain, weight) per error event of a sequence detector on a
    partial-response target, for a union bound.

    The minimum distance alone is not enough here, for two reasons the time
    engine shows. The FFE that shapes the pulse into [1, alpha] colours the
    noise (rho1 = -0.25, rho2 = -0.31 for [1, 1] on example 18's channel),
    and the Viterbi metric is Euclidean, so an event d is missed with
    Q(|d|^2 / (2 sigma sqrt(d' R d))), not Q(|d| / 2 sigma). And with alpha
    near 1 every alternating event (+1, -1, +1, ...) of any length L is near
    the minimum distance, so they add up. Events are one level step per
    symbol, alternating in sign; the data supports one of length L with
    probability ((M - 1) / M)^(L - 1) relative to a single error (which the
    per-level threshold sum already weighs). Each costs L symbol errors, or,
    precoded, as many as the mod-M sum of neighbouring errors leaves (2 for
    any alternating event).

    The gain is relative to the memoryless slicer's half-distance; with white
    noise and L = 1 it is |d|, the plain MLSD path's sqrt(d_min^2).

    Without ``snr`` the weight is that support times the errors: a plain sum
    over the events, which counts one detector error once for every event
    whose metric it also beats. Where the distances of many lengths sit close
    together (behind a transmit 1 + 0.5D, rho1 = -0.67, lengths 2-6 within
    10 % of each other) that is 2.5-2.9x pessimistic. So with ``snr`` (the
    half-distance over the noise sigma) each weight is the event's share of
    what the detector actually decides when its metric condition holds: the
    competing events are all of them -- every length, both families, and
    every start that overlaps (an error of length 4 contains the opposite-
    sign one of length 3 that starts a symbol later, which the per-start
    sum would count again) -- and the detector takes the one that beats the
    correct path by the most. Sampling the noise given event j's condition
    (and the symbols given its moves are possible), its weight is support_j
    times the mean of errs(chosen) / (number of events whose condition holds
    and whose moves the symbols allow). Summed against each event's own
    probability, sum_j P(A_j) w_j is then the expected number of decided
    symbol errors per start, the union and not the sum: the at-least-one-event
    estimator of Owen, Maximov and Chertkov (2019), whose relative error stays
    bounded however rare the events. The ISI around the events is left out
    of the weights, as the per-level sum adds it to each P(A_j), projected on
    the event (``pr_event_differences``).

    The chained pairwise overlap this replaced (each L less its intersection
    with L - 1) read 0.78-2.09x the time engine on 16 PR links, up to 2.09x
    with a negative third cursor; this 0.67-1.55x
    (``cairn/DSP发端与PR.md`` §14), and 0.58-1.08x once each event's ISI
    was projected on it (§19).

    ``families`` adds ``"constant"`` (+1, +1, ...): with a negative
    controlled cursor after the first (b or c < 0) a run of same-sign errors is
    pulled close by it, where for a non-negative target it lies far.
    ``n_samples`` per event and ``seed`` fix the sampling (deterministic).

    ``state_memory`` K: a reduced-state (DFSE) trellis compares two paths only
    until they merge, K symbols after the event's last error, so an event of
    n symbols counts the first n + K samples of its difference
    (``dsp.mlsd.mlse_min_distance_sq``); the survivors' feedback errors are
    not in it.

    The symbols are taken as equiprobable here, PAM6's corner32 map included
    (its outer levels are sent 1/8 of the time against 3/16): the support
    weights of long events are an approximation for it.
    """
    from scipy.special import ndtr, ndtri

    h = np.asarray(cursors, dtype=float)
    rho = np.asarray(noise_acf, dtype=float) / float(noise_acf[0])
    pats = _pr_patterns(families, max_len)
    if precoded:
        errs_p = np.array([np.count_nonzero(np.convolve(e, [1.0, 1.0]).round().astype(int) % n_levels)
                           for e in pats], dtype=float)
    else:
        errs_p = np.array([e.size for e in pats], dtype=float)
    supp_p = np.array([((n_levels - 1) / n_levels) ** (e.size - 1) for e in pats])
    # every pattern at every start that can overlap one starting at 0 (both
    # signs; at 0 the per-level sum covers the sign), in one noise window
    nb = max_len - 1 if snr is not None else 0
    size = 2 * nb + max_len + h.size - 1
    lag = np.abs(np.arange(size)[:, None] - np.arange(size)[None, :])
    r_mat = np.where(lag < rho.size, rho[np.minimum(lag, rho.size - 1)], 0.0)
    start, sign, pat = [], [], []
    for k in range(len(pats)):
        for st in range(-nb, nb + 1):
            for sg in (1.0, -1.0):
                if st == 0 and sg < 0.0:
                    continue
                start.append(st)
                sign.append(sg)
                pat.append(k)
    start, sign, pat = np.array(start), np.array(sign), np.array(pat)
    d = np.zeros((pat.size, size))
    for i in range(pat.size):
        dd = np.convolve(sign[i] * pats[pat[i]], h)
        if state_memory is not None:
            dd = dd[:pats[pat[i]].size + state_memory]
        d[i, nb + start[i]: nb + start[i] + dd.size] = dd
    s = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", d, r_mat, d), 1e-300))
    g = np.einsum("ij,ij->i", d, d) / s
    base = np.flatnonzero(start == 0)                    # one per pattern, in order
    if snr is None:
        return [(float(g[i]), float(supp_p[pat[i]] * errs_p[pat[i]])) for i in base]

    # whitened unit directions: event i's statistic is a_i . w, w ~ N(0, I)
    ev, evec = np.linalg.eigh(r_mat)
    a = (d @ (evec * np.sqrt(np.clip(ev, 0.0, None)))) / s[:, None]
    t = g * snr                                          # its condition: a_i . w > t_i
    errs = errs_p[pat]
    n_sym = 2 * nb + max_len                             # symbols the events can move
    up = np.zeros((pat.size, n_sym))
    dn = np.zeros((pat.size, n_sym))
    for i in range(pat.size):
        e = sign[i] * pats[pat[i]]
        cols = nb + start[i] + np.arange(e.size)
        up[i, cols[e > 0]] = 1.0
        dn[i, cols[e < 0]] = 1.0
    rng = np.random.default_rng(seed)
    out = []
    for j in base:
        # the events that can hold together with j: given j's statistic at
        # its threshold, the rest are left out once below 1e-6
        rho_j = a @ a[j]
        q = np.sqrt(np.maximum(1.0 - rho_j ** 2, 1e-12))
        keep = ndtr(-(t - rho_j * (t[j] + 1.0 / max(t[j], 1.0))) / q) > 1e-6
        keep[j] = True
        idx = np.flatnonzero(keep)
        jj = int(np.flatnonzero(idx == j)[0])
        # the noise given j's condition: its statistic from the normal tail
        # beyond t_j, the rest of w as it is
        z = -ndtri(np.maximum(rng.random(n_samples) * ndtr(-t[j]), 1e-300))
        w = rng.standard_normal((n_samples, a.shape[1]))
        w += np.outer(z - w @ a[j], a[j])
        stat = w @ a[idx].T
        # symbols uniform, given the moves j makes are possible
        lo = np.where(dn[j] > 0, 1, 0)
        hi = np.where(up[j] > 0, n_levels - 1, n_levels)
        sym = lo + (rng.random((n_samples, n_sym)) * (hi - lo)).astype(int)
        blocked = ((sym >= n_levels - 1).astype(float) @ up[idx].T
                   + (sym <= 0).astype(float) @ dn[idx].T)
        hold = (stat > t[idx]) & (blocked == 0)
        hold[:, jj] = True
        # the detector's choice: the path that beats the correct one by the
        # most, metric difference 2 sigma_d s_i (stat_i - t_i)
        chosen = np.argmax(np.where(hold, s[idx] * (stat - t[idx]), -np.inf), axis=1)
        share = errs[idx][chosen] / hold.sum(axis=1)
        out.append((float(g[j]), float(supp_p[pat[j]] * share.mean())))
    return out


def pr_symbol_decisions(pdf_lv: list, v_centers: np.ndarray, lv: np.ndarray,
                        head, composite: bool, precoded: bool,
                        bit_dist: np.ndarray | None = None,
                        level_probs: np.ndarray | None = None,
                        pair_cond: np.ndarray | None = None,
                        pair_words: np.ndarray | None = None) -> tuple[float, float]:
    """(SER, bit errors per symbol) of the per-symbol decisions a receive
    partial-response target leaves the LMS and the CDR, or the detector
    itself when there is no sequence detector.

    Those decisions take the controlled cursors off with the receiver's own
    previous decisions (sample - a x_{k-1} - b x_{k-2}), so they are a DFE
    on those cursors, errors and all: one wrong decision leaves a times its
    error on the next sample, and at a = 1 that is a whole level step. Taken
    as an ideal tap the time engine's figure was 2.7x (a = 0.5) to 4.1x
    (a = 1) above this engine's. Here the errors are a Markov chain: the
    state is the last one or two decision errors (in level steps), the next
    error's distribution is the per-level tail of the ISI + noise PDF shifted
    by what the state leaves, averaged over a uniform symbol, and the SER is
    its stationary rate.

    ``composite`` is precoded 1 + D at a = 1 as the kernel runs it: the
    sample is sliced to the 2N - 1 levels x_k + x_{k-1} and the user symbol
    is that mod N, so an error does not propagate; only the composite levels'
    unequal occupancy (inner ones are hit more often) differs from a plain
    slicer. ``precoded`` without ``composite`` scores user symbols
    (x_k + x_{k-1}) mod N off a propagating chain.

    ``pdf_lv`` is the ISI + noise PDF per transmitted level (on
    ``v_centers``) and ``lv`` the levels at the slicer; the levels are taken
    as evenly spaced. Bits per wrong symbol follow the checker: the Hamming
    distance of the two symbols' Gray codes (``GRAY_BIT_DISTANCE``), or
    ``bit_dist`` (``core.mapping.bit_distance_matrix``); the symbols are
    equiprobable unless ``level_probs`` says otherwise. With ``pair_cond``
    and ``pair_words`` (PAM6's corner32 pairs, not precoded) a pair is
    scored once, when its second symbol is decided: the first symbol's
    error is the chain's state, its level the second's partner
    (``neighbour_conditional``, the levels the state allows), and the bits
    are the two words' (``pair_word_bits``) -- half a pair per symbol.
    """
    n = lv.size
    bd = GRAY_BIT_DISTANCE if bit_dist is None else np.asarray(bit_dist, dtype=np.float64)
    pw = np.full(n, 1.0 / n) if level_probs is None else np.asarray(level_probs, dtype=np.float64)
    order = np.argsort(lv)
    ls = lv[order]
    sym = order                                  # sorted index -> symbol index
    step = float(np.mean(np.diff(ls))) if n > 1 else 1.0
    cdf = [np.cumsum(pdf_lv[int(o)]) for o in order]

    def decide(j: int, levels: np.ndarray, center: float) -> np.ndarray:
        """P(decision = k) per slicer level, the sample at ``center`` with
        sorted symbol j's ISI + noise around it (same bins as the slicer
        tails in run_statistical)."""
        edges = 0.5 * (levels[1:] + levels[:-1]) - center
        i = np.searchsorted(v_centers, edges) - 1
        f = np.where(i >= 0, cdf[j][np.clip(i, 0, v_centers.size - 1)], 0.0)
        return np.diff(np.concatenate([[0.0], f, [1.0]]))

    def bits(u: int, v: int) -> float:
        return float(bd[int(u), int(v)])

    if composite:
        comp = 2 * ls[0] + step * np.arange(2 * n - 1)
        # composite index in symbol order (an inverting slicer reverses it)
        c_sym = np.arange(2 * n - 1) if sym[0] == 0 else np.arange(2 * n - 2, -1, -1)
        ser = nbe = 0.0
        for j in range(n):                       # current symbol, sorted
            for i in range(n):                   # previous one
                c = j + i
                w_ji = float(pw[sym[j]] * pw[sym[i]])
                p = decide(j, comp, comp[c])
                ser += (1.0 - p[c]) * w_ji
                for k in range(2 * n - 1):
                    if k != c:
                        nbe += p[k] * bits(c_sym[c] % n, c_sym[k] % n) * w_ji
        return float(ser), float(nbe)

    h = np.asarray(head, dtype=float)[1:]
    depth = h.size
    vals = np.arange(-(n - 1), n)                # decision error = sent - decided index
    states = [()] if depth == 0 else [tuple(int(v) for v in t) for t in np.array(
        np.meshgrid(*([vals] * depth), indexing="ij")).reshape(depth, -1).T]
    index = {st: k for k, st in enumerate(states)}
    trans = np.zeros((len(states), len(states)))
    p_err = np.zeros(len(states))
    b_err = np.zeros(len(states))
    for k, st in enumerate(states):
        offset = step * float(np.dot(h, st)) if depth else 0.0
        dist = np.zeros(vals.size)               # P(error = e) at this state
        pairwise = pair_words is not None and pair_cond is not None and not precoded
        st0 = int(st[0]) if depth else 0
        for j in range(n):
            p = decide(j, ls, ls[j] + offset)
            w_j = float(pw[sym[j]])
            if pairwise:
                # the symbol before (sorted m, decided m - st0) as this one's partner
                ms = [m for m in range(n) if 0 <= m - st0 < n]
                w_m = np.array([pair_cond[sym[j], sym[m]] for m in ms])
                w_m = w_m / w_m.sum() if w_m.sum() > 0 else w_m
            for d in range(n):
                dist[j - d + n - 1] += p[d] * w_j
                if precoded or p[d] == 0.0:
                    continue
                if pairwise:
                    for m, wm in zip(ms, w_m):
                        b = float(pair_words[sym[m], sym[j], sym[m - st0], sym[d]])
                        if b and wm:
                            b_err[k] += 0.5 * p[d] * w_j * wm * b
                elif j != d:
                    b_err[k] += p[d] * bits(sym[j], sym[d]) * w_j
        for e, pe in zip(vals, dist):
            nxt = ((int(e),) + st[:-1]) if depth else ()
            trans[k, index[nxt]] += pe
            if precoded:
                # the user symbol (x_k + x_{k-1}) mod N is wrong unless the
                # two errors cancel mod N; bits over a uniform user symbol
                delta = (int(e) + (st[0] if depth else 0)) % n
                if delta:
                    p_err[k] += pe
                    b_err[k] += pe * float(sum(pw[u] * bits(u, (u - delta) % n) for u in range(n)))
            elif e:
                p_err[k] += pe
    # stationary distribution of the error chain
    a_mat = trans.T - np.eye(len(states))
    a_mat[-1] = 1.0
    rhs = np.zeros(len(states))
    rhs[-1] = 1.0
    pi = np.clip(np.linalg.solve(a_mat, rhs), 0.0, None)
    pi = pi / pi.sum()
    return float(pi @ p_err), float(pi @ b_err)
