"""Streaming waveform front end for the time engine (``sim.stream``).

The default engine builds the whole received waveform before the receiver
loop starts: Tx, channel, noise, each a full-length array, ~0.9 GB at 10^6
symbols and OSR 32. Here the same chain is produced block by block, and the
receiver kernels read a window that slides along it, so memory no longer
grows with the run length (only the per-symbol arrays do, 8 bytes each).

What is the same as the default engine, value for value: the symbol stage,
the edge offsets (drawn from the link generator in the same order) and the
jittered zero-order hold (``zoh_window`` is ``jittered_zoh`` restricted to a
range of samples), the driver curve, and the receiver kernels themselves.

What is not, because the default engine's operators are circular FFTs over
the whole waveform, where every output sample depends on every input one:

* the Tx driver pole (``tx.bw``) is the FIR ``TxPipeline.driver_response``
  -- the same sampled impulse the statistical engine and the receivers'
  pulse analysis already use -- instead of the whole-waveform rFFT;
* the receiver noise is the same per-sample white draws, band-limited to
  [0, baud] by a Kaiser-windowed sinc normalised to unit energy instead of a
  brick wall over the whole draw.

Every other random draw (comparator offsets, Rx clock, ADC mismatch and
noise, the photodiode noise of an optical topology, the crosstalk
aggressors' data) is the default engine's too, so the two runs are the same
link instance and differ only by those two filters. The optical stages and
the aggressors' couplings are linear convolutions in the default engine as
well (``fft_filter``, ``np.convolve``); as FIRs they differ by rounding.

Both are the same operators statistically, so a streamed run agrees with
the default one within its error counts (``tests/test_stream.py`` pins it),
not bit for bit. Within streaming the result does not depend on how the run
is chunked: the FIR stages work in fixed blocks counted from sample 0, and
the kernels read absolute positions whatever window they are handed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

#: half-length of the noise band-limiting FIR [UI]; with the Kaiser window
#: (beta 8) the transition band is ~0.08 baud wide and the stopband ~-80 dB
NOISE_FIR_HALF_UI = 32
NOISE_FIR_BETA = 8.0
#: samples kept behind the receiver's next sampling position when the window
#: slides: the interpolator, the half-UI edge sample, ADC lane skews and a
#: sampling clock that wanders back all fit with room to spare (a shortfall
#: is an error, never a silent wrong read)
WINDOW_BACK_UI = 8


def _zoh_window_py(v: np.ndarray, b: np.ndarray, osr: int, a: int, m: int, n: int,
                   k_lo: int, k_hi: int) -> np.ndarray:
    """Samples ``[a, a + m)`` of ``tx.jitter.jittered_zoh`` (zeros past ``n``).

    The same fill in the same order, restricted to the symbols ``k_lo..k_hi``
    whose writes can reach the range; later symbols overwrite earlier ones
    there exactly as in the full fill, so each sample gets the same value.
    """
    y = np.zeros(m)
    n_sym = v.size
    for i in range(m):
        t = a + i
        if t < n:
            y[i] = v[t // osr]
    for k in range(k_lo, k_hi + 1):
        cur_start = b[k - 1]
        cur_end = b[k]
        va = v[k - 1]
        i0 = max(int(np.ceil(cur_start)), 0)
        i1 = min(int(np.floor(cur_end)), n)
        if i1 > i0:
            lo = max(i0, a)
            hi = min(i1, a + m)
            for t in range(lo, hi):
                y[t - a] = va
        if k < n_sym:
            j = int(np.floor(cur_end))
            if 0 <= j < n and a <= j < a + m:
                frac = cur_end - j
                y[j - a] = va * frac + v[k] * (1.0 - frac)
    return y


_zoh_window = _zoh_window_py
if os.environ.get("HALO_NO_JIT") != "1":
    try:
        import numba

        _zoh_window = numba.njit(cache=True)(_zoh_window_py)
    except ImportError:
        pass


class ZohSource:
    """The jittered zero-order hold (then the driver curve), read in order."""

    def __init__(self, v_sym: np.ndarray, jitter_s: np.ndarray, osr: int, ui: float,
                 driver_nl=None):
        dt = ui / osr
        self.v = np.asarray(v_sym, dtype=np.float64)
        # the boundaries exactly as jittered_zoh computes them
        self.b = (np.arange(self.v.size + 1) * ui + jitter_s) / dt
        self.pmax = np.maximum.accumulate(self.b)
        self.smin = np.minimum.accumulate(self.b[::-1])[::-1]
        self.osr = osr
        self.n = self.v.size * osr
        self.nl = driver_nl
        self.pos = 0

    def read(self, m: int) -> np.ndarray:
        a = self.pos
        k_lo = max(int(np.searchsorted(self.pmax, a, side="left")), 1)
        k_hi = min(int(np.searchsorted(self.smin, a + m, side="right")), self.v.size)
        y = _zoh_window(self.v, self.b, self.osr, a, m, self.n, k_lo, k_hi)
        self.pos += m
        return y if self.nl is None else self.nl(y)


class NoiseSource:
    """Unit-variance white noise, one draw per sample, in order."""

    def __init__(self, rng: np.random.Generator):
        self.rng = rng

    def read(self, m: int) -> np.ndarray:
        return self.rng.normal(size=m)


class Fir:
    """``y[t] = sum_j h[j] x[t - j]``, overlap-save in fixed blocks.

    The input is always pulled in blocks of ``block`` samples counted from
    sample 0, whatever sizes the output is read in, so the result does not
    depend on the reader -- the property the streamed engine's chunk
    independence rests on.
    """

    def __init__(self, src, h: np.ndarray, block: int):
        self.src = src
        h = np.asarray(h, dtype=np.float64)
        self.nh = h.size
        self.block = block
        self.nfft = int(2 ** np.ceil(np.log2(block + self.nh - 1)))
        self.H = np.fft.rfft(h, self.nfft)
        self.hist = np.zeros(self.nh - 1)
        self.buf = np.zeros(0)

    def _block(self) -> None:
        x = np.concatenate([self.hist, self.src.read(self.block)])
        y = np.fft.irfft(np.fft.rfft(x, self.nfft) * self.H, self.nfft)
        self.buf = np.concatenate([self.buf, y[self.nh - 1: self.nh - 1 + self.block]])
        self.hist = x[x.size - (self.nh - 1):] if self.nh > 1 else self.hist

    def read(self, m: int) -> np.ndarray:
        while self.buf.size < m:
            self._block()
        out, self.buf = self.buf[:m], self.buf[m:]
        return out


class Lead:
    """Drops the first ``lead`` samples of a stream: a non-causal FIR with
    ``lead`` samples before t = 0, applied as a causal one, lines up."""

    def __init__(self, src, lead: int):
        self.src = src
        self.lead = int(lead)

    def read(self, m: int) -> np.ndarray:
        if self.lead:
            self.src.read(self.lead)
            self.lead = 0
        return self.src.read(m)


class Sum:
    def __init__(self, a, b, scale_b: float = 1.0):
        self.a, self.b, self.scale_b = a, b, scale_b

    def read(self, m: int) -> np.ndarray:
        return self.a.read(m) + self.scale_b * self.b.read(m)


class HoldSource:
    """``core.sampler.hold`` of per-UI values, read in order (zeros past the end)."""

    def __init__(self, v: np.ndarray, osr: int):
        self.v = np.asarray(v, dtype=np.float64)
        self.osr = osr
        self.pos = 0

    def read(self, m: int) -> np.ndarray:
        k = (self.pos + np.arange(m)) // self.osr
        self.pos += m
        y = np.zeros(m)
        ok = k < self.v.size
        y[ok] = self.v[k[ok]]
        return y


class Map:
    """A memoryless stage, sample by sample (the E/O curve)."""

    def __init__(self, src, f):
        self.src, self.f = src, f

    def read(self, m: int) -> np.ndarray:
        return self.f(self.src.read(m))


class OpticalNoiseStage:
    """``OpticalNoise.inject`` in order: sample t takes the t-th draw of
    ``rng`` and a sigma set by its own power, however the stream is read."""

    def __init__(self, src, noise, rng: np.random.Generator):
        self.src, self.noise, self.rng = src, noise, rng

    def read(self, m: int) -> np.ndarray:
        return self.noise.inject(self.src.read(m), self.rng)


class Tap:
    """Passes the stream through and shows its first ``n`` samples to
    ``sink.feed`` (the stages past the end are the FIR blocks' padding)."""

    def __init__(self, src, sink, n: int):
        self.src, self.sink, self.left = src, sink, int(n)

    def read(self, m: int) -> np.ndarray:
        y = self.src.read(m)
        if self.left > 0:
            self.sink.feed(y[: self.left])
            self.left -= min(self.left, y.size)
        return y


def skip_normals(rng: np.random.Generator, n: int, block: int = 1 << 20) -> None:
    """Advance ``rng`` past ``rng.normal(size=n)`` without holding it
    (blockwise draws read the generator exactly as one draw does)."""
    while n > 0:
        m = min(n, block)
        rng.normal(size=m)
        n -= m


def noise_fir(osr: int) -> np.ndarray:
    """Low-pass to [0, baud] on the sample grid, unit energy (so unit-variance
    white noise through it stays unit variance)."""
    half = NOISE_FIR_HALF_UI * osr
    t = np.arange(-half, half + 1)
    h = np.sinc(2.0 * t / osr) * np.kaiser(t.size, NOISE_FIR_BETA)
    return h / np.sqrt(np.sum(h * h))


def block_size(*taps: int) -> int:
    """Fixed FIR block: 2^16 samples, or the next power of two above the
    longest response (the block length is part of the result's definition,
    so it depends on the configuration only)."""
    nh = max(taps) if taps else 1
    return max(1 << 16, int(2 ** np.ceil(np.log2(max(nh, 2)))))


class StreamRx:
    """The received waveform as a stream, plus what the engines need of it."""

    def __init__(self, source, n_total: int, block: int):
        self.source = source
        self.n_total = int(n_total)
        self.block = block
        self.head = None        # first samples, kept for the eye diagram
        self.jitter = None      # (collectors, the Tx and channel-only streams)

    def keep_head(self, n: int) -> None:
        self.head_len = min(int(n), self.n_total)
        self.head = np.zeros(0)

    @property
    def drain(self) -> bool:
        """Whether the whole stream has to pass, even past the receiver."""
        return self.jitter is not None

    def jitter_stages(self) -> dict:
        """The per-stage crossings ``collect_jitter`` decomposes, once the
        receiver stream has been drained: the receiver-node tap saw it pass;
        the Tx output and the channel-only waveform are streamed here."""
        col, chnl = self.jitter
        while chnl.left_to_read > 0:
            chnl.read(min(self.block, chnl.left_to_read))
        return col


class _Count:
    """Counts what is read through it (the end of a side stream)."""

    def __init__(self, src, n: int):
        self.src, self.left_to_read = src, int(n)

    def read(self, m: int) -> np.ndarray:
        self.left_to_read -= m
        return self.src.read(m)


@dataclass
class OpticalPath:
    """An optical topology as the time engine runs it (``_optical_pass``)."""

    stages: tuple            # (h1, h2) or (drive, optics, receiver)
    optical: object          # ChannelModel.optical: the curve, drive amplitude, noise
    rng: np.random.Generator  # the photodiode noise draws


def build_stream(cfg, tx_pipe, v_sym: np.ndarray, jitter_s: np.ndarray, h: np.ndarray,
                 noise_rng: np.random.Generator | None, optical: OpticalPath | None = None,
                 xtalk=(), jitter_h: np.ndarray | None = None) -> StreamRx:
    """Tx -> [driver curve] -> [driver pole] -> channel (+ CTLE, VGA), or the
    optical stages around their noise node -> [crosstalk] [+ noise].

    ``jitter_h``: the channel-only impulse for ``collect_jitter``; with it the
    stream also collects the crossings of the Tx output, the channel-only
    waveform and the receiver node before its noise (``jitter_stages``).
    """
    osr = cfg.osr
    n_total = v_sym.size * osr
    drv = tx_pipe.driver_response(osr)
    nhn = 2 * NOISE_FIR_HALF_UI * osr + 1
    lti = [h] if optical is None else list(optical.stages)
    block = block_size(*(x.size for x in lti), nhn, 0 if drv is None else drv[0].size,
                       *(a.coupling.size for a in xtalk))

    def tx():
        src = ZohSource(v_sym, jitter_s, osr, cfg.ui, tx_pipe.driver_nl)
        return src if drv is None else Lead(Fir(src, drv[0], block), drv[1])

    if optical is None:
        rx = Fir(tx(), h, block)
    else:
        opt = optical.optical
        if len(optical.stages) == 3:
            hd, ho, h2 = optical.stages
            pd = Fir(Map(Fir(tx(), hd, block),
                         lambda y: opt.curve.apply(y, opt.drive_amplitude)), ho, block)
        else:
            h1, h2 = optical.stages
            pd = Fir(tx(), h1, block)
        rx = Fir(OpticalNoiseStage(pd, opt.noise, optical.rng), h2, block)
    for agg in xtalk:
        # the default engine's aggressor draws, held and filtered as they go
        v_agg = agg.symbol_volts(n_total, osr, cfg.modulation)
        rx = Sum(rx, Fir(HoldSource(v_agg, osr), agg.coupling, block))
    col = None
    if jitter_h is not None:
        from ..analysis.jitter import CrossingCollector

        col = {k: CrossingCollector(cfg.dt) for k in ("tx", "chnl", "ctle")}
        # the receiver node before its noise, as the default engine captures it
        rx = Tap(rx, col["ctle"], n_total)
    if cfg.rx.noise_rms > 0:
        # centred like the brick wall, so noise sample t is mostly white draw t's
        noise = Lead(Fir(NoiseSource(noise_rng), noise_fir(osr), block), NOISE_FIR_HALF_UI * osr)
        rx = Sum(rx, noise, cfg.rx.noise_rms)
    out = StreamRx(rx, n_total, block)
    if col is not None:
        # a second Tx stream (it is deterministic) feeds the channel-only FIR;
        # with the same block its samples are the receiver chain's Tx samples
        chnl = Tap(Fir(Tap(tx(), col["tx"], n_total), jitter_h,
                       max(block, block_size(jitter_h.size))), col["chnl"], n_total)
        out.jitter = (col, _Count(chnl, n_total))
    return out


def drive_stream(run, rx: StreamRx, chunk: int, progress):
    """``timedomain._drive`` over a sliding window of the received stream.

    The window is extended a block at a time when the kernel asks for
    samples past its end, and keeps only ``WINDOW_BACK_UI`` UI behind the
    next sampling position. ``progress`` is called at the same symbol counts
    as the non-streamed driver.
    """
    osr = run.osr
    back = WINDOW_BACK_UI * osr + 4
    state = {"buf": np.zeros(0), "off": 0, "end": 0}

    def extend():
        buf, off = state["buf"], state["off"]
        nxt = rx.source.read(rx.block)[: rx.n_total - state["end"]]
        if rx.head is not None and rx.head.size < rx.head_len:
            rx.head = np.concatenate([rx.head, nxt[: rx.head_len - rx.head.size]])
        keep = max(off, min(int(np.floor(run.next_position())) - back, state["end"]))
        state["buf"] = np.concatenate([buf[keep - off:], nxt])
        state["off"] = keep
        state["end"] += nxt.size
        run.set_window(state["buf"], state["off"], rx.n_total)

    extend()
    step = max(int(chunk), 1)
    target = step
    while not run.finished:
        run.advance(target)
        if run.need_more:
            extend()
            continue
        if progress is not None:
            progress(run.done, run.n_symbols)
        target = run.done + step
    # the eye needs its head, and collect_jitter the whole stream, even when
    # the receiver stopped early; the window is not needed any more
    while state["end"] < rx.n_total and (
            rx.drain or (rx.head is not None and rx.head.size < rx.head_len)):
        nxt = rx.source.read(rx.block)[: rx.n_total - state["end"]]
        if rx.head is not None and rx.head.size < rx.head_len:
            rx.head = np.concatenate([rx.head, nxt[: rx.head_len - rx.head.size]])
        state["end"] += nxt.size
    return run.result()
